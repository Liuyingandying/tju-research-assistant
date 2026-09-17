#!/usr/bin/env python3
"""正式测试：QueryExpansionService 纯逻辑层。

覆盖：同义词扩展 / 未命中保持原词 / 理论·应用·综述方向限定 / 综合不扩展。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.services.query_expansion import (
    DIRECTION_FILTERS,
    QueryExpansionService,
)


class TestSynonymExpansion(unittest.TestCase):
    def test_synonym_hit_expands_terms(self):
        eq = QueryExpansionService().expand("太赫兹")
        self.assertIn("太赫兹", eq.terms)
        self.assertIn("THz", eq.terms)
        self.assertIn("太赫兹波", eq.terms)
        self.assertGreater(len(eq.terms), 1)

    def test_synonym_hit_builds_or_query(self):
        eq = QueryExpansionService().expand("太赫兹")
        self.assertTrue(eq.query_string.startswith("("))
        self.assertIn("OR", eq.query_string)
        self.assertIn("太赫兹", eq.query_string)
        self.assertIn("THz", eq.query_string)

    def test_miss_keeps_original(self):
        eq = QueryExpansionService().expand("量子计算")
        self.assertEqual(eq.terms, ["量子计算"])
        self.assertEqual(eq.query_string, "量子计算")
        self.assertNotIn("OR", eq.query_string)


class TestDirectionFilters(unittest.TestCase):
    def test_theory_filter(self):
        eq = QueryExpansionService().expand("太赫兹", "理论")
        self.assertEqual(eq.filters, DIRECTION_FILTERS["理论"])
        self.assertIn("AND", eq.query_string)
        self.assertIn("理论", eq.query_string)

    def test_application_filter(self):
        eq = QueryExpansionService().expand("太赫兹", "应用")
        self.assertEqual(eq.filters, DIRECTION_FILTERS["应用"])
        self.assertIn("AND", eq.query_string)

    def test_review_filter(self):
        eq = QueryExpansionService().expand("太赫兹", "综述")
        self.assertEqual(eq.filters, DIRECTION_FILTERS["综述"])
        self.assertIn("AND", eq.query_string)

    def test_general_no_filter(self):
        eq = QueryExpansionService().expand("太赫兹", "综合")
        self.assertEqual(eq.filters, [])
        self.assertNotIn("AND", eq.query_string)

    def test_unknown_direction_treated_as_general(self):
        eq = QueryExpansionService().expand("太赫兹", "未知方向")
        self.assertEqual(eq.filters, [])
        self.assertNotIn("AND", eq.query_string)


class TestEdgeCases(unittest.TestCase):
    def test_empty_direction_raises(self):
        with self.assertRaises(ValueError):
            QueryExpansionService().expand("   ")

    def test_whitespace_stripped(self):
        eq = QueryExpansionService().expand("  太赫兹  ", "综合")
        self.assertEqual(eq.original, "太赫兹")

    def test_default_direction_is_general(self):
        eq = QueryExpansionService().expand("太赫兹")
        self.assertEqual(eq.expansion_direction, "综合")
        self.assertEqual(eq.filters, [])

    def test_notes_present(self):
        eq = QueryExpansionService().expand("太赫兹", "理论")
        self.assertTrue(eq.notes)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestReviewShortGroup(unittest.TestCase):
    """v0.17 Phase 2.5-B：综述必须收敛为稳定短组（修复 CNKI 0 条 bug）。"""

    def test_review_filters_exclude_poison_terms(self):
        from tju_info_retrieval.services.query_expansion import (
            DIRECTION_FILTERS, QueryExpansionService)
        filters = DIRECTION_FILTERS["综述"]
        assert filters == ["综述", "进展"]
        assert not {"研究现状", "展望", "回顾"} & set(filters)
        eq = QueryExpansionService().expand("太赫兹", "综述")
        assert eq.filters == ["综述", "进展"]
        assert "研究现状" not in eq.query_string
        assert "展望" not in eq.query_string and "回顾" not in eq.query_string
