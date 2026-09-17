"""科研调研报告 Prompt（v0.18 Phase 2.9-C-D，Prompt r1）。

与单篇增强分析 Prompt（`enhanced_summary_prompt.py`）完全独立：
- 独立版本 `REPORT_PROMPT_VERSION = "r1"`（不混用 `ENHANCED_PROMPT_VERSION`）；
- 输入是论文集合（ReportContext），输出是六章节报告 JSON；
- 本模块不 import 单篇 prompt / EnhancedSummary / SummaryService。

强制规则（写入 system prompt）：

1. **SOURCE TRACE**：所有事实必须来自输入论文；每个章节必须填
   `source_papers`（[P#] 编号）；不知道的信息写「根据当前论文集合无法判断」；
   禁止用模型记忆补充论文内容。
2. **SOURCE OWNERSHIP**：禁止把单篇论文贡献写成整个领域事实；
   禁止把综述论文的创新点冒充原创贡献；禁止生成不存在的实验结果。
3. **USER PROFILE**：画像只用于"论文集合 × 用户方向"的关系分析，
   禁止修改 Evidence（论文内容）。

输出 JSON：

    {"title": "...",
     "sections": [{"title": "...", "content": "...", "source_papers": [1, 3]}]}

解析规则（`parse_report_sections`）：

- JSON 失败 → `ReportParseError`（由 ReportService 统一降级，Provider 不降级）；
- 缺 title / sections / 章节 title / content → `ReportParseError`；
- `source_papers` 为空、非列表、编号越界（超出 1..n）→ `ReportGroundingError`；
- `source_papers` 是 [P#] **编号数组**，解析后映射为论文 id（报告模型存 id）。
"""
from __future__ import annotations

import json
import re

from tju_info_retrieval.models.report_context import ReportContext
from tju_info_retrieval.models.research_report import (
    REPORT_SECTION_LABELS,
    ReportSection,
)

# 报告 Prompt 版本（进入缓存 key；prompt 实质变化时必须升级）
REPORT_PROMPT_VERSION = "r1"

# 六章节（与 C-B 模型的 REPORT_SECTION_KEYS 一一对应，展示标题取本阶段任务书措辞）
PROMPT_SECTION_TITLES = (
    "研究背景",            # background
    "领域发展现状",         # trend
    "关键技术路线",         # tech_route
    "主要挑战",             # challenge
    "未来研究方向",         # future
    "与用户研究方向关系",    # profile_link
)

_ABSTRACT_MAX = 500          # 每篇摘要截断（控制 token）
_MATERIALS_MAX = 300         # 每篇已有分析材料截断

REPORT_SYSTEM_PROMPT = """[ROLE]
你是科研调研助手。你的目标：基于提供的论文证据，生成一份结构化的科研调研报告初稿。
只输出 JSON。

[SOURCE TRACE RULES]
1. 所有事实必须来自输入论文（<PAPERS> 区块），禁止利用你的模型记忆补充
   论文内容、数值、结论或领域共识；
2. 每个章节必须填写 source_papers：以 [P#] 编号（如 [P1][P3]）在正文标注来源，
   并把编号写入该章节的 source_papers 数组；source_papers 不能为空；
3. 某个信息无法从当前论文集合判断时，正文写「根据当前论文集合无法判断」，
   并在 source_papers 中给出被考虑过的论文编号；
4. 正文出现的 [P#] 必须与 source_papers 一致，不得引用不存在的编号。

[USER PROFILE]
<USER PROFILE> 区块是用户科研画像（领域/子方向/关键词/暂不关注/阶段）。
仅用于分析"论文集合与用户方向的关系"（第六章），并据此给出阅读优先级建议。
画像只是对照参考：禁止修改任何 Evidence（论文内容），禁止因画像而虚构论文内容；
画像为空时第六章写「尚未设置科研画像，无法进行个性化方向匹配。」。

[SOURCE OWNERSHIP]
5. 禁止把单篇论文的贡献写成整个领域的既有事实（除非多篇论文共同支持）；
6. 禁止把综述论文的创新点冒充原创贡献；区分"该论文提出"与"领域已有工作"；
7. 禁止生成不存在的实验结果、数值、性能提升或作者结论。

[REPORT STRUCTURE]
输出一个 JSON 对象，章节**固定六个**，顺序如下：
1. 研究背景
2. 领域发展现状
3. 关键技术路线
4. 主要挑战
5. 未来研究方向
6. 与用户研究方向关系

JSON 格式：
{
  "title": "报告标题（一句话概括所选论文的调研主题）",
  "sections": [
    {"title": "研究背景", "content": "……", "source_papers": [1, 2]},
    {"title": "领域发展现状", "content": "……", "source_papers": [2, 3]},
    {"title": "关键技术路线", "content": "……", "source_papers": [1]},
    {"title": "主要挑战", "content": "……", "source_papers": [1, 3]},
    {"title": "未来研究方向", "content": "……", "source_papers": [2]},
    {"title": "与用户研究方向关系", "content": "……", "source_papers": [1, 2, 3]}
  ]
}
source_papers 是 [P#] 编号数组（1 到 N 的整数），必须与正文标注一致；
不要输出 JSON 以外的内容。"""


