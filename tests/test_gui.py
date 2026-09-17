#!/usr/bin/env python3
"""正式测试：GUI 基础实例化、按钮状态逻辑、错误状态展示（offscreen）。"""
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

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from tju_info_retrieval.ui.main_window import MainWindow


class TestMainWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def test_window_title(self):
        self.assertEqual(self.window.windowTitle(), "信息自动检索整理系统")

    def test_widgets_present(self):
        self.assertIsNotNone(self.window.input_direction)
        self.assertIsNotNone(self.window.combo_count)
        self.assertIsNotNone(self.window.btn_search)
        self.assertIsNotNone(self.window.btn_check_auth)
        self.assertIsNotNone(self.window.btn_detail)
        self.assertIsNotNone(self.window.btn_export)
        self.assertIsNotNone(self.window.table)

    def test_initial_button_states(self):
        self.assertTrue(self.window.btn_search.isEnabled())
        self.assertTrue(self.window.btn_check_auth.isEnabled())
        self.assertFalse(self.window.btn_detail.isEnabled())
        self.assertFalse(self.window.btn_export.isEnabled())

    def test_search_failed_displays_error(self):
        with mock.patch.object(QMessageBox, "warning", return_value=None):
            self.window._on_search_failed("测试错误")
        self.assertIn("检索失败", self.window.label_status.text())
        self.assertTrue(self.window.btn_search.isEnabled())
        self.assertFalse(self.window.btn_export.isEnabled())

    def test_worker_error_displays_message(self):
        with mock.patch.object(QMessageBox, "critical", return_value=None):
            self.window._on_worker_error("测试错误")
        self.assertIn("测试错误", self.window.label_status.text())

    def test_auth_result_logged_in(self):
        self.window._on_auth_result("logged_in")
        self.assertIn("天津大学授权正常", self.window.label_status.text())

    def test_auth_result_login_page(self):
        self.window._on_auth_result("login_page")
        self.assertIn("需要重新登录", self.window.label_status.text())

    def test_search_done_populates_table(self):
        results = [
            {
                "rank": 1,
                "title": "T1",
                "authors": ["A", "B"],
                "source": "S",
                "year": "2026",
                "document_type": "期刊",
                "database": "CNKI",
                "detail_url": "https://x",
            }
        ]
        self.window._on_search_done(results)
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(self.window.table.item(0, 0).text(), "T1")
        self.assertTrue(self.window.btn_export.isEnabled())
        self.assertTrue(self.window.btn_detail.isEnabled())
        self.assertFalse(self.window._searching)

    def test_search_ignored_while_running(self):
        self.window._searching = True
        # 检索中再次点击应被忽略（不弹窗、不改变状态）
        with mock.patch.object(QMessageBox, "warning", return_value=None) as m:
            self.window._on_search()
        self.assertFalse(m.called)

    def test_check_auth_button_connected(self):
        # 回归防护：按钮 clicked 必须连接到 _on_check_auth
        with mock.patch.object(self.window, "_on_check_auth") as m:
            self.window.btn_check_auth.click()
            m.assert_called_once()

    def test_search_button_connected(self):
        with mock.patch.object(self.window, "_on_search") as m:
            self.window.btn_search.click()
            m.assert_called_once()

    def test_detail_button_connected(self):
        # 详情/导出按钮在有结果前禁用，测试先启用再点击
        self.window.btn_detail.setEnabled(True)
        with mock.patch.object(self.window, "_on_open_detail") as m:
            self.window.btn_detail.click()
            m.assert_called_once()

    def test_open_detail_emits_url(self):
        results = [
            {
                "rank": 1,
                "title": "T",
                "authors": ["A"],
                "source": "S",
                "year": "2026",
                "document_type": None,
                "database": "CNKI",
                "detail_url": "https://kns.cnki.net/x",
            }
        ]
        self.window._on_search_done(results)
        self.window.table.selectRow(0)
        with mock.patch.object(self.window, "open_detail_requested") as m:
            self.window._on_open_detail()
            m.emit.assert_called_once_with("https://kns.cnki.net/x")

    def test_open_detail_no_selection_no_emit(self):
        # 未选中行时不发出请求（弹窗被 patch）
        with mock.patch.object(QMessageBox, "information", return_value=None):
            with mock.patch.object(self.window, "open_detail_requested") as m:
                self.window._on_open_detail()
                m.emit.assert_not_called()

    def test_full_text_button_semantics(self):
        self.assertEqual(self.window.btn_detail.text(), "打开全文页面")
        self.assertIn("受控 Edge", self.window.btn_detail.toolTip())

    def test_detail_opened_updates_status(self):
        self.window._on_detail_opened("https://x")
        self.assertIn("论文全文页面已在浏览器中打开", self.window.label_status.text())

    def test_open_full_text_sets_opening_status(self):
        results = [
            {
                "rank": 1,
                "title": "T",
                "authors": ["A"],
                "source": "S",
                "year": "2026",
                "document_type": None,
                "database": "CNKI",
                "detail_url": "https://kns.cnki.net/x",
            }
        ]
        self.window._on_search_done(results)
        self.window.table.selectRow(0)
        with mock.patch.object(self.window, "open_detail_requested"):
            self.window._on_open_detail()
        self.assertIn("正在打开全文页面", self.window.label_status.text())

    def test_open_full_text_missing_url_no_title_feedback(self):
        """万方行无 detail_url 且无标题：无法按需解析，保持原提示。"""
        results = [
            {
                "rank": 1,
                "title": "",
                "authors": ["A"],
                "source": "S",
                "year": "2026",
                "document_type": None,
                "database": "万方",
                "detail_url": None,
            }
        ]
        self.window._on_search_done(results)
        self.window.table.selectRow(0)
        with mock.patch.object(QMessageBox, "information", return_value=None) as info:
            with mock.patch.object(self.window, "open_detail_requested") as requested:
                with mock.patch.object(
                    self.window, "open_wanfang_detail_requested"
                ) as wanfang:
                    self.window._on_open_detail()
        requested.emit.assert_not_called()
        wanfang.emit.assert_not_called()
        self.assertIn("没有可用的全文页面链接", info.call_args.args[2])

    def test_open_wanfang_detail_emits_title(self):
        """B′：万方行 detail_url 缺失但有标题 → 发出按需解析信号。"""
        results = [
            {
                "rank": 1,
                "title": "基于太赫兹成像技术的检测研究",
                "authors": ["A"],
                "source": "S",
                "year": "2026",
                "document_type": None,
                "database": "万方",
                "detail_url": None,
            }
        ]
        self.window._on_search_done(results)
        self.window.table.selectRow(0)
        with mock.patch.object(QMessageBox, "information", return_value=None) as info:
            with mock.patch.object(self.window, "open_detail_requested") as requested:
                with mock.patch.object(
                    self.window, "open_wanfang_detail_requested"
                ) as wanfang:
                    self.window._on_open_detail()
        wanfang.emit.assert_called_once_with("基于太赫兹成像技术的检测研究")
        requested.emit.assert_not_called()
        info.assert_not_called()

    def test_open_wanfang_branch_not_triggered_for_cnki(self):
        """键控隔离：CNKI 行无 url（异常数据）仍走原提示，不发万方信号。"""
        results = [
            {
                "rank": 1,
                "title": "T",
                "authors": ["A"],
                "source": "S",
                "year": "2026",
                "document_type": None,
                "database": "CNKI",
                "detail_url": None,
            }
        ]
        self.window._on_search_done(results)
        self.window.table.selectRow(0)
        with mock.patch.object(QMessageBox, "information", return_value=None) as info:
            with mock.patch.object(
                self.window, "open_wanfang_detail_requested"
            ) as wanfang:
                self.window._on_open_detail()
        wanfang.emit.assert_not_called()
        self.assertIn("没有可用的全文页面链接", info.call_args.args[2])

    # ---------- v0.13 GUI 人员字段 → 作者条件 映射回归 ----------

    def test_search_request_person_unit_maps_to_affiliation(self):
        """GUI 构造回归：人员单位→author_affiliation、人员姓名→author_name。

        模拟 _on_search 的字段读取：人员姓名=""、人员单位="天津大学"、
        仅万方 → 断言 QueryRequest 映射无错位（守护未来控件改版回归）。
        """
        self.window.input_direction.setText("太赫兹")
        self.window.input_author_name.setText("")            # 人员姓名=空
        self.window.input_author_affiliation.setText("天津大学")  # 人员单位
        self.window.chk_cnki.setChecked(False)
        self.window.chk_wanfang.setChecked(True)
        self.window.chk_ieee.setChecked(False)
        with mock.patch.object(self.window, "search_requested") as requested:
            self.window._on_search()
        requested.emit.assert_called_once()
        query = requested.emit.call_args.args[0]
        self.assertEqual(query.author_affiliation, "天津大学")
        self.assertEqual(query.author_name, "")
        self.assertEqual(query.research_direction, "太赫兹")
        self.assertEqual(query.sources, ["万方"])
        self.assertEqual(query.affiliation_filter_mode, "soft")

    # ---------- Query Expansion GUI 接入 ----------

    def test_expansion_combo_passes_to_query_request(self):
        self.window.input_direction.setText("太赫兹")
        self.window.combo_expansion.setCurrentText("理论")
        with mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
            query = m.emit.call_args[0][0]
            self.assertEqual(query.research_direction, "太赫兹")
            self.assertEqual(query.expansion_direction, "理论")

    def test_different_directions_produce_different_requests(self):
        self.window.input_direction.setText("太赫兹")
        directions = ("综合", "理论", "应用", "综述")
        seen = []
        for d in directions:
            self.window._searching = False  # 测试直接驱动，重置检索中状态
            self.window.combo_expansion.setCurrentText(d)
            with mock.patch.object(self.window, "search_requested") as m:
                self.window._on_search()
                seen.append(m.emit.call_args[0][0].expansion_direction)
        self.assertEqual(seen, list(directions))

    def test_default_expansion_is_general_compatible(self):
        self.window.input_direction.setText("太赫兹")
        self.assertEqual(self.window.combo_expansion.currentText(), "综合")
        with mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
            query = m.emit.call_args[0][0]
            self.assertEqual(query.expansion_direction, "综合")

    def test_expansion_tooltip_no_longer_placeholder(self):
        self.assertNotIn("后续阶段开放", self.window.combo_expansion.toolTip())

    # ---------- 多源选择 ----------

    def test_default_sources_cnki_checked(self):
        self.window.input_direction.setText("太赫兹")
        self.assertTrue(self.window.chk_cnki.isChecked())
        self.assertFalse(self.window.chk_wanfang.isChecked())
        self.assertFalse(self.window.chk_ieee.isChecked())
        with mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
            query = m.emit.call_args[0][0]
            self.assertEqual(query.sources, ["CNKI"])

    def test_multi_sources_passed_to_query(self):
        self.window.input_direction.setText("太赫兹")
        self.window.chk_cnki.setChecked(True)
        self.window.chk_wanfang.setChecked(True)
        self.window.chk_ieee.setChecked(True)
        with mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
            query = m.emit.call_args[0][0]
            self.assertEqual(query.sources, ["CNKI", "万方", "IEEE Xplore"])

    def test_export_button_connected(self):
        self.window.btn_export.setEnabled(True)
        with mock.patch.object(self.window, "_on_export") as m:
            self.window.btn_export.click()
            m.assert_called_once()

    # ---------- 报告生成 ----------

    def test_report_button_connected(self):
        self.window.btn_report.setEnabled(True)
        with mock.patch.object(self.window, "_on_generate_report") as m:
            self.window.btn_report.click()
            m.assert_called_once()

    def test_report_button_disabled_without_results(self):
        self.assertFalse(self.window.btn_report.isEnabled())

    def test_report_button_enabled_after_search(self):
        self.window._on_search_done(
            [{"rank": 1, "title": "T", "authors": [], "source": None,
              "year": None, "document_type": None, "database": "CNKI",
              "detail_url": None}]
        )
        self.assertTrue(self.window.btn_report.isEnabled())
        self.assertTrue(self.window.btn_export.isEnabled())

    def test_generate_report_creates_markdown_file(self):
        self.window._on_search_done(
            [{"rank": 1, "title": "太赫兹论文", "authors": ["张三"],
              "source": "期刊A", "year": "2026", "document_type": "期刊",
              "database": "CNKI", "detail_url": "https://kns.cnki.net/a1"}]
        )
        self.window._last_query_info = {
            "research_direction": "太赫兹",
            "expansion_direction": "综合",
            "sources": ["CNKI"],
        }
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "报告.md"
            with mock.patch.object(
                QFileDialog, "getSaveFileName", return_value=(str(target), "")
            ):
                self.window._on_generate_report()
            assert target.exists()
            text = target.read_text(encoding="utf-8")
        assert "# 文献检索报告" in text
        assert "太赫兹论文" in text
        assert "研究方向：太赫兹" in text
        self.assertIn("报告已生成", self.window.label_status.text())

    def test_generate_report_empty_results_no_dialog(self):
        self.window._on_search_done([])
        with mock.patch.object(
            QFileDialog, "getSaveFileName", return_value=("", "")
        ) as dlg:
            with mock.patch.object(QMessageBox, "information", return_value=None) as info:
                self.window._on_generate_report()
        info.assert_called_once()
        dlg.assert_not_called()

    # ---------- 关于 / 系统状态 ----------

    def test_about_button_exists_and_enabled(self):
        self.assertEqual(self.window.btn_about.text(), "关于")
        self.assertTrue(self.window.btn_about.isEnabled())

    def test_about_shows_version_and_sources(self):
        from tju_info_retrieval.version import VERSION
        with mock.patch(
            "tju_info_retrieval.services.test_report.current_test_status",
            return_value="178 passed, 0 failed, 0 errors",
        ), mock.patch.object(QMessageBox, "about", return_value=None) as about:
            self.window._on_about()
        about.assert_called_once()
        body = about.call_args.args[2]
        self.assertIn(f"v{VERSION}", body)
        self.assertIn("CNKI", body)
        self.assertIn("万方", body)
        self.assertIn("IEEE Xplore", body)
        for f in ("查询扩展", "多源检索", "结果去重", "排序", "全文页面", "报告生成"):
            self.assertIn(f, body)
        self.assertIn("178 passed", body)

    # ---------- 演示数据模式 ----------

    def test_demo_button_exists_and_enabled(self):
        self.assertEqual(self.window.btn_demo.text(), "加载演示数据")
        self.assertTrue(self.window.btn_demo.isEnabled())

    def test_load_demo_populates_and_dedups(self):
        self.window._on_load_demo()
        assert len(self.window._results) == 7  # 原始 8 条，跨来源重复去重后 7 条
        assert self.window.table.rowCount() == 7
        assert self.window.btn_report.isEnabled()
        self.assertIn("演示数据已加载", self.window.label_status.text())
        self.assertIn("未发起真实检索", self.window.label_status.text())

    def test_demo_shows_artifact_types_in_table(self):
        """v0.17 Phase 2.1：加载演示数据后类型列同时出现 专利/新闻 标签。"""
        self.window._on_load_demo()
        type_cells = {
            self.window.table.item(r, 4).text()
            for r in range(self.window.table.rowCount())
        }
        self.assertIn("专利", type_cells)
        self.assertIn("新闻", type_cells)
        self.assertIn("期刊论文", type_cells)  # 论文保持原类型文本


