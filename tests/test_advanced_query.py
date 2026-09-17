#!/usr/bin/env python3
"""v0.13 Phase 1：高级检索条件模型扩展测试。

覆盖：
1. 作者字段保存（author_name）
2. 单位字段保存（author_affiliation）
3. 日期解析（YYYY.MM 合法值）
4. 非法日期拒绝（格式错误 / 月份越界）
5. 结束日期早于开始日期拒绝
6. 数量 1-200 通过（MIN_RESULT_COUNT / MAX_RESULT_COUNT）
7. GUI 参数正确传递（作者/单位/起止年月/数量）
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

from tju_info_retrieval.models.query import (
    MAX_RESULT_COUNT,
    MIN_RESULT_COUNT,
    QueryRequest,
)


# ============================================================
# 1/2. 作者与单位字段保存
# ============================================================

class TestAuthorFields(unittest.TestCase):
    def test_author_name_saved(self):
        q = QueryRequest(research_direction="太赫兹", author_name="张三")
        self.assertEqual(q.author_name, "张三")

    def test_author_affiliation_saved(self):
        q = QueryRequest(research_direction="太赫兹", author_affiliation="天津大学")
        self.assertEqual(q.author_affiliation, "天津大学")

    def test_both_fields_validate(self):
        q = QueryRequest(
            research_direction="太赫兹",
            author_name="张三",
            author_affiliation="天津大学",
        )
        q.validate()
        self.assertEqual(q.author_name, "张三")
        self.assertEqual(q.author_affiliation, "天津大学")

    def test_default_empty(self):
        q = QueryRequest(research_direction="太赫兹")
        self.assertEqual(q.author_name, "")
        self.assertEqual(q.author_affiliation, "")
        self.assertEqual(q.start_date, "")
        self.assertEqual(q.end_date, "")


# ============================================================
# 3/4/5. 日期解析与校验
# ============================================================

class TestDateValidation(unittest.TestCase):
    def test_valid_dates_pass(self):
        q = QueryRequest(
            research_direction="太赫兹",
            start_date="2023.01",
            end_date="2025.12",
        )
        q.validate_date_range()  # 不抛异常
        q.validate()

    def test_empty_dates_legal(self):
        """空值合法（表示不限）。"""
        q = QueryRequest(research_direction="太赫兹")
        q.validate_date_range()

    def test_only_start_date_legal(self):
        q = QueryRequest(research_direction="太赫兹", start_date="2024.06")
        q.validate_date_range()

    def test_invalid_month_rejected(self):
        q = QueryRequest(research_direction="太赫兹", start_date="2023.13")
        with self.assertRaises(ValueError):
            q.validate_date_range()

    def test_invalid_month_zero_rejected(self):
        q = QueryRequest(research_direction="太赫兹", end_date="2023.00")
        with self.assertRaises(ValueError):
            q.validate_date_range()

    def test_invalid_format_rejected(self):
        for bad in ("2023-01", "202301", "23.01", "2023.1", "2023/01", "abcd.ef"):
            with self.subTest(bad=bad):
                q = QueryRequest(research_direction="太赫兹", start_date=bad)
                with self.assertRaises(ValueError):
                    q.validate_date_range()

    def test_validate_integrates_date_check(self):
        """validate() 内部调用 validate_date_range()。"""
        q = QueryRequest(research_direction="太赫兹", end_date="2025.13")
        with self.assertRaises(ValueError):
            q.validate()

    def test_end_before_start_rejected(self):
        q = QueryRequest(
            research_direction="太赫兹",
            start_date="2024.01",
            end_date="2023.12",
        )
        with self.assertRaises(ValueError):
            q.validate_date_range()

    def test_end_equal_start_allowed(self):
        q = QueryRequest(
            research_direction="太赫兹",
            start_date="2024.01",
            end_date="2024.01",
        )
        q.validate_date_range()

    def test_year_boundary_strings_compare_correctly(self):
        """跨年比较：2023.12 < 2024.01。"""
        q = QueryRequest(
            research_direction="太赫兹",
            start_date="2023.12",
            end_date="2024.01",
        )
        q.validate_date_range()


# ============================================================
# 6. 数量 1-200
# ============================================================

class TestCountRange(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(MIN_RESULT_COUNT, 1)
        self.assertEqual(MAX_RESULT_COUNT, 200)

    def test_boundary_values_pass(self):
        for count in (1, 20, 30, 50, 100, 199, 200):
            with self.subTest(count=count):
                q = QueryRequest(research_direction="太赫兹", result_count=count)
                q.validate()

    def test_below_min_rejected(self):
        q = QueryRequest(research_direction="太赫兹", result_count=0)
        with self.assertRaises(ValueError):
            q.validate()

    def test_above_max_rejected(self):
        q = QueryRequest(research_direction="太赫兹", result_count=201)
        with self.assertRaises(ValueError):
            q.validate()

    def test_negative_rejected(self):
        q = QueryRequest(research_direction="太赫兹", result_count=-5)
        with self.assertRaises(ValueError):
            q.validate()

    def test_candidate_count_range(self):
        q = QueryRequest(
            research_direction="太赫兹", result_count=10, candidate_count=200,
        )
        q.validate()


# ============================================================
# 7. GUI 参数正确传递
# ============================================================

class TestGuiParamsPassed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        self.window = MainWindow()
        self.captured = []

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def test_person_inputs_enabled(self):
        self.assertTrue(self.window.input_author_name.isEnabled())
        self.assertTrue(self.window.input_author_affiliation.isEnabled())

    def test_spin_count_range_and_default(self):
        self.assertEqual(self.window.combo_count.minimum(), MIN_RESULT_COUNT)
        self.assertEqual(self.window.combo_count.maximum(), MAX_RESULT_COUNT)
        self.assertEqual(self.window.combo_count.value(), 20)

    def test_date_inputs_exist(self):
        self.assertTrue(hasattr(self.window, "start_date_input"))
        self.assertTrue(hasattr(self.window, "end_date_input"))

    def test_all_params_passed(self):
        self.window.search_requested.connect(self.captured.append)
        self.window.input_direction.setText("太赫兹")
        self.window.input_author_name.setText("张三")
        self.window.input_author_affiliation.setText("天津大学")
        self.window.start_date_input.setText("2023.01")
        self.window.end_date_input.setText("2025.12")
        self.window.combo_count.setValue(50)
        self.window._on_search()
        self.assertEqual(len(self.captured), 1)
        q = self.captured[0]
        self.assertEqual(q.author_name, "张三")
        self.assertEqual(q.author_affiliation, "天津大学")
        self.assertEqual(q.start_date, "2023.01")
        self.assertEqual(q.end_date, "2025.12")
        self.assertEqual(q.result_count, 50)

    def test_invalid_date_blocked_in_gui(self):
        """非法日期被 validate() 拦截：弹提示、不发请求。"""
        self.window.search_requested.connect(self.captured.append)
        self.window.input_direction.setText("太赫兹")
        self.window.start_date_input.setText("2023.13")
        with mock.patch(
            "tju_info_retrieval.ui.main_window.QMessageBox.warning"
        ) as m:
            self.window._on_search()
        m.assert_called_once()
        self.assertEqual(self.captured, [])

    def test_end_before_start_blocked_in_gui(self):
        self.window.search_requested.connect(self.captured.append)
        self.window.input_direction.setText("太赫兹")
        self.window.start_date_input.setText("2024.01")
        self.window.end_date_input.setText("2023.12")
        with mock.patch(
            "tju_info_retrieval.ui.main_window.QMessageBox.warning"
        ) as m:
            self.window._on_search()
        m.assert_called_once()
        self.assertEqual(self.captured, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
