#!/usr/bin/env python3
"""正式测试：ConceptSegmenter 中文科研关键词切分（v0.8.1 Phase 1）。

覆盖：连续中文科研词切分 / 多概念组合 / 未知词 fallback / 空输入 /
与 QueryTranslator 集成（不影响已有行为）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.services.concept_segmenter import ConceptSegmenter
from tju_info_retrieval.services.query_expansion import QueryExpansionService
from tju_info_retrieval.services.query_translator import QueryTranslator
from tju_info_retrieval.services.terminology import TERMS


def _segmenter() -> ConceptSegmenter:
    """以项目专业词典（TERMS 键）构造切分器。"""
    return ConceptSegmenter(set(TERMS.keys()))


class TestSegmenter(unittest.TestCase):
    def test_continuous_chinese_compound(self):
        # 问题 1：无空格复合词正确切分
        self.assertEqual(
            _segmenter().segment("太赫兹通感一体化"),
            ["太赫兹", "通感一体化"],
        )

    def test_known_plus_unknown(self):
        # 问题 2：太赫兹 切出，通信 未命中保留为一段
        self.assertEqual(
            _segmenter().segment("太赫兹通信"),
            ["太赫兹", "通信"],
        )

    def test_unknown_word_fallback(self):
        # 未知词整段保留（安全回退）
        self.assertEqual(
            _segmenter().segment("量子计算"),
            ["量子计算"],
        )

    def test_empty_input(self):
        self.assertEqual(_segmenter().segment(""), [])

    def test_whitespace_separated(self):
        # 有空格输入与最长匹配结果一致（向后兼容）
        self.assertEqual(
            _segmenter().segment("太赫兹 通感一体化"),
            ["太赫兹", "通感一体化"],
        )

    def test_single_concept(self):
        self.assertEqual(_segmenter().segment("通感一体化"), ["通感一体化"])

    def test_repeated_concept(self):
        self.assertEqual(
            _segmenter().segment("太赫兹太赫兹"),
            ["太赫兹", "太赫兹"],
        )

    def test_whitespace_only(self):
        self.assertEqual(_segmenter().segment("   "), [])

    def test_custom_dictionary(self):
        seg = ConceptSegmenter({"太赫兹", "通信"})
        self.assertEqual(seg.segment("太赫兹通信"), ["太赫兹", "通信"])

    def test_empty_dictionary_keeps_whole(self):
        # 空词典 → 整段保留
        self.assertEqual(ConceptSegmenter(set()).segment("太赫兹通感一体化"), ["太赫兹通感一体化"])


class TestTranslatorIntegration(unittest.TestCase):
    def test_no_space_compound_translates(self):
        # 经 ConceptSegmenter 后，无空格复合词正确产出概念分组
        eq = QueryExpansionService().expand("太赫兹通感一体化")
        concepts = QueryTranslator().translate_concepts(eq, ["IEEE Xplore"])["IEEE Xplore"]
        self.assertEqual(
            concepts,
            [["terahertz", "THz"], ["integrated sensing and communication", "ISAC"]],
        )

    def test_known_plus_unknown_keeps_chinese(self):
        # 太赫兹 翻译，量子计算 未命中保留中文
        eq = QueryExpansionService().expand("太赫兹量子计算")
        concepts = QueryTranslator().translate_concepts(eq, ["IEEE Xplore"])["IEEE Xplore"]
        self.assertEqual(
            concepts,
            [["terahertz", "THz"], ["量子计算"]],
        )

    def test_spaced_input_unchanged(self):
        # 有空格输入行为不变（向后兼容）
        eq = QueryExpansionService().expand("太赫兹 通感一体化")
        concepts = QueryTranslator().translate_concepts(eq, ["IEEE Xplore"])["IEEE Xplore"]
        self.assertEqual(
            concepts,
            [["terahertz", "THz"], ["integrated sensing and communication", "ISAC"]],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
