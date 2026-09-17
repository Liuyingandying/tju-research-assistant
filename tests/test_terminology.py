#!/usr/bin/env python3
"""正式测试：Domain Terminology 领域词典（v0.8.1 Phase 2）。

覆盖：太赫兹通信完整英文转换 / 无线通信优先匹配 / 毫米波通信 /
未知词 fallback / terminology 加载与校验。
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
from tju_info_retrieval.services.terminology import TERMS, validate


def _ieee_concepts(direction: str) -> list[list[str]]:
    eq = QueryExpansionService().expand(direction)
    return QueryTranslator().translate_concepts(eq, ["IEEE Xplore"])["IEEE Xplore"]


class TestTerminologyDictionary(unittest.TestCase):
    def test_required_terms_present(self):
        for term in ("太赫兹", "通感一体化", "通信", "无线通信", "毫米波", "6G",
                     "感知", "雷达", "阵列", "天线", "波束形成"):
            self.assertIn(term, TERMS)

    def test_communication_mapping(self):
        self.assertEqual(TERMS["通信"]["en"], ["communication"])

    def test_wireless_communication_mapping(self):
        self.assertEqual(
            TERMS["无线通信"]["en"],
            ["wireless communication", "wireless"],
        )

    def test_validate_clean(self):
        self.assertEqual(validate(), [])

    def test_validate_detects_missing_en(self):
        problems = validate({"太赫兹": {}})
        self.assertTrue(any("缺少 en" in p for p in problems))

    def test_validate_detects_empty_key(self):
        problems = validate({"": {"en": ["x"]}})
        self.assertTrue(any("空概念" in p for p in problems))


class TestFullEnglishConversion(unittest.TestCase):
    def test_terahertz_communication_full_conversion(self):
        # 问题解决：太赫兹通信 现在 通信 也翻译为英文
        concepts = _ieee_concepts("太赫兹通信")
        self.assertEqual(
            concepts,
            [["terahertz", "THz"], ["communication"]],
        )

    def test_millimeter_wave_communication(self):
        # 毫米波通信：毫米波 + 通信 均翻译
        concepts = _ieee_concepts("毫米波通信")
        self.assertEqual(
            concepts,
            [["millimeter wave", "mmWave"], ["communication"]],
        )

    def test_unknown_word_fallback(self):
        # 未知词整段保留（安全回退）
        concepts = _ieee_concepts("量子计算")
        self.assertEqual(concepts, [["量子计算"]])


class TestLongestMatchPriority(unittest.TestCase):
    def test_wireless_communication_priority(self):
        # 无线通信 优先于 通信（最长匹配）
        seg = ConceptSegmenter(set(TERMS.keys()))
        self.assertEqual(seg.segment("无线通信"), ["无线通信"])

    def test_millimeter_wave_priority(self):
        # 毫米波 优先于 波（波 未入典，但 毫米波 整词命中）
        seg = ConceptSegmenter(set(TERMS.keys()))
        self.assertEqual(seg.segment("毫米波"), ["毫米波"])

    def test_wireless_communication_conversion(self):
        concepts = _ieee_concepts("无线通信")
        self.assertEqual(
            concepts,
            [["wireless communication", "wireless"]],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
