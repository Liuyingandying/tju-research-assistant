#!/usr/bin/env python3
"""v0.9.3 Phase 1 测试：LibraryRecord 模型（offscreen）。

覆盖：SearchResult dict 转换、to_dict/from_dict 往返、默认 tags/note、时间字段。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest
from datetime import datetime

from tju_info_retrieval.models.library import LibraryRecord


class TestLibraryRecord(unittest.TestCase):
    # ---------- 默认值 ----------

    def test_default_tags_is_empty_list(self):
        rec = LibraryRecord()
        self.assertEqual(rec.tags, [])

    def test_default_note_is_empty_string(self):
        rec = LibraryRecord()
        self.assertEqual(rec.note, "")

    def test_default_reading_status(self):
        rec = LibraryRecord()
        self.assertEqual(rec.reading_status, "unread")

    def test_default_id_is_hex(self):
        rec = LibraryRecord()
        self.assertEqual(len(rec.id), 32)
        int(rec.id, 16)  # 不抛异常即合法 hex

    # ---------- from_search_result ----------

    def test_from_search_result_basic(self):
        data = {
            "title": "A survey on terahertz communications",
            "authors": ["Zhi Chen", "Li Wang"],
            "year": "2019",
            "source": "China Communications",
            "database": "IEEE Xplore",
            "venue": "IEEE Communications Surveys",
            "detail_url": "https://ieeexplore.ieee.org/document/8663550/",
            "doi": "10.1109/TCOMM.2019.1234567",
        }
        rec = LibraryRecord.from_search_result(data)
        self.assertEqual(rec.title, "A survey on terahertz communications")
        self.assertEqual(rec.authors, ["Zhi Chen", "Li Wang"])
        self.assertEqual(rec.year, "2019")
        self.assertEqual(rec.source, "China Communications")
        self.assertEqual(rec.database, "IEEE Xplore")
        self.assertEqual(rec.venue, "IEEE Communications Surveys")
        self.assertEqual(rec.detail_url, "https://ieeexplore.ieee.org/document/8663550/")
        self.assertEqual(rec.doi, "10.1109/TCOMM.2019.1234567")
        self.assertTrue(rec.saved_at)  # 非空 ISO 时间

    def test_from_search_result_missing_fields(self):
        data = {"title": "Minimal paper"}
        rec = LibraryRecord.from_search_result(data)
        self.assertEqual(rec.title, "Minimal paper")
        self.assertEqual(rec.authors, [])
        self.assertIsNone(rec.year)
        self.assertIsNone(rec.source)
        self.assertEqual(rec.database, "")
        self.assertIsNone(rec.venue)
        self.assertIsNone(rec.detail_url)
        self.assertIsNone(rec.doi)
        self.assertEqual(rec.tags, [])
        self.assertEqual(rec.note, "")

    def test_from_search_result_strips_whitespace(self):
        data = {"title": "  spaced title  ", "authors": ["  name  "]}
        rec = LibraryRecord.from_search_result(data)
        self.assertEqual(rec.title, "spaced title")
        self.assertEqual(rec.authors, ["name"])

    def test_from_search_result_empty_authors_list(self):
        data = {"title": "T", "authors": []}
        rec = LibraryRecord.from_search_result(data)
        self.assertEqual(rec.authors, [])

    def test_from_search_result_none_authors(self):
        data = {"title": "T", "authors": None}
        rec = LibraryRecord.from_search_result(data)
        self.assertEqual(rec.authors, [])

    # ---------- to_dict / from_dict roundtrip ----------

    def test_roundtrip_full_record(self):
        original = LibraryRecord(
            id="abc123",
            title="Full paper",
            authors=["A", "B"],
            year="2025",
            source="Test Journal",
            database="CNKI",
            venue="Test Venue",
            abstract="An abstract",
            detail_url="https://example.com/paper",
            doi="10.1234/test",
            tags=["tag1", "tag2"],
            note="My note",
            saved_at="2026-01-01T00:00:00+00:00",
            reading_status="reading",
        )
        d = original.to_dict()
        restored = LibraryRecord.from_dict(d)
        self.assertEqual(restored.id, "abc123")
        self.assertEqual(restored.title, "Full paper")
        self.assertEqual(restored.authors, ["A", "B"])
        self.assertEqual(restored.year, "2025")
        self.assertEqual(restored.source, "Test Journal")
        self.assertEqual(restored.database, "CNKI")
        self.assertEqual(restored.venue, "Test Venue")
        self.assertEqual(restored.abstract, "An abstract")
        self.assertEqual(restored.detail_url, "https://example.com/paper")
        self.assertEqual(restored.doi, "10.1234/test")
        self.assertEqual(restored.tags, ["tag1", "tag2"])
        self.assertEqual(restored.note, "My note")
        self.assertEqual(restored.saved_at, "2026-01-01T00:00:00+00:00")
        self.assertEqual(restored.reading_status, "reading")

    def test_roundtrip_minimal_record(self):
        original = LibraryRecord(title="Minimal")
        d = original.to_dict()
        restored = LibraryRecord.from_dict(d)
        self.assertEqual(restored.title, "Minimal")
        self.assertEqual(restored.authors, [])
        self.assertIsNone(restored.year)
        self.assertEqual(restored.tags, [])
        self.assertEqual(restored.note, "")

    def test_from_dict_preserves_explicit_id(self):
        d = {"id": "custom-id", "title": "T"}
        rec = LibraryRecord.from_dict(d)
        self.assertEqual(rec.id, "custom-id")

    def test_from_dict_generates_id_when_missing(self):
        d = {"title": "T"}
        rec = LibraryRecord.from_dict(d)
        self.assertEqual(len(rec.id), 32)

    def test_from_dict_uses_defaults_for_missing_user_fields(self):
        d = {"id": "x", "title": "T", "tags": None, "note": None, "reading_status": None}
        rec = LibraryRecord.from_dict(d)
        self.assertEqual(rec.tags, [])
        self.assertEqual(rec.note, "")
        self.assertEqual(rec.reading_status, "unread")


if __name__ == "__main__":
    unittest.main(verbosity=2)