class TestAuthorOrDirectionUi(unittest.TestCase):
    """v0.17 Phase 1：研究方向 OR 人员姓名（任务书 R14）。

    合法组合：A 仅方向 / B 仅姓名 / C 方向+姓名；
    阻止：D 双空 / E 仅人员单位。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.captured = []
        self.window.search_requested.connect(self.captured.append)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def _search(self):
        self.window._on_search()

    def test_topic_only_allowed(self):
        self.window.input_direction.setText("太赫兹")
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].research_direction, "太赫兹")
        self.assertEqual(self.captured[0].author_name, "")

    def test_person_only_allowed(self):
        """仅人员姓名 → 发出 author_name 请求（research_direction 为空）。"""
        self.window.input_author_name.setText("张三")
        self._search()
        self.assertEqual(len(self.captured), 1)
        q = self.captured[0]
        self.assertEqual(q.research_direction, "")
        self.assertEqual(q.author_name, "张三")

    def test_topic_and_person_allowed(self):
        self.window.input_direction.setText("太赫兹")
        self.window.input_author_name.setText("张三")
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].research_direction, "太赫兹")
        self.assertEqual(self.captured[0].author_name, "张三")

    def test_both_empty_blocked_with_exact_message(self):
        with mock.patch.object(QMessageBox, "warning") as warn:
            self._search()
        warn.assert_called_once()
        self.assertEqual(
            warn.call_args.args[2], "请输入研究方向或人员姓名，至少填写一项。"
        )
        self.assertEqual(self.captured, [])

    def test_affiliation_only_blocked(self):
        """仅人员单位不能作为主检索条件。"""
        self.window.input_author_affiliation.setText("天津大学")
        with mock.patch.object(QMessageBox, "warning") as warn:
            self._search()
        warn.assert_called_once()
        self.assertEqual(self.captured, [])

    def test_person_with_affiliation_allowed(self):
        self.window.input_author_name.setText("张三")
        self.window.input_author_affiliation.setText("天津大学")
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].author_name, "张三")
        self.assertEqual(self.captured[0].author_affiliation, "天津大学")


class TestResponsiveActionBarLayout(unittest.TestCase):
    """v0.17 Phase 2.6-PKG-B Hotfix：底部操作按钮栏自适应布局。

    经真实 MainWindow resize 验证：宽窗口单行、窄窗口（1366×768 @125%
    逻辑宽 1093 等）自动两行（5+5），任何宽度下按钮文字不裁切。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.show()
        QApplication.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.window.deleteLater()
        QApplication.processEvents()

    def _window(self):
        return type(self).window

    def _action_bar(self):
        return self._window()._action_bar

    def _settle(self):
        QApplication.processEvents()
        QApplication.processEvents()

    def test_wide_window_single_row(self):
        self._window().resize(1920, 1080)
        self._settle()
        self.assertFalse(self._action_bar()._two_rows)

    def test_narrow_window_two_rows(self):
        # 1093 逻辑宽（1366×768 @125% 桌面）不足以单行容纳 11 个按钮
        self._window().resize(1093, 582)
        self._settle()
        bar = self._action_bar()
        self.assertTrue(bar._two_rows)
        # v0.18 Phase 2.8-A：11 按钮（新增“设置”）→ 第一行 5、第二行 6
        self.assertEqual(bar._row_a.count(), 5)
        self.assertEqual(bar._row_b.count(), 6)
        self.assertEqual(bar._row_a.count() + bar._row_b.count(), 11)

    def test_buttons_never_clipped_in_two_rows(self):
        from PySide6.QtGui import QFontMetrics
        self._window().resize(1093, 582)
        self._settle()
        for name in ("btn_favorite", "btn_detail", "btn_explanation", "btn_summary",
                     "btn_ieee_original", "btn_export", "btn_report", "btn_library",
                     "btn_demo", "btn_settings", "btn_about"):
            btn = getattr(self._window(), name)
            fm = QFontMetrics(btn.font())
            need = fm.horizontalAdvance(btn.text()) + 36
            self.assertGreaterEqual(
                btn.width(), need,
                f"{name} 文字在窄窗口被裁切（宽 {btn.width()} < 需 {need}）",
            )

    def test_buttons_never_clipped_single_row(self):
        from PySide6.QtGui import QFontMetrics
        self._window().resize(1920, 1080)
        self._settle()
        for name in ("btn_favorite", "btn_detail", "btn_explanation", "btn_summary",
                     "btn_ieee_original", "btn_export", "btn_report", "btn_library",
                     "btn_demo", "btn_settings", "btn_about"):
            btn = getattr(self._window(), name)
            fm = QFontMetrics(btn.font())
            need = fm.horizontalAdvance(btn.text()) + 36
            self.assertGreaterEqual(
                btn.width(), need,
                f"{name} 文字在宽窗口被裁切",
            )

    def test_resize_wide_back_to_single_row(self):
        bar = self._action_bar()
        self._window().resize(1093, 582)
        self._settle()
        self.assertTrue(bar._two_rows)
        self._window().resize(1920, 1080)
        self._settle()
        self.assertFalse(bar._two_rows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
