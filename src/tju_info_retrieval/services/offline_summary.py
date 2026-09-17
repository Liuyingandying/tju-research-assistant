"""离线结构化关键信息整理引擎（v0.17 Phase 2.4-B-OFFLINE）。

确定性句级抽取：仅依据 SearchResult 当前已有的真实文本证据
（abstract / 列表摘要 / 详情正文瞬态片段 / metadata），
按字段词类评分选取子句，输出文本全部为证据子串——不引入任何
原文不存在的信息（no-hallucination），证据不足的字段诚实降级。

约束：
- 无 LLM / 无网络 / 无新依赖；
- 同一子句至多被一个字段使用（避免同句无意义占满四字段）；
- 仅凭标题（无任何 prose 证据）→ 全字段信息不足（paper 现状）。
"""
from __future__ import annotations

import re

from tju_info_retrieval.models.artifact import ARTIFACT_NEWS, ARTIFACT_PATENT
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.models.summary import SUMMARY_FIELDS, StructuredSummary

INSUFFICIENT_TEXT = "当前材料未提供足够信息，无法可靠提取。"

# 字段词类（起点词表，结合真实新闻/专利样本调试）
FIELD_LEXICONS: dict[str, tuple[str, ...]] = {
    "research_content": (
        "研究", "针对", "面向", "旨在", "解决", "围绕", "开展",
        "研究了", "开发", "团队",
    ),
    "core_technology": (
        "提出", "采用", "基于", "利用", "通过", "设计", "构建",
        "方法", "技术", "系统", "装置", "算法", "模型", "结构", "机制",
    ),
    "main_results": (
        "实现", "达到", "获得", "结果表明", "实验表明", "成功",
        "发现", "提高", "降低", "验证", "研制",
    ),
    "application_value": (
        "应用", "可用于", "有望", "适用于", "场景", "产业", "临床",
        "通信", "检测", "成像", "工程", "前景",
    ),
}

# artifact 类型词类加成（同一引擎，仅词类优先级差异）
TYPE_BONUS_LEXICONS: dict[str, dict[str, tuple[str, ...]]] = {
    ARTIFACT_PATENT: {
        "research_content": ("为了", "实现", "公开", "涉及", "目的", "提供"),
        "main_results": ("定量分析", "定量检测", "测量", "授权"),
    },
    ARTIFACT_NEWS: {
        "main_results": ("入选", "当选", "突破", "发表", "荣获"),
        "research_content": ("报道", "介绍"),
    },
}

# 英文词类最小集（v0.17 Phase 2.4-C-C）：与中文词类运行期合并；
# 英文 evidence 命中后按原文子句输出（不做翻译）。
EN_FIELD_LEXICONS: dict[str, tuple[str, ...]] = {
    "research_content": (
        "study", "investigate", "investigated", "focus", "focused", "aim",
        "aims", "address", "addressed", "explore", "analyze", "analysis",
        "this work", "this paper", "we study", "we investigate",
    ),
    "core_technology": (
        "propose", "proposed", "develop", "developed", "design", "designed",
        "based on", "using", "utilize", "employ", "method", "approach",
        "technique", "system", "device", "algorithm", "model", "architecture",
        "framework", "mechanism",
    ),
    "main_results": (
        "result", "results", "demonstrate", "demonstrated", "show", "shown",
        "achieve", "achieved", "improve", "improved", "reduce", "reduced",
        "increase", "increased", "outperform", "validation", "experiment",
        "experimental",
    ),
    "application_value": (
        "application", "applications", "can be used", "can be applied",
        "potential", "promising", "useful for", "enable", "enables",
        "imaging", "communication", "communications", "sensing",
        "detection", "biomedical", "industrial",
    ),
}

# 句/子句切分：句末标点（含英文 .?!）→ 长句再按逗号切子句。
# 科研文本中常见 period（e.g. / i.e. / et al. / Fig. / 小数 0.5）先保护，
# 避免被误切。
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;.\n]+")
_CLAUSE_SPLIT_RE = re.compile(r"[，,]+")
_ABBREV_RE = re.compile(
    r"(?i)\b(e\.g|i\.e|et\s+al|fig|eq|dr|etc|vs|approx|cf|no)\.(?=\s|$)"
)
_DECIMAL_RE = re.compile(r"(?<=\d)\.(?=\d)")
_MIN_CLAUSE_CHARS = 3
_MAX_FIELD_CHARS = 120


