"""科研调研报告模型（v0.18 Phase 2.9-C-B）。

报告是**论文库的只读投影**（read-only projection）：

    LibraryRecord[]  ──(生成时快照)──▶  ResearchReport

设计约束（本阶段只做模型与持久化基础）：

1. **不污染论文库**：不修改 `LibraryRecord` / `EnhancedSummary` / `ResearchProfile`；
   报告自带 `ReportSource` 快照，**不实时依赖** LibraryRecord —— 论文库后续
   增删改都不影响已生成报告的可读性。
2. **每个章节可追溯**：`ReportSection.source_papers` 保存来源论文的 `paper_id`
   列表，正文与来源一一对应。
3. **schema 可升级**：顶层 `schema_version`；`from_dict` 忽略未知字段、
   缺失字段取默认值、非法类型安全降级（不抛异常）。
4. **记录生成方式**：`generation_scope` 记录"这个报告如何产生"
   （论文数 / 画像版本 / 当时的论文库筛选条件）。

本阶段**不实现**：ReportService、LLM Prompt、UI、批量生成、Markdown 导出
（均留待后续阶段）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

# 报告 schema 版本（结构变化时递增；from_dict 兼容更低版本）
REPORT_SCHEMA_VERSION = 1

# 报告 Prompt 版本占位（2.9-C-D 实现 Prompt 时使用同一常量）
REPORT_PROMPT_VERSION = "r1"

# 报告状态
STATUS_DRAFT = "draft"
STATUS_GENERATED = "generated"
STATUS_FAILED = "failed"
REPORT_STATUSES = (STATUS_DRAFT, STATUS_GENERATED, STATUS_FAILED)
REPORT_STATUS_LABELS = {
    STATUS_DRAFT: "草稿",
    STATUS_GENERATED: "已生成",
    STATUS_FAILED: "生成失败",
}

# 章节键（固定 6 节；与 2.9-C-D Prompt 章节一一对应）
SECTION_BACKGROUND = "background"
SECTION_TREND = "trend"
SECTION_TECH_ROUTE = "tech_route"
SECTION_CHALLENGE = "challenge"
SECTION_FUTURE = "future"
SECTION_PROFILE_LINK = "profile_link"
REPORT_SECTION_KEYS = (
    SECTION_BACKGROUND,
    SECTION_TREND,
    SECTION_TECH_ROUTE,
    SECTION_CHALLENGE,
    SECTION_FUTURE,
    SECTION_PROFILE_LINK,
)
# 基础报告（降级）专用章节：只由论文库既有数据聚合，不含任何 AI 归纳
SECTION_OVERVIEW = "collection_overview"
SECTION_EXISTING_DIRECTIONS = "existing_directions"
BASE_SECTION_KEYS = (SECTION_OVERVIEW, SECTION_EXISTING_DIRECTIONS)

REPORT_SECTION_LABELS = {
    SECTION_BACKGROUND: "研究背景",
    SECTION_TREND: "领域发展趋势",
    SECTION_TECH_ROUTE: "关键技术路线",
    SECTION_CHALLENGE: "主要挑战",
    SECTION_FUTURE: "未来方向",
    SECTION_PROFILE_LINK: "与用户研究方向关系",
    SECTION_OVERVIEW: "文献集合概览",
    SECTION_EXISTING_DIRECTIONS: "已有研究方向",
}

# 降级章节的固定文案（禁止编造：只是明确说明未调用 AI）
NOT_GENERATED_TEXT = "未调用AI生成：本次未使用 AI 归纳该章节。"
NOT_GENERATED_REASON_TEXT = (
    "未调用 AI：本机未配置或调用失败，无法从所选论文归纳该章节。")

# 无画像时的固定措辞（与单篇分析保持一致，禁止个性化编造）
NO_PROFILE_RELATION_TEXT = "尚未设置科研画像，无法进行个性化方向匹配。"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_str(value) -> str:
    return str(value) if value not in (None, "") else ""


def _as_str_list(value) -> list[str]:
    """字符串列表归一：接受 list / 单个字符串；过滤空值。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _norm_version(value) -> str:
    """版本号规范化：`p3` / `3` / ` 3 ` → `3`（用于跨来源比较）。"""
    text = str(value or "").strip()
    if text[:1] in ("p", "P"):
        text = text[1:].strip()
    return text


