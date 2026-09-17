#!/usr/bin/env python3
"""关键信息整理 UI 测试（v0.17 Phase 2.4-B-OFFLINE）。

覆盖：按钮存在/初始禁用/检索后启用/失败禁用/demo 启用、未选择提示、
news/patent 正常四字段展示、paper 诚实降级展示、免责声明。
"""
import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from PySide6.QtWidgets import QApplication, QMessageBox

from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.ui.summary_dialog import SummaryDialog


def _news_dict(title="健康报：太赫兹和声波结合使无针血钠检测成为可能"):
    return SearchResult(
        rank=1, title=title, database="天津大学新闻网", artifact_type="news",
        abstract=("近日，天津大学研究团队开发了一种新型太赫兹光声系统，该系统"
                  "克服水干扰，无须抽血或标记，实现了对活体小鼠钠水平的实时测量，"
                  "并通过人体实验初步验证了走向临床应用的潜力与可行性。"),
        source="健康报", year="2025",
        artifact_metadata={"media": "健康报",
                           "publish_time": "2025-07-17 15:54:40",
                           "authors": [], "related_person": []},
    ).to_dict()


def _paper_dict():
    return SearchResult(rank=1, title="一种太赫兹检测方法", database="CNKI",
                        artifact_type="paper", abstract=None).to_dict()


class TestSummaryUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def _select(self, row_dict):
        self.window._results = [row_dict]
        self.window.table.setRowCount(1)
        self.window.table.selectRow(0)

    def test_button_exists_and_initially_disabled(self):
        assert self.window.btn_summary.text() == "关键信息整理"
        assert not self.window.btn_summary.isEnabled()

    def test_enabled_after_search_done(self):
        self.window._on_search_done([_news_dict()])
        assert self.window.btn_summary.isEnabled()

    def test_disabled_after_search_failed(self):
        self.window._on_search_done([_news_dict()])
        # _on_search_failed 会弹模态 QMessageBox，必须 mock（离屏无用户输入）
        with mock.patch.object(QMessageBox, "warning"):
            self.window._on_search_failed("网络错误")
        assert not self.window.btn_summary.isEnabled()

    def test_enabled_after_demo_load(self):
        self.window._on_load_demo()
        assert self.window.btn_summary.isEnabled()

    def test_no_selection_prompts(self):
        self.window._on_search_done([_news_dict()])
        with mock.patch.object(QMessageBox, "information") as info:
            self.window._on_summarize()
        info.assert_called_once()
        self.assertIn("请先在表格中选择一条结果", info.call_args.args[2])

    @mock.patch.object(SummaryDialog, "exec", lambda self: None)
    def test_news_selection_shows_four_fields(self):
        self.window._on_search_done([_news_dict()])
        self._select(_news_dict())
        self.window._on_summarize()
        # SummaryDialog 以模态 exec 弹出会阻塞 —— 改为校验构造逻辑：
        # 直接构造 dialog 验证内容（exec 不在此测试）。
        from tju_info_retrieval.services.offline_summary import summarize_offline
        result = SearchResult.from_dict(_news_dict())
        summary = summarize_offline(result)
        dialog = SummaryDialog(result, summary, self.window)
        texts = [w.text() for w in dialog.findChildren(type(self.window.label_status))]
        joined = "\n".join(texts)
        self.assertIn("研究内容", joined)
        self.assertIn("核心技术", joined)
        self.assertIn("主要成果", joined)
        self.assertIn("应用价值", joined)
        self.assertIn("请结合原始资料核对", joined)
        dialog.deleteLater()

    def test_paper_insufficient_honest_display(self):
        self.window._on_search_done([_paper_dict()])
        self._select(_paper_dict())
        from tju_info_retrieval.services.offline_summary import (
            INSUFFICIENT_TEXT, summarize_offline)
        result = SearchResult.from_dict(_paper_dict())
        summary = summarize_offline(result)
        dialog = SummaryDialog(result, summary, self.window)
        texts = [w.text() for w in dialog.findChildren(type(self.window.label_status))]
        joined = "\n".join(texts)
        self.assertIn(INSUFFICIENT_TEXT, joined)
        dialog.deleteLater()

    def test_no_ai_provider_wording(self):
        """措辞锁定：自动整理，非“AI 整理结果”。"""
        from tju_info_retrieval.ui.summary_dialog import SummaryDialog
        self.window._on_search_done([_news_dict()])
        self._select(_news_dict())
        from tju_info_retrieval.services.offline_summary import summarize_offline
        result = SearchResult.from_dict(_news_dict())
        summary = summarize_offline(result)
        dialog = SummaryDialog(result, summary, self.window)
        self.assertEqual(dialog.windowTitle(), "自动关键信息整理")
        all_text = "\n".join(
            w.text() for w in dialog.findChildren(type(self.window.label_status)))
        self.assertNotIn("AI 整理结果", all_text)
        self.assertNotIn("权威结论", all_text)
        dialog.deleteLater()


class TestSummaryModeSwitcher(unittest.TestCase):
    """v0.18 Phase 2.7-D：整理模式切换（结果区右上角）。

    - 默认基础整理（basic）；
    - 可切换 AI增强分析（enhanced）；
    - 控件固定在结果区域（resultsCard）内，窗口缩放后仍可见；
    - 路由 data 值不变（basic/enhanced），SummaryService 逻辑不受影响。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        self.window = MainWindow()
        self.window.show()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QApplication.processEvents()

    def test_default_mode_is_basic_summary(self):
        combo = self.window.combo_summary_mode
        self.assertEqual(combo.currentData(), "basic")
        self.assertEqual(combo.itemText(0), "基础整理")
        self.assertEqual(self.window._summary_mode(), "basic")

    def test_switch_to_enhanced_mode(self):
        combo = self.window.combo_summary_mode
        self.assertEqual(combo.itemText(1), "AI增强分析")
        combo.setCurrentIndex(1)
        self.assertEqual(combo.currentData(), "enhanced")
        self.assertEqual(self.window._summary_mode(), "enhanced")

    def test_mode_combo_located_in_results_card(self):
        from PySide6.QtWidgets import QFrame
        results_card = self.window.findChild(QFrame, "resultsCard")
        self.assertIsNotNone(results_card)
        # 控件父级为结果卡片（右上角固定位置）
        self.assertEqual(self.window.combo_summary_mode.parent(), results_card)

    def test_visible_at_small_window(self):
        self.window.resize(920, 560)
        QApplication.processEvents()
        combo = self.window.combo_summary_mode
        self.assertTrue(combo.isVisible())
        # 在窗口可视范围内
        self.assertGreaterEqual(combo.x(), 0)
        self.assertLessEqual(combo.x() + combo.width(), self.window.width())
        self.assertLessEqual(combo.y() + combo.height(), self.window.height())

    def test_visible_at_large_window(self):
        self.window.resize(1920, 1080)
        QApplication.processEvents()
        combo = self.window.combo_summary_mode
        self.assertTrue(combo.isVisible())
        self.assertLessEqual(combo.x() + combo.width(), self.window.width())


if __name__ == "__main__":
    unittest.main(verbosity=2)