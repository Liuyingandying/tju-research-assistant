"""Query Builder 纯逻辑层（v0.8 Phase 2）。

根据目标数据库语法，把 QueryTranslator 输出的概念分组组装为最终检索串：
  - IEEE Xplore：Boolean Query（同义词组内 OR、组间 AND、多词短语加双引号）。
  - CNKI / 万方：透传中文原串（保持现有行为）。
  - 未知数据源：安全回退（空格连接所有概念词，不报语法错误）。

纯逻辑、确定性、无网络、无 GUI 依赖。
"""

from __future__ import annotations

# 需要生成 Boolean Query 的数据源
BOOLEAN_SOURCES: set[str] = {"IEEE Xplore"}
# 透传中文原串的数据源
CHINESE_SOURCES: set[str] = {"CNKI", "万方"}


class QueryBuilder:
    """按数据源把概念分组组装为数据库专用检索串（纯逻辑，可单测）。"""

    def build(self, source: str, translated_query) -> str:
        """为指定数据源生成最终检索串。

        translated_query: QueryTranslator.translate_concepts 输出的概念分组，
        形如 [[syn1, syn2], [syn3, syn4], ...]（每个内层列表为一个概念的同义词组）。
        """
        if source in BOOLEAN_SOURCES:
            return self._build_boolean(translated_query)
        if source in CHINESE_SOURCES:
            return self._build_chinese(translated_query)
        return self._fallback(translated_query)

    @staticmethod
    def _build_boolean(concepts) -> str:
        """Boolean Query：同义词组内 OR、组间 AND、多词短语加引号。"""
        groups = []
        for group in concepts:
            terms = [QueryBuilder._quote(term) for term in group]
            groups.append("(" + " OR ".join(terms) + ")")
        return " AND ".join(groups)

    @staticmethod
    def _quote(term: str) -> str:
        """多词短语加双引号，单词不加。"""
        if " " in term:
            return f'"{term}"'
        return term

    @staticmethod
    def _build_chinese(translated_query) -> str:
        """中文源：透传原串（translate_concepts 返回 [[query_string]]）。"""
        if translated_query and isinstance(translated_query[0], list):
            return translated_query[0][0]
        return ""

    @staticmethod
    def _fallback(translated_query) -> str:
        """未知源安全回退：空格连接所有概念词。"""
        terms: list[str] = []
        for group in translated_query:
            if isinstance(group, list):
                terms.extend(group)
            else:
                terms.append(group)
        return " ".join(terms)
