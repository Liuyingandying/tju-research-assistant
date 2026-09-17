#!/usr/bin/env python3
"""v0.9.3 Phase 1 测试：LibraryStore JSON 存储层（offscreen）。

覆盖：首次创建、保存读取、删除、重复判断、损坏 JSON 恢复。
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.models.library import LibraryRecord
from tju_info_retrieval.services.library_store import LibraryStore


class TestLibraryStore(unittest.TestCase):
    def setUp(self):
        # 每个测试使用独立临时文件，避免互相干扰
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.store = LibraryStore(Path(self.tmp.name))

    def tearDown(self):
        try:
            Path(self.tmp.name).unlink(missing_ok=True)
        except OSError:
            pass

    # ---------- 首次创建 ----------

    def test_load_all_returns_empty_when_no_file(self):
        # 删除文件确保不存在
        Path(self.tmp.name).unlink(missing_ok=True)
        store = LibraryStore(Path(self.tmp.name))
        self.assertEqual(store.load_all(), [])

    def test_load_all_returns_empty_when_file_empty(self):
        # 创建空文件
        Path(self.tmp.name).touch()
        self.assertEqual(self.store.load_all(), [])

    # ---------- 保存与读取 ----------

    def test_save_and_load_all(self):
        rec = LibraryRecord.from_search_result({
            "title": "Test paper",
            "authors": ["Author A"],
            "database": "CNKI",
        })
        self.store.save(rec)
        records = self.store.load_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Test paper")
        self.assertEqual(records[0].id, rec.id)

    def test_save_multiple_records(self):
        for i in range(3):
            rec = LibraryRecord(title=f"Paper {i}", database="CNKI")
            self.store.save(rec)
        records = self.store.load_all()
        self.assertEqual(len(records), 3)

    def test_save_overwrites_existing_id(self):
        rec1 = LibraryRecord(id="same-id", title="Original")
        self.store.save(rec1)
        rec2 = LibraryRecord(id="same-id", title="Updated")
        self.store.save(rec2)
        records = self.store.load_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Updated")

    # ---------- 删除 ----------

    def test_remove_existing_record(self):
        rec = LibraryRecord(title="To remove")
        self.store.save(rec)
        self.assertTrue(self.store.remove(rec.id))
        self.assertEqual(self.store.load_all(), [])

    def test_remove_nonexistent_record(self):
        self.assertFalse(self.store.remove("nonexistent-id"))

    def test_remove_does_not_affect_other_records(self):
        rec1 = LibraryRecord(id="keep", title="Keep")
        rec2 = LibraryRecord(id="remove", title="Remove")
        self.store.save(rec1)
        self.store.save(rec2)
        self.store.remove("remove")
        records = self.store.load_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Keep")

    # ---------- 重复判断 ----------

    def test_exists_true(self):
        rec = LibraryRecord(id="check-id", title="T")
        self.store.save(rec)
        self.assertTrue(self.store.exists("check-id"))

    def test_exists_false(self):
        self.assertFalse(self.store.exists("nonexistent-id"))

    # ---------- 损坏 JSON 恢复 ----------

    def test_corrupted_json_returns_empty(self):
        Path(self.tmp.name).write_text("not valid json {{{", encoding="utf-8")
        self.assertEqual(self.store.load_all(), [])

    def test_json_array_of_strings_returns_empty(self):
        Path(self.tmp.name).write_text('["a", "b"]', encoding="utf-8")
        self.assertEqual(self.store.load_all(), [])

    def test_save_after_corrupted_json_recovers(self):
        # 先写损坏数据
        Path(self.tmp.name).write_text("corrupted", encoding="utf-8")
        rec = LibraryRecord(title="Recovered")
        self.store.save(rec)
        records = self.store.load_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Recovered")

    # ---------- 导出 ----------

    def test_export_json(self):
        rec = LibraryRecord(title="Export me")
        self.store.save(rec)
        out = Path(tempfile.mkdtemp()) / "export.json"
        self.store.export_json(out)
        self.assertTrue(out.exists())
        import json
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["title"], "Export me")

    def test_export_csv(self):
        rec = LibraryRecord(title="CSV export", authors=["A", "B"], tags=["t1"])
        self.store.save(rec)
        out = Path(tempfile.mkdtemp()) / "export.csv"
        self.store.export_csv(out)
        self.assertTrue(out.exists())
        text = out.read_text(encoding="utf-8-sig")
        self.assertIn("CSV export", text)
        self.assertIn("A；B", text)
        self.assertIn("t1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
