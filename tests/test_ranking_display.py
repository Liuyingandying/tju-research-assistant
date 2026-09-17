#!/usr/bin/env python3
"""v0.12 Phase 2：推荐理由展示测试（offscreen）。

覆盖：
- 解释窗口创建（ExplanationDialog）
- 正确显示分数（综合评分 + 拆解四项）
- 正确显示 reasons
- 无解释安全处理（无选中行 / 空 reasons / 缺字段 / 无 query_info）
- 报告增强（explanations 可选，旧格式不变）
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

from PySide6.QtWidgets import QApplication

from tju_info_retrieval.models.ranking import RankingExplanation
from tju_info_retrieval.services import report_generator
from tju_info_retrieval.ui.explanation_dialog import ExplanationDialog


def _explanation(**kw):
    data = dict(
        total_score=106.8, relevance_score=60.0, year_score=25.0,
        citation_score=16.8, database_score=5.0,
        reasons=[
            "标题高度匹配检索词",
            "发表于近5年",
            "引用量较高（被引50次）",
            "IEEE Xplore来源",
        ],
    )
    data.update(kw)
    return RankingExplanation(**data)


def _result(**kw):
    data = dict(
        rank=1, title="太赫兹成像方法", authors=["张三"],
        source="IEEE Journal", year="2024", database="IEEE Xplore",
    )
    data.update(kw)
    return data


# ============================================================
# 解释窗口
# ============================================================

class TestExplanationDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_creation(self):
        dialog = ExplanationDialog(_result(), _explanation())
        self.assertEqual(dialog.windowTitle(), "推荐理由")
        self.assertEqual(dialog.title_label.text(), "太赫兹成像方法")
        self.assertEqual(dialog.reasons_list.count(), 4)
        dialog.deleteLater()

    def test_scores_displayed(self):
        dialog = ExplanationDialog(_result(), _explanation())
        self.assertIn("综合评分：106.8", dialog.total_label.text())
        self.assertIn("分", dialog.total_label.text())
        self.assertEqual(dialog.score_labels["相关性"].text(), "60.0 分")
        self.assertEqual(dialog.score_labels["年份"].text(), "25.0 分")
        self.assertEqual(dialog.score_labels["引用"].text(), "16.8 分")
        self.assertEqual(dialog.score_labels["来源"].text(), "5.0 分")
        dialog.deleteLater()

    def test_reasons_displayed_in_order(self):
        exp = _explanation()
        dialog = ExplanationDialog(_result(), exp)
        items = [
            dialog.reasons_list.item(i).text()
            for i in range(dialog.reasons_list.count())
        ]
        self.assertEqual(items, exp.reasons)
        dialog.deleteLater()

    def test_meta_displayed(self):
        dialog = ExplanationDialog(_result(), _explanation())
        self.assertIn("IEEE Xplore", dialog.meta_label.text())
        self.assertIn("IEEE Journal", dialog.meta_label.text())
        self.assertIn("2024", dialog.meta_label.text())
        dialog.deleteLater()

    def test_empty_reasons_safe(self):
        """空 reasons 列表安全渲染（窗口仍可创建）。"""
        dialog = ExplanationDialog(_result(), _explanation(reasons=[]))
        self.assertEqual(dialog.reasons_list.count(), 0)
        self.assertIn("综合评分：106.8", dialog.total_label.text())
        dialog.deleteLater()

    def test_zero_scores_displayed(self):
        exp = _explanation(
            total_score=0.0, relevance_score=0.0, year_score=0.0,
            citation_score=0.0, database_score=0.0, reasons=[],
        )
        dialog = ExplanationDialog(_result(title="x"), exp)
        self.assertIn("综合评分：0.0", dialog.total_label.text())
        for label in dialog.score_labels.values():
            self.assertEqual(label.text(), "0.0 分")
        dialog.deleteLater()

    def test_missing_result_fields_safe(self):
        """结果缺字段（空 dict）安全渲染。"""
        dialog = ExplanationDialog({}, _explanation())
        self.assertEqual(dialog.title_label.text(), "（无标题）")
        self.assertFalse(hasattr(dialog, "meta_label"))
        dialog.deleteLater()


# ============================================================
# MainWindow 集成
# ============================================================

class TestMainWindowExplanation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def test_button_exists_initially_disabled(self):
        self.assertTrue(hasattr(self.window, "btn_explanation"))
        self.assertEqual(self.window.btn_explanation.text(), "查看推荐理由")
        self.assertFalse(self.window.btn_explanation.isEnabled())

    def test_no_selection_shows_hint(self):
        """无选中行时安全提示，不创建窗口。"""
        self.window._results = [_result()]
        with mock.patch(
            "tju_info_retrieval.ui.main_window.QMessageBox.information"
        ) as m:
            self.window._on_explanation()
        m.assert_called_once()

    def test_explanation_for_result(self):
        """_explanation_for 使用检索词计算拆解（与排序同一套逻辑）。"""
        self.window._last_query_info = {"research_direction": "太赫兹"}
        exp = self.window._explanation_for(
            _result(title="太赫兹成像方法", year="2024", database="CNKI")
        )
        self.assertIsInstance(exp, RankingExplanation)
        self.assertAlmostEqual(exp.relevance_score, 60.0)
        self.assertAlmostEqual(exp.database_score, 3.0)
        self.assertIn("标题高度匹配检索词", exp.reasons)
        self.assertIn("CNKI来源", exp.reasons)

    def test_explanation_without_query_info_safe(self):
        """_last_query_info 为 None 时安全：相关性 0，其余分项正常。"""
        self.window._last_query_info = None
        exp = self.window._explanation_for(
            _result(title="任意标题", year="2024", database="CNKI")
        )
        self.assertAlmostEqual(exp.relevance_score, 0.0)
        self.assertAlmostEqual(exp.database_score, 3.0)
        self.assertNotIn("标题高度匹配检索词", exp.reasons)
        self.assertNotIn("标题部分匹配检索词", exp.reasons)

    def test_demo_load_enables_button_and_opens_dialog(self):
        """加载演示数据后按钮可用，点击弹出解释窗口（exec 被 mock）。"""
        self.window._on_load_demo()
        self.assertTrue(self.window.btn_explanation.isEnabled())
        self.window.table.setCurrentCell(0, 0)
        with mock.patch.object(ExplanationDialog, "exec", return_value=None) as m:
            self.window._on_explanation()
        m.assert_called_once()


# ============================================================
# 报告增强
# ============================================================

class TestReportExplanations(unittest.TestCase):
    def test_report_without_explanations_unchanged(self):
        """不传 explanations 时输出与旧格式一致（无新增行）。"""
        results = [_result(), _result(rank=2, title="量子计算方法", database="CNKI")]
        content = report_generator.generate_markdown(
            results, {"research_direction": "太赫兹"}
        )
        self.assertNotIn("综合评分", content)
        self.assertNotIn("推荐理由", content)

    def test_report_with_explanations(self):
        content = report_generator.generate_markdown(
            [_result()], {"research_direction": "太赫兹"}, [_explanation()],
        )
        self.assertIn("综合评分：106.8", content)
        self.assertIn("相关性 60.0 / 年份 25.0 / 引用 16.8 / 来源 5.0", content)
        self.assertIn("推荐理由：标题高度匹配检索词；发表于近5年", content)
        self.assertIn("IEEE Xplore来源", content)

    def test_report_empty_reasons_placeholder(self):
        content = report_generator.generate_markdown(
            [_result()], None, [_explanation(reasons=[])],
        )
        self.assertIn("推荐理由：—", content)

    def test_report_length_mismatch_ignored(self):
        """explanations 长度不匹配时整体忽略（避免理由错配）。"""
        results = [_result(), _result(rank=2, title="B", database="CNKI")]
        content = report_generator.generate_markdown(results, None, [_explanation()])
        self.assertNotIn("综合评分", content)
        self.assertNotIn("推荐理由", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