@dataclass
class ReportSource:
    """报告来源论文的**生成时快照**（不实时依赖 LibraryRecord）。

    论文库中的记录可能被修改或删除；报告凭本快照必须可独立阅读。
    """

    paper_id: str = ""            # 论文库记录 id（用于溯源/回跳）
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: str = ""
    doi: str = ""
    # 论文摘要（报告 Prompt 的核心证据；生成时快照，非实时引用）
    abstract: str = ""
    artifact_type: str = "paper"
    direction_match_level: str = ""
    reading_priority: str = ""
    # 以下为便于阅读与追溯的补充快照（可选，缺失不影响模型合法性）
    source: str = ""
    document_type: str = ""
    index: int = 0                # 报告中 [P#] 编号（1-based）

    def to_dict(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "doi": self.doi,
            "abstract": self.abstract,
            "artifact_type": self.artifact_type,
            "direction_match_level": self.direction_match_level,
            "reading_priority": self.reading_priority,
            "source": self.source,
            "document_type": self.document_type,
            "index": self.index,
        }

    @classmethod
    def from_dict(cls, data) -> "ReportSource":
        data = data if isinstance(data, dict) else {}
        return cls(
            paper_id=_as_str(data.get("paper_id")),
            title=_as_str(data.get("title")),
            authors=_as_str_list(data.get("authors")),
            year=_as_str(data.get("year")),
            doi=_as_str(data.get("doi")),
            abstract=_as_str(data.get("abstract")),
            artifact_type=_as_str(data.get("artifact_type")) or "paper",
            direction_match_level=_as_str(data.get("direction_match_level")),
            reading_priority=_as_str(data.get("reading_priority")),
            source=_as_str(data.get("source")),
            document_type=_as_str(data.get("document_type")),
            index=_as_int(data.get("index")),
        )

    def display_name(self) -> str:
        """引用展示名：标题（作者, 年份）。"""
        authors = "、".join(self.authors[:2])
        if len(self.authors) > 2:
            authors += "等"
        tail = ", ".join(x for x in (authors, self.year) if x)
        return f"{self.title}（{tail}）" if tail else self.title


