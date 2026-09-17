#!/usr/bin/env python3
"""v0.10 Phase 1 测试：文献标签系统（offscreen）。

覆盖：标签显示、标签保存、多标签、空标签、重新加载保持。
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

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from tju_info_retrieval.models.library import LibraryRecord
from tju_info_retrieval.services.library_store import LibraryStore
from tju_info_retrieval.ui.library_window import LibraryWindow


class TestLibraryTags(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.store = LibraryStore(Path(self.tmp.name))

    def tearDown(self):
        try:
            Path(self.tmp.name).unlink(missing_ok=True)
        except OSError:
            pass

    def _make_record(self, title="Tag paper", tags=None, detail_url="https://x"):
        return LibraryRecord(
            title=title,
            authors=["A"],
            year="2025",
            source="J",
            database="CNKI",
            detail_url=detail_url,
            tags=list(tags or []),
        )

    def _open_window(self):
        win = LibraryWindow(self.store, parent=None)
        return win

    # ---------- 标签显示 ----------

    def test_tags_displayed_joined(self):
        self.store.save(self._make_record(tags=["THz", "6G", "ISAC"]))
        win = self._open_window()
        self.assertEqual(win.table.rowCount(), 1)
        self.assertEqual(win.table.item(0, 7).text(), "THz,6G,ISAC")
        win.close()

    def test_empty_tags_display_blank(self):
        self.store.save(self._make_record(tags=[]))
        win = self._open_window()
        self.assertEqual(win.table.item(0, 7).text(), "")
        win.close()

    # ---------- 标签保存 ----------

    def test_edit_tags_saves_to_record(self):
        self.store.save(self._make_record(title="T", tags=["old"]))
        win = self._open_window()
        win.table.selectRow(0)
        with mock.patch.object(
            QInputDialog, "getText", return_value=("THz,6G,重点阅读", True)
        ):
            win.btn_edit_tags.click()
        records = self.store.load_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].tags, ["THz", "6G", "重点阅读"])
        # 表格立即刷新
        self.assertEqual(win.table.item(0, 7).text(), "THz,6G,重点阅读")
        win.close()

    def test_edit_tags_cancel_keeps_original(self):
        self.store.save(self._make_record(title="T", tags=["keep"]))
        win = self._open_window()
        win.table.selectRow(0)
        with mock.patch.object(
            QInputDialog, "getText", return_value=("changed", False)
        ):
            win.btn_edit_tags.click()
        records = self.store.load_all()
        self.assertEqual(records[0].tags, ["keep"])
        win.close()

    def test_edit_tags_multiple_formats(self):
        # 中英文逗号、顿号、分号混合输入
        self.store.save(self._make_record(title="T"))
        win = self._open_window()
        win.table.selectRow(0)
        with mock.patch.object(
            QInputDialog, "getText", return_value=("THz, 6G、ISAC；重点阅读", True)
        ):
            win.btn_edit_tags.click()
        records = self.store.load_all()
        self.assertEqual(records[0].tags, ["THz", "6G", "ISAC", "重点阅读"])
        win.close()

    def test_edit_tags_blank_clears(self):
        self.store.save(self._make_record(title="T", tags=["THz", "6G"]))
        win = self._open_window()
        win.table.selectRow(0)
        with mock.patch.object(
            QInputDialog, "getText", return_value=("", True)
        ):
            win.btn_edit_tags.click()
        records = self.store.load_all()
        self.assertEqual(records[0].tags, [])
        self.assertEqual(win.table.item(0, 7).text(), "")
        win.close()

    def test_edit_tags_deduplicates(self):
        self.store.save(self._make_record(title="T"))
        win = self._open_window()
        win.table.selectRow(0)
        with mock.patch.object(
            QInputDialog, "getText", return_value=("THz,THz,6G", True)
        ):
            win.btn_edit_tags.click()
        records = self.store.load_all()
        self.assertEqual(records[0].tags, ["THz", "6G"])
        win.close()

    # ---------- 重新加载保持 ----------

    def test_tags_persist_after_reload(self):
        self.store.save(self._make_record(title="T", tags=["THz"]))
        win = self._open_window()
        win.table.selectRow(0)
        with mock.patch.object(
            QInputDialog, "getText", return_value=("THz,6G,ISAC", True)
        ):
            win.btn_edit_tags.click()
        win.close()

        # 重新打开窗口：标签仍保留
        win2 = self._open_window()
        self.assertEqual(win2.table.item(0, 7).text(), "THz,6G,ISAC")
        win2.close()

    # ---------- 边界 ----------

    def test_edit_tags_no_selection_safe(self):
        self.store.save(self._make_record(title="T"))
        win = self._open_window()
        with mock.patch.object(QMessageBox, "information", return_value=None) as info:
            win.btn_edit_tags.click()
        info.assert_called_once()
        win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
