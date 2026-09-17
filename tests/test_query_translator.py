#!/usr/bin/env python3
"""正式测试：QueryTranslator 纯逻辑层（v0.8 Phase 1）。

覆盖：中文数据源透传 / 英文数据源基础替换 / 未知词保持原样 /
多数据源返回不同检索串 / 与 QueryExpansion 组合。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.services.query_expansion import QueryExpansionService
from tju_info_retrieval.services.query_translator import (
    ENGLISH_SOURCES,
    QueryTranslator,
)
from tju_info_retrieval.services.terminology import TERMS


def _expand(direction: str):
    return QueryExpansionService().expand(direction)


class TestChinesePassthrough(unittest.TestCase):
    def test_cnki_keeps_original_query(self):
        eq = _expand("通感一体化")
        queries = QueryTranslator().translate(eq, ["CNKI"])
        self.assertEqual(queries["CNKI"], eq.query_string)

    def test_wanfang_keeps_original_query(self):
        eq = _expand("太赫兹")
        queries = QueryTranslator().translate(eq, ["万方"])
        self.assertEqual(queries["万方"], eq.query_string)


class TestEnglishConversion(unittest.TestCase):
    def test_ieee_translates_known_term(self):
        eq = _expand("通感一体化")
        queries = QueryTranslator().translate(eq, ["IEEE Xplore"])
        self.assertEqual(
            queries["IEEE Xplore"],
            "integrated sensing and communication ISAC",
        )

    def test_ieee_translates_multi_term_input(self):
        # 用户示例：太赫兹 通感一体化 → 英文等价词空格连接
        eq = _expand("太赫兹 通感一体化")
        queries = QueryTranslator().translate(eq, ["IEEE Xplore"])
        self.assertEqual(
            queries["IEEE Xplore"],
            "terahertz THz integrated sensing and communication ISAC",
        )

    def test_unknown_term_keeps_original(self):
        eq = _expand("量子计算")
        queries = QueryTranslator().translate(eq, ["IEEE Xplore"])
        self.assertEqual(queries["IEEE Xplore"], "量子计算")


class TestMultiSource(unittest.TestCase):
    def test_multi_source_returns_different_queries(self):
        eq = _expand("通感一体化")
        queries = QueryTranslator().translate(eq, ["CNKI", "IEEE Xplore"])
        self.assertEqual(queries["CNKI"], "通感一体化")
        self.assertEqual(
            queries["IEEE Xplore"],
            "integrated sensing and communication ISAC",
        )
        self.assertNotEqual(queries["CNKI"], queries["IEEE Xplore"])

    def test_english_source_set_contains_ieee(self):
        self.assertIn("IEEE Xplore", ENGLISH_SOURCES)


class TestDictionaryInterface(unittest.TestCase):
    def test_terms_dict_has_expected_entries(self):
        self.assertIn("太赫兹", TERMS)
        self.assertIn("通感一体化", TERMS)
        self.assertEqual(TERMS["太赫兹"]["en"], ["terahertz", "THz"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestIeeeDirectionGroup(unittest.TestCase):
    """v0.17 Phase 2.5-B：IEEE Route-A——direction filters 进入英文查询。"""

    def _ieee_concepts(self, direction):
        from tju_info_retrieval.services.query_expansion import (
            QueryExpansionService)
        from tju_info_retrieval.services.query_translator import QueryTranslator
        expanded = QueryExpansionService().expand("太赫兹", direction)
        return QueryTranslator().translate_concepts(expanded, ["IEEE Xplore"])[
            "IEEE Xplore"]

    def _ieee_query(self, direction):
        from tju_info_retrieval.services.query_builder import QueryBuilder
        concepts = self._ieee_concepts(direction)
        return QueryBuilder().build("IEEE Xplore", concepts)

    def test_general_no_direction_group(self):
        q = self._ieee_query("综合")
        assert "review" not in q.lower() and "theory" not in q.lower()
        assert "AND" not in q

    def test_review_direction_group(self):
        q = self._ieee_query("综述")
        assert "review" in q.lower() and "survey" in q.lower()

    def test_application_direction_group(self):
        q = self._ieee_query("应用")
        assert "application" in q.lower()

    def test_theory_direction_group(self):
        q = self._ieee_query("理论")
        assert "theory" in q.lower() or "theoretical" in q.lower() \
            or "mechanism" in q.lower()

    def test_direction_changes_query_general(self):
        """Phase 2.5-B 前 direction 会丢失；现在必须真实改变 query。"""
        assert self._ieee_query("综述") != self._ieee_query("综合")

    def test_topic_kept_with_direction(self):
        q = self._ieee_query("综述")
        assert "terahertz" in q.lower()  # topic group 仍在（direction 不替代 topic）

    def test_chinese_source_unchanged(self):
        from tju_info_retrieval.services.query_expansion import (
            QueryExpansionService)
        from tju_info_retrieval.services.query_translator import QueryTranslator
        expanded = QueryExpansionService().expand("太赫兹", "综述")
        concepts = QueryTranslator().translate_concepts(expanded, ["CNKI"])["CNKI"]
        assert concepts == [[expanded.query_string]]