class ReportPromptError(RuntimeError):
    """报告 Prompt/解析基类错误。"""


class ReportParseError(ReportPromptError):
    """JSON 结构不满足契约（缺字段 / 类型错误）。"""


class ReportGroundingError(ReportPromptError):
    """来源标注不满足 Grounding（空来源 / 越界 / 引用不存在编号）。"""


def _truncate(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def _paper_block(paper, materials: dict, index: int) -> str:
    lines = [f"[P{index}] 标题：{paper.title or '（无标题）'}"]
    if paper.authors:
        lines.append(f"作者：{'、'.join(paper.authors)}")
    meta = []
    if paper.year:
        meta.append(f"年份：{paper.year}")
    if paper.source:
        meta.append(f"来源：{paper.source}")
    if paper.document_type:
        meta.append(f"文献类型：{paper.document_type}")
    if paper.direction_match_level:
        meta.append(f"方向匹配：{paper.direction_match_level}")
    if paper.reading_priority:
        meta.append(f"阅读优先级：{paper.reading_priority}")
    if paper.doi:
        meta.append(f"DOI：{paper.doi}")
    if meta:
        lines.append("；".join(meta))
    abstract = _truncate(getattr(paper, "abstract", "") or "", _ABSTRACT_MAX)
    if abstract:
        lines.append(f"摘要：{abstract}")
    materials_text = _truncate("；".join(
        f"{k}：{v}" for k, v in (materials or {}).items()), _MATERIALS_MAX)
    lines.append(f"已有分析材料：{materials_text or '（无）'}")
    return "\n".join(lines)


def build_report_prompt(context: ReportContext) -> str:
    """组装报告用户 prompt（<PAPERS> + <USER PROFILE> + 输出要求）。"""
    papers = list(context.papers or [])
    parts = [f"请基于以下 {len(papers)} 篇论文生成科研调研报告。", "", "[PAPERS]"]
    for index, paper in enumerate(papers, start=1):
        materials = context.materials_for(paper.paper_id)
        parts.append(_paper_block(paper, materials, index))
        parts.append("")
    parts.append("[USER PROFILE]")
    parts.append(context.profile_summary_text())
    parts.append("")
    parts.append(
        "输出 JSON（六章节固定：研究背景 / 领域发展现状 / 关键技术路线 / "
        "主要挑战 / 未来研究方向 / 与用户研究方向关系），"
        "每个章节的 source_papers 用 [P#] 编号数组；"
        "只输出 JSON 对象，不要输出其它内容。")
    return "\n".join(parts)


# ---------------- 解析 ----------------

def _mapping(context: ReportContext) -> dict[int, str]:
    """[P#] 编号 → 论文 id 映射（优先取 paper.index，缺省按位置）。"""
    mapping: dict[int, str] = {}
    papers = list(context.papers or [])
    for position, paper in enumerate(papers, start=1):
        index = int(getattr(paper, "index", 0) or 0) or position
        if paper.paper_id:
            mapping[index] = paper.paper_id
    return mapping


# 本阶段 Prompt 的六章节标题 → 模型 key（与 REPORT_SECTION_KEYS 一一对应）
_PROMPT_TITLE_TO_KEY = {title: key for key, title in zip(
    ("background", "trend", "tech_route", "challenge", "future",
     "profile_link"),
    PROMPT_SECTION_TITLES)}


def _map_key(title: str) -> str:
    """章节标题 → 模型 key（先精确匹配本阶段 Prompt 标题，再宽松匹配标签）。

    匹配不到返回空串（允许自定义章节，不影响可追溯性）。
    """
    title_clean = str(title or "").strip()
    if title_clean in _PROMPT_TITLE_TO_KEY:
        return _PROMPT_TITLE_TO_KEY[title_clean]
    for key, label in REPORT_SECTION_LABELS.items():
        if label == title_clean or label in title_clean or title_clean in label:
            return key
    return ""


def parse_report_sections(text: str, context: ReportContext
                          ) -> tuple[str, list[ReportSection]]:
    """从 LLM 输出解析 (title, sections)。

    任何契约违反都抛 `ReportParseError` / `ReportGroundingError`——
    由 ReportService 统一降级，**本函数与 Provider 都不降级**。
    """
    raw = (text or "").strip()
    if not raw:
        raise ReportParseError("报告 LLM 输出为空")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidate = fenced.group(1) if fenced else raw
    start = candidate.find("{")
    if start == -1:
        raise ReportParseError("报告 LLM 输出中未找到 JSON 对象")
    try:
        obj, _ = json.JSONDecoder().raw_decode(candidate[start:])
    except json.JSONDecodeError as exc:
        raise ReportParseError(f"报告 JSON 解析失败: {exc}") from exc
    if not isinstance(obj, dict):
        raise ReportParseError("报告 JSON 顶层必须是对象")

    title = str(obj.get("title") or "").strip()
    sections_raw = obj.get("sections")
    if not isinstance(sections_raw, list) or not sections_raw:
        raise ReportParseError("报告 JSON 缺少非空 sections 数组")

    n = context.paper_count
    mapping = _mapping(context)
    sections: list[ReportSection] = []
    for index, item in enumerate(sections_raw):
        if not isinstance(item, dict):
            raise ReportParseError(f"sections[{index}] 不是 JSON 对象")
        section_title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not section_title:
            raise ReportParseError(f"sections[{index}] 缺少 title")
        if not content:
            raise ReportParseError(f"sections[{index}] 缺少 content")

        sp = item.get("source_papers")
        if not isinstance(sp, list) or not sp:
            raise ReportGroundingError(
                f"章节「{section_title}」缺少来源标注（source_papers 不能为空）")
        indices: list[int] = []
        for value in sp:
            if isinstance(value, bool):
                raise ReportGroundingError(
                    f"章节「{section_title}」来源编号非法：{value!r}")
            if isinstance(value, int):
                number = value
            elif isinstance(value, str) and value.strip().isdigit():
                number = int(value.strip())
            else:
                raise ReportGroundingError(
                    f"章节「{section_title}」来源编号非法：{value!r}（应为 1..{n} 的整数）")
            if number < 1 or number > n:
                raise ReportGroundingError(
                    f"章节「{section_title}」来源编号 {number} 超出论文范围 1..{n}")
            indices.append(number)

        seen: set[int] = set()
        indices = [x for x in indices if not (x in seen or seen.add(x))]
        paper_ids = [mapping[i] for i in indices if i in mapping]
        if not paper_ids:
            raise ReportGroundingError(
                f"章节「{section_title}」无法映射到任何来源论文")

        sections.append(ReportSection(
            title=section_title, content=content,
            source_papers=paper_ids, key=_map_key(section_title)))

    if not title:
        raise ReportParseError("报告 JSON 缺少 title")
    return title, sections