def split_into_clauses(text_blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """证据文本块 → (来源标签, 子句) 列表。

    text_blocks: [(来源标签, 文本块)]；块内先按句末标点分句，
    长句（>50 字）再按逗号切子句；去重、去短碎片。

    v0.17 Phase 2.4-C-C 英文支持：切分前先保护科研文本常见 period
    （e.g./i.e./et al./Fig./Eq./Dr./etc./vs./No. 与小数 0.5），
    切分完成后还原——避免把缩写/小数切成畸形碎片。
    """
    clauses: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, block in text_blocks:
        text = (block or "").strip()
        if not text:
            continue
        text = _protect_periods(text)
        for sentence in _SENTENCE_SPLIT_RE.split(text):
            sentence = _restore_periods(sentence).strip()
            if len(sentence) >= _MIN_CLAUSE_CHARS:
                candidates = [sentence]
                if len(sentence) > 50:
                    candidates = [
                        _restore_periods(c).strip()
                        for c in _CLAUSE_SPLIT_RE.split(sentence)
                        if len(_restore_periods(c).strip()) >= _MIN_CLAUSE_CHARS
                    ] or [sentence]
                for clause in candidates:
                    if clause not in seen:
                        seen.add(clause)
                        clauses.append((label, clause))
    return clauses


_PERIOD_SENTINEL = "\x00"
_HAS_ENGLISH = re.compile(r"[A-Za-z]{2,}")
_HAS_CJK = re.compile(r"[一-鿿]")

# 英文低信息 gate（v0.17 2.4-C-C Hotfix；仅作用于含英文的子句，中文零影响）。
# 1) discourse-only：纯连接/冠词/代词等组成的碎片（and in this paper）不入选任何字段；
# 2) research_content 需含实质研究动作词（study/investigate/address/focus/aim…）；
# 3) main_results 需含强结果证据（demonstrate/achieve/results/validate/outperform…），
#    弱表达（efforts/attempt/仅 improve）不再够格；
# 4) application_value 含 negative/limitation 信号（difficult/limitation/cannot/
#    however/but…）→ 拒绝（负面限制不作正向应用价值，且不做改写）。
_DISCOURSE_TOKENS = frozenset(
    "and in this paper work our study the of for to a an we i on with by from "
    "that which at as its their however therefore but or be been is are was "
    "were not no it so such can could would will may might must".split()
)
_EN_RESEARCH_STEMS = (
    "investigat", "stud", "address", "focus", "aim", "explor", "analyz",
    "analys", "examin", "evaluat", "survey", "review",
)
_EN_RESULT_STEMS = (
    "demonstrat", "achiev", "result", "results", "validat", "outperform",
    "successfully", "we found", "show that", "indicate", "reveal", "confirm",
)
_EN_NEGATIVE = (
    "difficult", "difficulty", "limitation", "limited", "challenge",
    "cannot", "unable", "restrict", "prevent", "barrier", "problem",
    "drawback", "however", "suffers from", "hard to", "cannot be",
    "limited by",
)
_LEADING_CONNECTIVES = frozenset(
    "and but however therefore moreover furthermore also in addition".split()
)


def _tokens_of(clause: str) -> list[str]:
    return [t for t in re.split(r"[^A-Za-z]+", clause.lower()) if t]


def _strip_leading_discourse(clause: str) -> str:
    """英文子句去除句首 discourse 前缀（如 “and in this paper”）。

    仅删除词首连接词/介词短语 token（≤3 个），余部仍为 evidence 子串
    （select 范畴，非改写）；中文子句原样返回。
    """
    if not _HAS_ENGLISH.search(clause) or _HAS_CJK.search(clause):
        return clause
    tokens = clause.split()
    stripped = 0
    # 前缀链内允许剥除 discourse token 及其后继 "paper"/"work"（如
    # “and in this paper a compressed sensing approach…” → “a compressed…”）
    strippable = _LEADING_CONNECTIVES | {"in", "this", "our", "paper", "work"}
    while tokens and stripped < 4 and tokens[0].lower().strip(",.;") in strippable:
        tokens.pop(0)
        stripped += 1
    return " ".join(tokens) if tokens else clause


def clause_eligible_for_field(clause: str, field_key: str) -> bool:
    """英文子句的字段资格 gate（中文子句恒 True，零影响）。"""
    if not _HAS_ENGLISH.search(clause):
        return True
    # 中文为主（可内嵌英文词如 Optica/THz/RF）→ 跳过英文 gate，防误伤
    if _HAS_CJK.search(clause):
        return True
    tokens = _tokens_of(clause)
    if not tokens:
        return False
    if all(t in _DISCOURSE_TOKENS for t in tokens):
        return False  # and in this paper / however / in addition …
    if field_key == "research_content":
        return any(stem in clause.lower() for stem in _EN_RESEARCH_STEMS)
    if field_key == "main_results":
        return any(stem in clause.lower() for stem in _EN_RESULT_STEMS)
    if field_key == "application_value":
        return not any(neg in clause.lower() for neg in _EN_NEGATIVE)
    return True


def _protect_periods(text: str) -> str:
    """小数与常见缩写句点 → 哨兵（防句切）。"""
    text = _DECIMAL_RE.sub(_PERIOD_SENTINEL, text)
    return _ABBREV_RE.sub(lambda m: m.group(0).replace(".", _PERIOD_SENTINEL), text)


def _restore_periods(text: str) -> str:
    return text.replace(_PERIOD_SENTINEL, ".")


def _score_clause(clause: str, lexicon: tuple[str, ...]) -> int:
    return sum(clause.count(term) for term in lexicon)


def _collect_evidence(result: SearchResult) -> list[tuple[str, str]]:
    """按 artifact 类型收集可用文本证据块（仅复用现有字段）。"""
    blocks: list[tuple[str, str]] = []
    artifact_type = getattr(result, "artifact_type", "paper") or "paper"
    abstract = (getattr(result, "abstract", None) or "").strip()
    if artifact_type == ARTIFACT_NEWS:
        if abstract:
            blocks.append(("summary", abstract))
        # 详情正文瞬态片段（搜索会话内存在；不入 to_dict）
        head = getattr(result, "_news_content_head", None) or ""
        if head.strip():
            blocks.append(("detail_content", head.strip()))
    elif artifact_type == ARTIFACT_PATENT:
        if abstract:
            blocks.append(("abstract", abstract))
    else:  # paper：当前三源无摘要 → 无 prose 证据
        if abstract:
            blocks.append(("abstract", abstract))
    return blocks


def _stable_id(result: SearchResult) -> str:
    return (
        (getattr(result, "detail_url", None) or "")
        or (getattr(result, "doi", None) or "")
        or ((result.artifact_metadata or {}).get("publication_number") or "")
        or re.sub(r"[\s\u3000]+", "", (result.title or "")).lower()
    )


_SUMMARY_CACHE: dict[tuple[str, str], StructuredSummary] = {}
_CACHE_MAX = 300


class OfflineSummaryEngine:
    """离线结构化整理引擎（确定性抽取，无 LLM/网络）。"""

    def __init__(self, cache: dict | None = None) -> None:
        self._cache = cache if cache is not None else _SUMMARY_CACHE

    def summarize(self, result: SearchResult) -> StructuredSummary:
        key = (getattr(result, "artifact_type", "paper") or "paper",
               _stable_id(result))
        if key in self._cache:
            return self._cache[key]
        summary = self._summarize_uncached(result)
        if len(self._cache) > _CACHE_MAX:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = summary
        return summary

    def _summarize_uncached(self, result: SearchResult) -> StructuredSummary:
        artifact_type = getattr(result, "artifact_type", "paper") or "paper"
        blocks = _collect_evidence(result)
        clauses = split_into_clauses(blocks)
        summary = StructuredSummary()
        used: set[str] = set()
        for field_key in SUMMARY_FIELDS:
            lexicon = (
                FIELD_LEXICONS[field_key]
                + EN_FIELD_LEXICONS.get(field_key, ())
                + TYPE_BONUS_LEXICONS.get(artifact_type, {}).get(field_key, ())
            )
            best_label, best_clause, best_score = "", "", 0
            for label, clause in clauses:
                if clause in used:
                    continue  # 每个子句至多被一个字段使用
                if not clause_eligible_for_field(clause, field_key):
                    continue  # v0.17 Hotfix：英文低信息/极性/阈值 gate
                score = _score_clause(clause, lexicon)
                if score > best_score:
                    best_label, best_clause, best_score = label, clause, score
            if best_score <= 0 or not best_clause:
                setattr(summary, field_key, INSUFFICIENT_TEXT)
                summary.source_basis[field_key] = "none"
            else:
                value = _strip_leading_discourse(best_clause)
                if len(value) > _MAX_FIELD_CHARS:
                    value = value[:_MAX_FIELD_CHARS] + "…"
                setattr(summary, field_key, value)
                summary.source_basis[field_key] = best_label
                used.add(best_clause)
        return summary


def summarize_offline(result: SearchResult) -> StructuredSummary:
    """便捷入口（带进程级缓存）。"""
    return OfflineSummaryEngine().summarize(result)