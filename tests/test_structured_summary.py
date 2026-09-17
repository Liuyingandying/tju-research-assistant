#!/usr/bin/env python3
"""StructuredSummary 模型测试（v0.17 Phase 2.4-B-OFFLINE）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.models.summary import (
    SUMMARY_FIELDS,
    SUMMARY_FIELD_LABELS,
    StructuredSummary,
)


class TestStructuredSummary(unittest.TestCase):
    def test_defaults(self):
        s = StructuredSummary()
        for key in SUMMARY_FIELDS:
            self.assertEqual(getattr(s, key), "")
        self.assertEqual(s.source_basis, {})

    def test_to_dict_roundtrip(self):
        s = StructuredSummary(
            research_content="研究内容文本",
            core_technology="核心技术文本",
            main_results="主要成果文本",
            application_value="应用价值文本",
            source_basis={"research_content": "summary"},
        )
        d = s.to_dict()
        self.assertEqual(set(d), {"research_content", "core_technology",
                                  "main_results", "application_value",
                                  "source_basis"})
        restored = StructuredSummary.from_dict(d)
        self.assertEqual(restored, s)

    def test_from_dict_missing_fields_default_empty(self):
        s = StructuredSummary.from_dict({"research_content": "仅一项"})
        self.assertEqual(s.research_content, "仅一项")
        self.assertEqual(s.core_technology, "")
        self.assertEqual(s.source_basis, {})

    def test_from_dict_none_safe(self):
        self.assertEqual(StructuredSummary.from_dict(None), StructuredSummary())

    def test_items_order_and_labels(self):
        s = StructuredSummary(research_content="A", core_technology="B",
                              main_results="C", application_value="D")
        items = s.items()
        self.assertEqual([k for k, _, _ in items], list(SUMMARY_FIELDS))
        self.assertEqual([l for _, l, _ in items],
                         ["研究内容", "核心技术", "主要成果", "应用价值"])
        self.assertEqual([v for _, _, v in items], ["A", "B", "C", "D"])

    def test_labels_no_byline_style_fields(self):
        """字段名锁定四项，防止 schema 漂移。"""
        self.assertEqual(set(SUMMARY_FIELD_LABELS), set(SUMMARY_FIELDS))


if __name__ == "__main__":
    unittest.main(verbosity=2)