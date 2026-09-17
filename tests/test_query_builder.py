#!/usr/bin/env python3
"""正式测试：QueryBuilder 纯逻辑层（v0.8 Phase 2）。

覆盖：IEEE Boolean 生成（同义词 OR / 组间 AND / 多词短语引号）/
中文源透传 / 未知 source 安全回退 / 与 QueryTranslator 概念分组协作。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.services.query_builder import (
    BOOLEAN_SOURCES,
    CHINESE_SOURCES,
    QueryBuilder,
)
from tju_info_retrieval.services.query_expansion import QueryExpansionService
from tju_info_retrieval.services.query_translator import QueryTranslator


def _concepts(direction: str, source: str):
    """经 QueryTranslator 得到某数据源的概念分组。"""
    eq = QueryExpansionService().expand(direction)
    return QueryTranslator().translate_concepts(eq, [source])[source]


class TestIeeeBoolean(unittest.TestCase):
    def test_ieee_boolean_generation(self):
        # 用户示例：太赫兹 [terahertz, THz] + 通感一体化 [integrated..., ISAC]
        concepts = [["terahertz", "THz"], ["integrated sensing and communication", "ISAC"]]
        self.assertEqual(
            QueryBuilder().build("IEEE Xplore", concepts),
            '(terahertz OR THz) AND ("integrated sensing and communication" OR ISAC)',
        )

    def test_ieee_quotes_multiword_phrase(self):
        concepts = [["integrated sensing and communication"]]
        self.assertEqual(
            QueryBuilder().build("IEEE Xplore", concepts),
            '("integrated sensing and communication")',
        )

    def test_ieee_single_concept_no_and(self):
        concepts = [["terahertz", "THz"]]
        self.assertEqual(
            QueryBuilder().build("IEEE Xplore", concepts),
            "(terahertz OR THz)",
        )

    def test_ieee_single_word_not_quoted(self):
        concepts = [["terahertz"]]
        self.assertEqual(QueryBuilder().build("IEEE Xplore", concepts), "(terahertz)")


class TestChinesePassthrough(unittest.TestCase):
    def test_cnki_passthrough(self):
        concepts = [["太赫兹 通感一体化"]]
        self.assertEqual(
            QueryBuilder().build("CNKI", concepts),
            "太赫兹 通感一体化",
        )

    def test_wanfang_passthrough(self):
        concepts = [["太赫兹"]]
        self.assertEqual(QueryBuilder().build("万方", concepts), "太赫兹")


class TestUnknownSourceFallback(unittest.TestCase):
    def test_unknown_source_flattens_concepts(self):
        concepts = [["terahertz", "THz"], ["ISAC"]]
        self.assertEqual(
            QueryBuilder().build("Web of Science", concepts),
            "terahertz THz ISAC",
        )

    def test_unknown_source_empty(self):
        self.assertEqual(QueryBuilder().build("未知库", []), "")


class TestTranslatorBuilderIntegration(unittest.TestCase):
    def test_ieee_concepts_flow_to_boolean(self):
        # 中文输入 → translate_concepts → build → Boolean
        concepts = _concepts("太赫兹 通感一体化", "IEEE Xplore")
        self.assertEqual(
            concepts,
            [["terahertz", "THz"], ["integrated sensing and communication", "ISAC"]],
        )
        self.assertEqual(
            QueryBuilder().build("IEEE Xplore", concepts),
            '(terahertz OR THz) AND ("integrated sensing and communication" OR ISAC)',
        )

    def test_cnki_concepts_passthrough(self):
        concepts = _concepts("太赫兹 通感一体化", "CNKI")
        self.assertEqual(
            QueryBuilder().build("CNKI", concepts),
            "太赫兹 通感一体化",
        )

    def test_source_sets(self):
        self.assertIn("IEEE Xplore", BOOLEAN_SOURCES)
        self.assertIn("CNKI", CHINESE_SOURCES)
        self.assertIn("万方", CHINESE_SOURCES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