@dataclass
class ReportSection:
    """报告章节（每个章节可追溯来源论文）。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    title: str = ""
    content: str = ""
    order: int = 0
    source_papers: list[str] = field(default_factory=list)   # 引用 ReportSource.paper_id
    # 章节键（6 节固定键之一；自定义章节可为空）
    key: str = ""
    # 证据不足标记（生成时明确标注，而不是编造）
    insufficient: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "order": self.order,
            "source_papers": list(self.source_papers),
            "key": self.key,
            "insufficient": self.insufficient,
        }

    @classmethod
    def from_dict(cls, data) -> "ReportSection":
        data = data if isinstance(data, dict) else {}
        return cls(
            id=_as_str(data.get("id")) or uuid.uuid4().hex,
            title=_as_str(data.get("title")),
            content=_as_str(data.get("content")),
            order=_as_int(data.get("order")),
            source_papers=_as_str_list(data.get("source_papers")),
            key=_as_str(data.get("key")),
            insufficient=bool(data.get("insufficient")),
        )

    def has_sources(self) -> bool:
        return bool(self.source_papers)


def build_generation_scope(
    paper_count: int = 0,
    profile_version=None,
    library_filter: dict | None = None,
) -> dict:
    """组装 `generation_scope`：记录"这个报告如何产生"。

    结构（与 2.9-C 任务书一致）：

        {"paper_count": 8,
         "profile_version": 3,
         "library_filter": {"direction_match": ["strong", "medium"],
                            "reading_priority": ["deep_read", "priority_read"]}}
    """
    scope: dict = {"paper_count": _as_int(paper_count)}
    if profile_version not in (None, ""):
        scope["profile_version"] = _as_int(profile_version)
    scope["library_filter"] = _as_dict(library_filter)
    return scope


@dataclass
class ResearchReport:
    """科研调研报告（论文库的只读投影）。"""

    # --- 基础信息 ---
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    title: str = ""
    created_time: str = field(default_factory=_now_iso)

    # --- 报告来源 ---
    profile_snapshot: dict = field(default_factory=dict)   # 生成时科研画像快照
    papers: list[ReportSource] = field(default_factory=list)
    sections: list[ReportSection] = field(default_factory=list)

    # --- 生成信息 ---
    provider: str = ""
    model: str = ""
    report_prompt_version: str = REPORT_PROMPT_VERSION

    # --- 状态 ---
    status: str = STATUS_DRAFT

    # --- 版本 ---
    schema_version: int = REPORT_SCHEMA_VERSION

    # --- 生成方式（记录如何产生） ---
    generation_scope: dict = field(default_factory=dict)

    # ---------- 语义辅助 ----------

    @property
    def paper_count(self) -> int:
        return len(self.papers)

    def paper_by_id(self, paper_id: str) -> ReportSource | None:
        return next((p for p in self.papers
                     if p.paper_id and p.paper_id == paper_id), None)

    def ordered_sections(self) -> list[ReportSection]:
        """按 order 排序的章节（同 order 保持插入顺序）。"""
        return sorted(self.sections, key=lambda s: (s.order, s.title))

    def section(self, key: str) -> ReportSection | None:
        return next((s for s in self.sections if s.key == key), None)

    def traced_paper_ids(self) -> set[str]:
        """被任一章节引用的论文 id 集合（可追溯性检查用）。"""
        return {pid for s in self.sections for pid in s.source_papers}

    def untraced_paper_ids(self) -> set[str]:
        """未被任何章节引用的论文 id（生成质量提示用）。"""
        return {p.paper_id for p in self.papers if p.paper_id} \
            - self.traced_paper_ids()

    def dangling_source_ids(self) -> set[str]:
        """章节引用但 papers 中不存在的 id（追溯断裂，应提示）。"""
        known = {p.paper_id for p in self.papers if p.paper_id}
        return self.traced_paper_ids() - known

    def profile_version(self) -> str:
        """生成时画像版本（从快照读取；缺省返回空串）。"""
        value = self.profile_snapshot.get("profile_version")
        return "" if value in (None, "") else str(value)

    def is_stale(self, profile_version=None, prompt_version=None) -> bool:
        """报告是否基于旧画像 / 旧 Prompt 版本（仅提示，不自动重算）。

        画像版本在不同来源写法不同（报告快照存 `2`，论文库存 `p2`），
        比较前统一规范化（去 `p` 前缀 / 去空白），避免误报。
        """
        if profile_version not in (None, ""):
            snapshot = _norm_version(self.profile_version())
            current = _norm_version(profile_version)
            if snapshot and current and current != snapshot:
                return True
        if prompt_version not in (None, ""):
            if (self.report_prompt_version
                    and str(prompt_version) != self.report_prompt_version):
                return True
        return False

    def missing_source_paper_ids(self, library_ids) -> list[str]:
        """已从论文库移除的来源论文 id（报告内容仍保留）。"""
        known = {str(i) for i in (library_ids or [])}
        return [p.paper_id for p in self.papers
                if p.paper_id and p.paper_id not in known]

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_time": self.created_time,
            "profile_snapshot": dict(self.profile_snapshot),
            "papers": [p.to_dict() for p in self.papers],
            "sections": [s.to_dict() for s in self.sections],
            "provider": self.provider,
            "model": self.model,
            "report_prompt_version": self.report_prompt_version,
            "status": self.status,
            "schema_version": int(self.schema_version),
            "generation_scope": dict(self.generation_scope),
        }

    @classmethod
    def from_dict(cls, data) -> "ResearchReport":
        """反序列化：未知字段忽略、缺失取默认、非法类型安全降级。

        `schema_version` 高于本版本时仍按已知字段尽力加载（前向兼容），
        不抛异常、不丢数据。
        """
        data = data if isinstance(data, dict) else {}
        status = _as_str(data.get("status")) or STATUS_DRAFT
        if status not in REPORT_STATUSES:
            status = STATUS_DRAFT
        papers_raw = data.get("papers")
        sections_raw = data.get("sections")
        return cls(
            id=_as_str(data.get("id")) or uuid.uuid4().hex,
            title=_as_str(data.get("title")),
            created_time=_as_str(data.get("created_time")),
            profile_snapshot=_as_dict(data.get("profile_snapshot")),
            papers=[ReportSource.from_dict(x)
                    for x in papers_raw if isinstance(x, dict)]
            if isinstance(papers_raw, list) else [],
            sections=[ReportSection.from_dict(x)
                      for x in sections_raw if isinstance(x, dict)]
            if isinstance(sections_raw, list) else [],
            provider=_as_str(data.get("provider")),
            model=_as_str(data.get("model")),
            report_prompt_version=(
                _as_str(data.get("report_prompt_version"))
                or REPORT_PROMPT_VERSION),
            status=status,
            schema_version=_as_int(data.get("schema_version"),
                                   REPORT_SCHEMA_VERSION),
            generation_scope=_as_dict(data.get("generation_scope")),
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "ResearchReport":
        """从 JSON 文本加载；非法 JSON → 抛 ValueError（由调用方处理）。"""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("报告 JSON 为空")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"报告 JSON 非法: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("报告 JSON 顶层必须是对象")
        return cls.from_dict(data)
