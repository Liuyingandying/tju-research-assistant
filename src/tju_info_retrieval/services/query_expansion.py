"""Query Expansion 纯逻辑层（v0.3）。

规则式、确定性、本地：无 AI、无网络、无 GUI 依赖。
把研究方向扩展为一条单次检索用的 CNKI 布尔检索串：
  - 水平扩展：内置同义词词表（未命中保持原词，不猜测）。
  - 垂直扩展：扩展方向（理论/应用/综述）附加限定词；综合不附加。

本模块只做纯逻辑，不接入 GUI / 不修改现有搜索流程（接入见 v0.3 设计文档）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tju_info_retrieval.services.expansion_direction import (
    GENERAL as DIRECTION_GENERAL,
    ZH_TERMS,
)

# 同义词词表（有限、人工维护；未命中则使用原词）
SYNONYM_THESAURUS: dict[str, list[str]] = {
    "太赫兹": ["THz", "太赫兹波", "Terahertz"],
    "深度学习": ["深度神经网络", "DNN", "神经网络"],
    "机器学习": ["Machine Learning", "ML", "统计学习"],
    "知识图谱": ["Knowledge Graph", "知识图"],
}

# 方向限定词（v0.17 Phase 2.5-B：集中委托 expansion_direction.ZH_TERMS；
# 综述已收敛为稳定短组（综述/进展）——旧 5 词组（综述/进展/研究现状/展望/回顾）
# 触发 CNKI 普通搜索 Boolean 解析脆弱性，真实页面返回“暂无数据”）
DIRECTION_FILTERS: dict[str, list[str]] = {
    direction: list(ZH_TERMS[direction]) for direction in ZH_TERMS
}

DEFAULT_DIRECTION = "综合"


@dataclass
class ExpandedQuery:
    """一次扩展的结果（纯数据，供上层展示/使用）。"""

    original: str
    expansion_direction: str
    terms: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    query_string: str = ""
    notes: list[str] = field(default_factory=list)


class QueryExpansionService:
    """把研究方向扩展为单条 CNKI 检索串（纯逻辑，可单测）。"""

    def expand(
        self,
        research_direction: str,
        expansion_direction: str = DEFAULT_DIRECTION,
    ) -> ExpandedQuery:
        original = (research_direction or "").strip()
        if not original:
            raise ValueError("研究方向不能为空")

        direction = expansion_direction or DEFAULT_DIRECTION
        notes: list[str] = []

        # 水平扩展：同义词（未命中保持原词，不猜测）
        terms = [original]
        synonyms = SYNONYM_THESAURUS.get(original, [])
        if synonyms:
            for syn in synonyms:
                if syn != original and syn not in terms:
                    terms.append(syn)
            notes.append(f"同义词扩展：{'、'.join(synonyms)}")
        else:
            notes.append("未命中同义词词表，使用原词")

        # 垂直扩展：方向限定（综合/未知方向不附加）
        filters = list(DIRECTION_FILTERS.get(direction, []))
        if direction not in DIRECTION_FILTERS:
            notes.append(f"未知扩展方向“{direction}”，按综合处理")
        elif filters:
            notes.append(f"方向限定[{direction}]：{'、'.join(filters)}")
        else:
            notes.append("综合方向：不附加限定词")

        query_string = self._build_query_string(terms, filters)
        return ExpandedQuery(
            original=original,
            expansion_direction=direction,
            terms=terms,
            filters=filters,
            query_string=query_string,
            notes=notes,
        )

    @staticmethod
    def _build_query_string(terms: list[str], filters: list[str]) -> str:
        """组装单条 CNKI 布尔检索串：(A OR B) AND (C OR D)。"""
        term_part = "(" + " OR ".join(terms) + ")" if len(terms) > 1 else terms[0]
        if filters:
            filter_part = "(" + " OR ".join(filters) + ")"
            return f"{term_part} AND {filter_part}"
        return term_part
