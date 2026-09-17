#!/usr/bin/env python3
"""v0.10 Phase 2 测试：文献阅读状态系统（offscreen）。

覆盖：默认状态显示、状态展示、修改状态保存、取消不修改、
重新打开窗口保持、未选择安全失败。

v0.18 Phase 2.9-B 适配：
- reading_status 枚举统一为 unread/skimmed/reading/finished
  （中文显示 未读/已泛读/阅读中/已完成，§二十）；
- 表格新增分析列后，阅读状态列移位至第 7 列（索引 6）。
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

from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog

from tju_info_retrieval.models.library import LibraryRecord
from tju_info_retrieval.services.library_store import LibraryStore
from tju_info_retrieval.ui.library_window import LibraryWindow


class TestLibraryStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.store = LibraryStore(Path(self.tmp.name))

    def tearDown(self):
        self.store = None
        try:
            Path(self.tmp.name).unlink(missing_ok=True)
        except OSError:
            pass

    def _make_record(self, title="Test paper", detail_url=None,
                     reading_status="unread"):
        return LibraryRecord(
            title=title,
            authors=["Author A"],
            year="2025",
            source="Test Journal",
            database="CNKI",
            detail_url=detail_url,
            reading_status=reading_status,
        )

    @staticmethod
    def _status_cell(win, row=0):
        return win.table.item(row, 6).text()

    # ---------- 1. 默认状态显示 ----------

    def test_default_status_shows_unread(self):
        """新建记录的默认状态为 unread（显示"未读"）。"""
        rec = self._make_record()
        self.assertEqual(rec.reading_status, "unread")
        self.store.save(rec)
        win = LibraryWindow(self.store, parent=None)
        self.assertEqual(self._status_cell(win), "未读")
        win.close()

    # ---------- 2. 三种状态展示 ----------

    def test_four_status_values_display(self):
        """四种状态值在表格中正确显示（§二十）。"""
        self.store.save(self._make_record("Paper A", reading_status="unread"))
        self.store.save(self._make_record("Paper B", reading_status="skimmed"))
        self.store.save(self._make_record("Paper C", reading_status="reading"))
        self.store.save(self._make_record("Paper D", reading_status="finished"))
        win = LibraryWindow(self.store, parent=None)
        statuses = [self._status_cell(win, i) for i in range(4)]
        self.assertIn("未读", statuses)
        self.assertIn("已泛读", statuses)
        self.assertIn("阅读中", statuses)
        self.assertIn("已完成", statuses)
        win.close()

    def test_legacy_status_values_migrated(self):
        """旧值（done/skipped/未阅读/已完成）加载时归一为新枚举。"""
        from tju_info_retrieval.models.library import normalize_reading_status

        self.assertEqual(normalize_reading_status("done"), "finished")
        self.assertEqual(normalize_reading_status("skipped"), "skimmed")
        self.assertEqual(normalize_reading_status("未阅读"), "unread")
        self.assertEqual(normalize_reading_status("已完成"), "finished")
        self.assertEqual(normalize_reading_status("bogus"), "unread")

    # ---------- 3. 修改状态保存 ----------

    def test_change_status_saves_to_record(self):
        """修改状态保存到 record 并刷新表格。"""
        rec = self._make_record("Test paper", reading_status="unread")
        self.store.save(rec)
        win = LibraryWindow(self.store, parent=None)
        win.table.selectRow(0)

        with mock.patch.object(QInputDialog, "getItem", return_value=("阅读中", True)) as mock_get:
            win.btn_change_status.click()
            mock_get.assert_called_once()

        # 验证 store 中已更新（存枚举值，显示中文）
        updated = self.store.load_all()
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0].reading_status, "reading")
        # 验证表格已刷新
        self.assertEqual(self._status_cell(win), "阅读中")
        win.close()

    # ---------- 4. 取消不修改 ----------

    def test_change_status_cancel_keeps_original(self):
        """取消修改时保留原状态。"""
        rec = self._make_record("Test paper", reading_status="reading")
        self.store.save(rec)
        win = LibraryWindow(self.store, parent=None)
        win.table.selectRow(0)

        with mock.patch.object(QInputDialog, "getItem", return_value=("未读", False)) as mock_get:
            win.btn_change_status.click()
            mock_get.assert_called_once()

        # 验证 store 未变
        updated = self.store.load_all()
        self.assertEqual(updated[0].reading_status, "reading")
        win.close()

    # ---------- 5. 重新打开窗口保持 ----------

    def test_status_persists_after_reload(self):
        """修改状态后重新打开窗口，状态保持不变。"""
        rec = self._make_record("Persist test", reading_status="unread")
        self.store.save(rec)

        # 第一次打开并修改
        win1 = LibraryWindow(self.store, parent=None)
        win1.table.selectRow(0)
        with mock.patch.object(QInputDialog, "getItem", return_value=("已完成", True)):
            win1.btn_change_status.click()
        win1.close()

        # 重新打开
        win2 = LibraryWindow(self.store, parent=None)
        self.assertEqual(self._status_cell(win2), "已完成")
        win2.close()

    # ---------- 6. 未选择安全失败 ----------

    def test_no_selection_safe_failure(self):
        """未选择行时点击修改状态按钮，弹出提示不报错。"""
        self.store.save(self._make_record("T"))
        win = LibraryWindow(self.store, parent=None)
        # 不 selectRow
        with mock.patch.object(QMessageBox, "information", return_value=None) as info:
            win.btn_change_status.click()
        self.assertIn("请先在表格中选择一条结果", info.call_args.args[2])
        win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
