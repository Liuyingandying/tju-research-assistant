#!/usr/bin/env python3
"""v0.9.3 Phase 1.5 测试：GUI 收藏功能（offscreen）。

覆盖：按钮存在、初始禁用、搜索后启用、点击保存记录、状态栏反馈、
无选中行安全失败、保存数据内容正确。
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from PySide6.QtWidgets import QApplication, QMessageBox

from tju_info_retrieval.models.library import LibraryRecord
from tju_info_retrieval.services.library_store import LibraryStore
from tju_info_retrieval.ui.main_window import MainWindow


class TestFavoriteButton(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # 每个测试使用独立临时 LibraryStore，避免测试间互相污染
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.window = MainWindow()
        self.window._library_store = LibraryStore(Path(self.tmp.name))

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        try:
            Path(self.tmp.name).unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _row(title="Test paper", detail_url=None):
        return {
            "rank": 1,
            "title": title,
            "authors": ["Author A"],
            "source": "Test Journal",
            "year": "2025",
            "document_type": "期刊",
            "database": "CNKI",
            "detail_url": detail_url,
            "venue": None,
            "doi": None,
        }

    # ---------- 按钮存在 ----------

    def test_favorite_button_exists(self):
        self.assertEqual(self.window.btn_favorite.text(), "收藏当前论文")
        self.assertIsNotNone(self.window.btn_favorite)

    def test_favorite_button_initially_disabled(self):
        self.assertFalse(self.window.btn_favorite.isEnabled())

    # ---------- 搜索后启用 ----------

    def test_favorite_enabled_after_search_done(self):
        self.window._on_search_done([self._row()])
        self.assertTrue(self.window.btn_favorite.isEnabled())

    def test_favorite_disabled_after_search_failed(self):
        with mock.patch.object(QMessageBox, "warning", return_value=None):
            self.window._on_search_failed("测试错误")
        self.assertFalse(self.window.btn_favorite.isEnabled())

    def test_favorite_enabled_after_demo(self):
        self.window._on_load_demo()
        self.assertTrue(self.window.btn_favorite.isEnabled())

    # ---------- 按钮连接 ----------

    def test_favorite_button_connected(self):
        self.window.btn_favorite.setEnabled(True)
        with mock.patch.object(self.window, "_on_favorite") as m:
            self.window.btn_favorite.click()
            m.assert_called_once()

    # ---------- 点击保存记录 ----------

    def test_click_saves_record(self):
        self.window._on_search_done([self._row()])
        self.window.table.selectRow(0)
        with mock.patch.object(self.window, "open_detail_requested") as m:
            self.window._on_favorite()
        # 验证 LibraryStore.save 被调用（通过检查 store 中的记录数）
        records = self.window._library_store.load_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Test paper")
        self.assertEqual(records[0].database, "CNKI")

    # ---------- 状态栏反馈 ----------

    def test_click_shows_status_message(self):
        self.window._on_search_done([self._row()])
        self.window.table.selectRow(0)
        self.window._on_favorite()
        self.assertIn("已收藏", self.window.label_status.text())
        self.assertIn("Test paper", self.window.label_status.text())

    # ---------- 无选中行安全失败 ----------

    def test_no_selection_no_save(self):
        self.window._on_search_done([self._row()])
        # 不 selectRow，直接点击
        with mock.patch.object(QMessageBox, "information", return_value=None) as info:
            self.window._on_favorite()
        self.assertIn("请先在表格中选择一条结果", info.call_args.args[2])
        records = self.window._library_store.load_all()
        self.assertEqual(len(records), 0)

    # ---------- 保存数据内容正确 ----------

    def test_saved_record_has_correct_fields(self):
        self.window._on_search_done([self._row(
            title="Deep THz study",
            detail_url="https://ieeexplore.ieee.org/document/12345/",
        )])
        self.window.table.selectRow(0)
        self.window._on_favorite()
        records = self.window._library_store.load_all()
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.title, "Deep THz study")
        self.assertEqual(rec.authors, ["Author A"])
        self.assertEqual(rec.year, "2025")
        self.assertEqual(rec.source, "Test Journal")
        self.assertEqual(rec.database, "CNKI")
        self.assertEqual(rec.detail_url, "https://ieeexplore.ieee.org/document/12345/")
        self.assertEqual(rec.tags, [])
        self.assertEqual(rec.note, "")
        self.assertEqual(rec.reading_status, "unread")
        self.assertTrue(rec.saved_at)  # 非空 ISO 时间
        self.assertTrue(rec.id)  # 非空 id

    def test_saved_record_has_uuid_id(self):
        self.window._on_search_done([self._row()])
        self.window.table.selectRow(0)
        self.window._on_favorite()
        records = self.window._library_store.load_all()
        self.assertEqual(len(records[0].id), 32)
        int(records[0].id, 16)  # 合法 hex


if __name__ == "__main__":
    unittest.main(verbosity=2)
