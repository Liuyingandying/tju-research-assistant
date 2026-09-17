#!/usr/bin/env python3
"""v0.9.3 Phase 2 测试：论文库窗口（offscreen）。

覆盖：窗口创建、空库显示、收藏数据展示、刷新重新加载、
打开论文链接调用、无链接安全失败。
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
from tju_info_retrieval.ui.library_window import LibraryWindow


class TestLibraryWindow(unittest.TestCase):
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

    def _make_record(self, title="Test paper", detail_url=None):
        return LibraryRecord(
            title=title,
            authors=["Author A"],
            year="2025",
            source="Test Journal",
            database="CNKI",
            detail_url=detail_url,
        )

    # ---------- 1. 窗口创建 ----------

    def test_window_created(self):
        """v0.18 Phase 2.9-B：列布局扩展为 8 列（含分析字段）。"""
        win = LibraryWindow(self.store, parent=None)
        self.assertEqual(win.windowTitle(), "我的论文库")
        # v0.18 Phase 2.9-C-E：表尾新增“选择”checkbox 列（报告多选）
        self.assertEqual(win.table.columnCount(), 9)
        self.assertEqual(win.table.horizontalHeaderItem(0).text(), "标题")
        self.assertEqual(win.table.horizontalHeaderItem(1).text(), "作者")
        self.assertEqual(win.table.horizontalHeaderItem(2).text(), "年份")
        self.assertEqual(win.table.horizontalHeaderItem(3).text(), "文献类型")
        self.assertEqual(win.table.horizontalHeaderItem(4).text(), "方向匹配")
        self.assertEqual(win.table.horizontalHeaderItem(5).text(), "阅读优先级")
        self.assertEqual(win.table.horizontalHeaderItem(6).text(), "阅读状态")
        self.assertEqual(win.table.horizontalHeaderItem(7).text(), "标签")
        self.assertEqual(win.table.horizontalHeaderItem(8).text(), "选择")
        # 详情 Tabs（§二十五/§二十六）
        tabs = [win.detail_tabs.tabText(i)
                for i in range(win.detail_tabs.count())]
        self.assertEqual(tabs, ["原始信息", "基础整理", "AI增强分析", "我的记录"])
        win.close()

    # ---------- 2. 空库显示正常 ----------

    def test_empty_library_shows_zero_rows(self):
        win = LibraryWindow(self.store, parent=None)
        self.assertEqual(win.table.rowCount(), 0)
        win.close()

    # ---------- 3. 收藏数据展示 ----------

    def test_library_displays_saved_records(self):
        self.store.save(self._make_record("Paper A", "https://example.com/a"))
        self.store.save(self._make_record("Paper B", "https://example.com/b"))
        win = LibraryWindow(self.store, parent=None)
        self.assertEqual(win.table.rowCount(), 2)
        titles = [win.table.item(i, 0).text() for i in range(2)]
        self.assertIn("Paper A", titles)
        self.assertIn("Paper B", titles)
        self.assertEqual(win.table.item(0, 1).text(), "Author A")
        self.assertEqual(win.table.item(0, 2).text(), "2025")
        # 来源不再单独占列，改在详情「原始信息」中展示
        win.table.selectRow(0)
        self.assertIn("Test Journal", win.label_original.text())
        win.close()

    # ---------- 4. 刷新重新加载 ----------

    def test_refresh_reloads_from_store(self):
        self.store.save(self._make_record("Initial Paper"))
        win = LibraryWindow(self.store, parent=None)
        self.assertEqual(win.table.rowCount(), 1)
        # 添加新记录
        self.store.save(self._make_record("New Paper"))
        # 点击刷新
        win.btn_refresh.click()
        self.assertEqual(win.table.rowCount(), 2)
        titles = [win.table.item(i, 0).text() for i in range(2)]
        self.assertIn("New Paper", titles)
        win.close()

    # ---------- 5. 打开论文链接调用 ----------

    def test_open_selected_emits_url(self):
        rec = self._make_record("Open me", "https://ieeexplore.ieee.org/document/12345/")
        self.store.save(rec)
        win = LibraryWindow(self.store, parent=None)
        win.table.selectRow(0)
        emitted_urls = []
        win.open_detail_requested.connect(lambda u: emitted_urls.append(u))
        win.btn_open.click()
        self.assertEqual(len(emitted_urls), 1)
        self.assertEqual(emitted_urls[0], "https://ieeexplore.ieee.org/document/12345/")
        win.close()

    # ---------- 6. 无链接安全失败 ----------

    def test_no_url_safe_failure(self):
        rec = self._make_record("No URL", None)
        self.store.save(rec)
        win = LibraryWindow(self.store, parent=None)
        win.table.selectRow(0)
        emitted_urls = []
        win.open_detail_requested.connect(lambda u: emitted_urls.append(u))
        with mock.patch.object(QMessageBox, "information", return_value=None) as info:
            win.btn_open.click()
        self.assertEqual(len(emitted_urls), 0)
        self.assertIn("没有可用的全文页面链接", info.call_args.args[2])
        win.close()

    def test_no_selection_no_emit(self):
        self.store.save(self._make_record("T", "https://x"))
        win = LibraryWindow(self.store, parent=None)
        # 不 selectRow
        emitted_urls = []
        win.open_detail_requested.connect(lambda u: emitted_urls.append(u))
        with mock.patch.object(QMessageBox, "information", return_value=None):
            win.btn_open.click()
        self.assertEqual(len(emitted_urls), 0)
        win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
