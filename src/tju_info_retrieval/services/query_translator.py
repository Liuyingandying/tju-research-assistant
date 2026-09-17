"""Query Translation 纯逻辑层（v0.8 Phase 1 骨架 + Phase 2 概念分组）。

把扩展后的检索词按目标数据源翻译为对应语言：
  - 中文数据源（CNKI / 万方）：默认透传，保持原检索串。
  - 英文数据源（IEEE Xplore）：按内置概念词典把已知中文词替换为英文等价词，
    未知词保持原样（不猜测）。

Phase 1 提供 translate()（扁平检索串，向后兼容）；
Phase 2 新增 translate_concepts()（概念分组，供 QueryBuilder 生成数据库 query）。
不引入 LLM、不做复杂语义翻译。
"""

from __future__ import annotations

from tju_info_retrieval.services.concept_segmenter import ConceptSegmenter
from tju_info_retrieval.services.expansion_direction import (
    GENERAL as DIRECTION_GENERAL,
    en_terms,
)
from tju_info_retrieval.services.query_expansion import ExpandedQuery
from tju_info_retrieval.services.terminology import TERMS

# 需要翻译为英文的数据源（其余数据源透传中文原串）
ENGLISH_SOURCES: set[str] = {"IEEE Xplore"}


class QueryTranslator:
    """按数据源把扩展检索词翻译为对应语言（纯逻辑，可单测）。"""

    def translate(
        self,
        expanded: ExpandedQuery,
        sources: list[str],
    ) -> dict[str, str]:
        """为每个目标数据源生成扁平检索串（向后兼容，Phase 1 行为）。

        中文数据源透传原串；英文数据源把概念词空格连接。
        """
        queries: dict[str, str] = {}
        for source in sources:
            if self._is_english_source(source):
                concepts = self._concepts_for(expanded)
                queries[source] = " ".join(t for group in concepts for t in group)
            else:
                queries[source] = expanded.query_string
        return queries

    def translate_concepts(
        self,
        expanded: ExpandedQuery,
        sources: list[str],
    ) -> dict[str, list[list[str]]]:
        """为每个目标数据源生成概念分组（供 QueryBuilder 组装数据库 query）。

        英文数据源：[[syn1, syn2], [syn3, syn4], ...]，每个内层列表为一个概念的同义词组；
        中文数据源：[[query_string]]，透传原串。
        """
        concepts: dict[str, list[list[str]]] = {}
        for source in sources:
            if self._is_english_source(source):
                concepts[source] = self._concepts_for(expanded)
            else:
                concepts[source] = [[expanded.query_string]]
        return concepts

    def _is_english_source(self, source: str) -> bool:
        return source in ENGLISH_SOURCES

    def _concepts_for(self, expanded: ExpandedQuery) -> list[list[str]]:
        """把原始研究方向按专业词典最长匹配切分，每个概念映射为英文等价词
        （未命中保持原样）。

        v0.17 Phase 2.5-B（Route-A）：direction != 综合 时追加英文方向组，
        最终 IEEE 查询形如 (topic group) AND (direction group)——
        Phase 2.5-A 真机验证该语法可用且方向区分明显；CNKI/Wanfang 等中文源
        不透传（保持原 query_string 语义）。
        """
        concepts: list[list[str]] = []
        segmenter = ConceptSegmenter(set(TERMS.keys()))
        for piece in segmenter.segment(expanded.original):
            en = TERMS.get(piece, {}).get("en")
            if en:
                concepts.append(list(en))
            else:
                concepts.append([piece])
        direction = expanded.expansion_direction or DIRECTION_GENERAL
        direction_terms = en_terms(direction)
        if direction_terms:
            concepts.append(list(direction_terms))
        return concepts
