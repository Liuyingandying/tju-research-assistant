"""扩展方向集中语义 Spec 与 applicability resolver（v0.17 Phase 2.5-B）。

原则：
- 方向词集中管理，不散落 UI / adapter / translator；
- 禁止领域词（imaging/communication/sensing…）——方向只表达“研究取向”；
- CNKI zh 应用/理论保留 Phase 2.5-A 真机已验证词组；综述收敛为已验证的
  稳定短组（综述 OR 进展）——旧 5 词组（综述/进展/研究现状/展望/回顾）会
  触发 CNKI 普通搜索 Boolean 解析脆弱性（真实页面“暂无数据”）；
- IEEE en 组来自 Phase 2.5-A Route-A 真机验证（(topic) AND (direction-group)）。
"""
from __future__ import annotations

# 用户可见方向（与 UI EXPANSION_OPTIONS 一致）
GENERAL = "综合"
APPLICATION = "应用"
THEORY = "理论"
REVIEW = "综述"
DIRECTIONS = (GENERAL, APPLICATION, THEORY, REVIEW)

# 中文方向词（CNKI 用；应用/理论为已真机验证组，综述为稳定短组）
ZH_TERMS: dict[str, tuple[str, ...]] = {
    GENERAL: (),
    APPLICATION: ("应用", "系统", "装置", "实现", "工程"),
    THEORY: ("理论", "机理", "原理", "模型", "算法"),
    REVIEW: ("综述", "进展"),
}

# 英文方向词（IEEE Route-A 用，Phase 2.5-A 真机验证）
EN_TERMS: dict[str, tuple[str, ...]] = {
    GENERAL: (),
    APPLICATION: ("application", "applications", "applied"),
    THEORY: ("theory", "theoretical", "mechanism", "model"),
    REVIEW: ("review", "survey", "overview"),
}

# 哪些内部 source 真正支持 direction 语义（其余一律 GENERAL fallback）
DIRECTION_SOURCE_SUPPORT: dict[str, frozenset[str]] = {
    "CNKI": frozenset(DIRECTIONS),
    "IEEE Xplore": frozenset(DIRECTIONS),
}

# 内部 source 类别（用于用户提示措辞）
_PATENT_SOURCES = frozenset({"CNKI专利", "万方专利"})
_NEWS_SOURCE = frozenset({"天津大学新闻网"})
_WANFANG_PAPER = "万方"


def zh_terms(direction: str) -> tuple[str, ...]:
    return ZH_TERMS.get(direction, ())


def en_terms(direction: str) -> tuple[str, ...]:
    return EN_TERMS.get(direction, ())


def direction_applies_to_source(source: str, direction: str) -> bool:
    """该内部 source 是否真实应用该方向的 topic 语义。"""
    return direction in DIRECTION_SOURCE_SUPPORT.get(source, frozenset())


def resolve_expansion_behavior(
    artifact_types: list[str],
    effective_sources: list[str],
    expansion_direction: str,
    has_topic: bool,
) -> tuple[list[str], list[str], str | None]:
    """纯函数：决定每个 source 的方向行为与一次性用户提示。

    返回 (direction_applied_sources, general_fallback_sources, user_hint)。
    - has_topic=False（pure person）或 direction=综合 → 无方向应用、无 fallback、
      无提示（Phase 1 不变量：pure person ignore expansion）；
    - 支持方向的 source 应用方向；其余 GENERAL fallback（非 silent）；
    - 提示合并为一句自然文案（不弹多个窗口）。
    """
    if not has_topic or expansion_direction == GENERAL:
        return [], [], None
    applied = [
        s for s in effective_sources
        if direction_applies_to_source(s, expansion_direction)
    ]
    fallback = [s for s in effective_sources if s not in applied]
    hint = _build_hint(fallback, expansion_direction, artifact_types)
    return applied, fallback, hint


def _build_hint(fallback: list[str], direction: str,
                artifact_types: list[str]) -> str | None:
    if not fallback:
        return None
    parts: list[str] = []
    if _WANFANG_PAPER in fallback:
        parts.append("万方论文当前暂不应用该扩展方向，将按综合主题模式检索")
    has_patent = any(s in _PATENT_SOURCES for s in fallback)
    has_news = any(s in _NEWS_SOURCE for s in fallback)
    if has_patent and has_news:
        parts.append("专利与新闻结果将按综合主题模式检索")
    elif has_patent:
        parts.append("专利结果将按综合主题模式检索")
    elif has_news:
        parts.append("新闻结果将按综合主题模式检索")
    if not parts:
        return None
    return "；".join(parts) + "。"