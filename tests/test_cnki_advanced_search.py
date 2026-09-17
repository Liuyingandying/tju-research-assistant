#!/usr/bin/env python3
"""v0.13 Phase 7：CNKI 普通/高级双路径路由测试（全部 mock，不访问真实 CNKI）。

覆盖：
1. 无高级条件 → 普通搜索
2. 作者条件 → 调用 advanced
3. 单位条件 → 调用 advanced
4. 日期条件 → 调用 advanced
5. 字段组合（主题+作者+单位+日期）→ 调用 advanced
6. 高级失败 → fallback 普通搜索（不阻断整体搜索）
7. 旧 adapter 接口兼容（search(keyword, count) 不带 query_context）
"""
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.sources.cnki import CNKIAdapter, _has_advanced_conditions
from tju_info_retrieval.sources.cnki_advanced import CNKIAdvancedQueryBuilder

KEYWORD = "太赫兹"


def _request(**kw):
    data = dict(research_direction="太赫兹")
    data.update(kw)
    return QueryRequest(**data)


def _fake_result_page():
    """普通/高级搜索共用的结果页 mock。"""
    page = mock.Mock()
    page.url = "https://kns.cnki.net/kns8s/defaultresult/index?x=1"
    page.query_selector_all.return_value = []
    return page


def _normal_search_page():
    """普通搜索页 mock：可定位搜索输入框与按钮。"""
    page = mock.Mock()
    search_input = mock.Mock()
    search_btn = mock.Mock()

    def qs(sel):
        if sel in ("#txt_SearchText", "textarea.search-input"):
            return search_input
        if sel in ("div.search-btn", "input.search-btn"):
            return search_btn
        return None

    page.query_selector.side_effect = qs
    return page, search_input, search_btn


def _session_with(page):
    session = mock.Mock()
    session.page.return_value = page
    context = mock.Mock()
    context.pages = [page]
    session.context = context
    return session


class TestCnkiRouting(unittest.TestCase):
    # ---- 1. 无高级条件 → 普通搜索 ----

    def test_no_advanced_condition_uses_normal_search(self):
        page, search_input, search_btn = _normal_search_page()
        session = _session_with(page)
        # 无高级条件：query_context 全空
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_result_page",
            return_value=_fake_result_page(),
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder",
        ) as builder_cls, mock.patch.object(
            CNKIAdapter,
            "parse_results",
            return_value=[SearchResult(rank=1, title="N")],
        ):
            adapter = CNKIAdapter(session)
            out = adapter.search(KEYWORD, 10, query_context=_request())
        # 高级路径未触发
        builder_cls.assert_not_called()
        # 普通路径执行：填入检索词并点击
        search_input.fill.assert_called_once_with(KEYWORD)
        search_btn.click.assert_called_once()
        self.assertEqual(len(out), 1)

    # ---- 2/3/4. 单个条件 → advanced ----

    def test_author_condition_calls_advanced(self):
        session = _session_with(mock.Mock())
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_advanced_result_page",
            return_value=_fake_result_page(),
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder",
        ) as builder_cls, mock.patch.object(
            CNKIAdapter,
            "parse_results",
            return_value=[SearchResult(rank=1, title="A")],
        ):
            adapter = CNKIAdapter(session)
            q = _request(author_name="张三")
            adapter.search(KEYWORD, 10, query_context=q)
        builder_cls.assert_called_once()
        builder_cls.return_value.build.assert_called_once()

    def test_affiliation_condition_calls_advanced(self):
        session = _session_with(mock.Mock())
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_advanced_result_page",
            return_value=_fake_result_page(),
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder",
        ) as builder_cls, mock.patch.object(
            CNKIAdapter,
            "parse_results",
            return_value=[SearchResult(rank=1, title="B")],
        ):
            adapter = CNKIAdapter(session)
            adapter.search(
                KEYWORD, 10,
                query_context=_request(author_affiliation="天津大学"),
            )
        builder_cls.assert_called_once()
        builder_cls.return_value.build.assert_called_once()

    def test_date_condition_calls_advanced(self):
        session = _session_with(mock.Mock())
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_advanced_result_page",
            return_value=_fake_result_page(),
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder",
        ) as builder_cls, mock.patch.object(
            CNKIAdapter,
            "parse_results",
            return_value=[SearchResult(rank=1, title="C")],
        ):
            adapter = CNKIAdapter(session)
            adapter.search(
                KEYWORD, 10,
                query_context=_request(start_date="2020.06"),
            )
        builder_cls.assert_called_once()

    # ---- 5. 字段组合 → advanced，且 builder 收到全部条件 ----

    def test_combined_conditions_advanced(self):
        session = _session_with(mock.Mock())
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_advanced_result_page",
            return_value=_fake_result_page(),
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder",
        ) as builder_cls, mock.patch.object(
            CNKIAdapter,
            "parse_results",
            return_value=[SearchResult(rank=1, title="D")],
        ):
            adapter = CNKIAdapter(session)
            q = _request(
                author_name="张三",
                author_affiliation="天津大学",
                start_date="2020.06",
                end_date="2023.08",
            )
            adapter.search(KEYWORD, 10, query_context=q)
        builder_cls.return_value.build.assert_called_once()
        # build 收到的 keyword 与 query 完整（v0.13 Phase 8.1 回归：类型不污染）
        call_args = builder_cls.return_value.build.call_args[0]
        self.assertEqual(call_args[0], session.page())
        self.assertEqual(call_args[1], KEYWORD)
        self.assertIsInstance(call_args[1], str)  # keyword 永远是 str
        self.assertIsInstance(call_args[2], QueryRequest)  # query 永远是 QueryRequest
        self.assertIs(call_args[2], q)

    # ---- 6. 高级失败 → fallback 普通搜索 ----

    def test_advanced_failure_falls_back_to_normal(self):
        page, search_input, search_btn = _normal_search_page()
        session = _session_with(page)
        builder = mock.Mock()
        builder.build.side_effect = Exception("高级检索页面异常")
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_result_page",
            return_value=_fake_result_page(),
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder",
            return_value=builder,
        ), mock.patch.object(
            CNKIAdapter,
            "parse_results",
            return_value=[SearchResult(rank=1, title="FB")],
        ):
            adapter = CNKIAdapter(session)
            out = adapter.search(
                KEYWORD, 10, query_context=_request(author_name="张三")
            )
        # 高级先尝试、失败后普通搜索兜底
        builder.build.assert_called_once()
        search_input.fill.assert_called_once_with(KEYWORD)
        search_btn.click.assert_called_once()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].title, "FB")

    # ---- 7. 旧接口兼容 ----

    def test_legacy_interface_without_query_context(self):
        page, search_input, search_btn = _normal_search_page()
        session = _session_with(page)
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_result_page",
            return_value=_fake_result_page(),
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder",
        ) as builder_cls, mock.patch.object(
            CNKIAdapter,
            "parse_results",
            return_value=[SearchResult(rank=1, title="L")],
        ):
            adapter = CNKIAdapter(session)
            out = adapter.search(KEYWORD, 10)  # 不带 query_context
        builder_cls.assert_not_called()
        search_input.fill.assert_called_once_with(KEYWORD)
        self.assertEqual(len(out), 1)


class TestAdvancedConditionDetect(unittest.TestCase):
    def test_none_returns_false(self):
        self.assertFalse(_has_advanced_conditions(None))

    def test_empty_query_returns_false(self):
        self.assertFalse(_has_advanced_conditions(_request()))

    def test_each_field_triggers(self):
        for kw in (
            dict(author_name="张三"),
            dict(author_affiliation="天津大学"),
            dict(start_date="2020.06"),
            dict(end_date="2023.08"),
        ):
            with self.subTest(kw=kw):
                self.assertTrue(_has_advanced_conditions(_request(**kw)))

    def test_whitespace_only_returns_false(self):
        self.assertFalse(
            _has_advanced_conditions(_request(author_name="  ", start_date=""))
        )


# ============================================================
# v0.13 Phase 8.1 回归：参数类型完整性（keyword=str, query=QueryRequest）
# ============================================================

class TestParameterTypeIntegrity(unittest.TestCase):
    """回归：'QueryRequest' object has no attribute 'strip' 类型污染。"""

    def test_advanced_path_keyword_is_str_query_is_queryrequest(self):
        """高级路径：builder.build 收到 keyword=str、query=QueryRequest。"""
        session = _session_with(mock.Mock())
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_advanced_result_page",
            return_value=_fake_result_page(),
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder",
        ) as builder_cls, mock.patch.object(
            CNKIAdapter,
            "parse_results",
            return_value=[SearchResult(rank=1, title="T")],
        ):
            adapter = CNKIAdapter(session)
            q = _request(author_affiliation="天津大学", start_date="2023.01")
            adapter.search(KEYWORD, 20, query_context=q)
        call_args = builder_cls.return_value.build.call_args[0]
        self.assertIsInstance(call_args[0], type(session.page()))
        self.assertIsInstance(call_args[1], str)
        self.assertEqual(call_args[1], KEYWORD)
        self.assertIsInstance(call_args[2], QueryRequest)
        self.assertIs(call_args[2], q)

    def test_fallback_path_still_passes_keyword_str(self):
        """高级异常 fallback：普通搜索仍收到 keyword=str，不受类型污染。"""
        page, search_input, search_btn = _normal_search_page()
        session = _session_with(page)
        builder = mock.Mock()
        builder.build.side_effect = Exception("高级失败")
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_result_page",
            return_value=_fake_result_page(),
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder",
            return_value=builder,
        ), mock.patch.object(
            CNKIAdapter,
            "parse_results",
            return_value=[SearchResult(rank=1, title="FB")],
        ):
            adapter = CNKIAdapter(session)
            adapter.search(
                KEYWORD, 20, query_context=_request(author_affiliation="天津大学")
            )
        # 普通搜索收到的是 str keyword，而非 QueryRequest
        fill_call = search_input.fill.call_args[0][0]
        self.assertIsInstance(fill_call, str)
        self.assertEqual(fill_call, KEYWORD)
        search_btn.click.assert_called_once()

    def test_builder_build_signature_order(self):
        """build 形参顺序：(page, keyword: str, query)，与调用方一致。"""
        import inspect

        from tju_info_retrieval.sources.cnki_advanced import CNKIAdvancedQueryBuilder
        params = list(inspect.signature(CNKIAdvancedQueryBuilder.build).parameters)
        self.assertEqual(params, ["self", "page", "keyword", "query"])


# ============================================================
# v0.13 Phase 8.3 回归：DOM 定位（hidden 不选、三行定位、字段选项）
# ============================================================

class TestDomLocators(unittest.TestCase):
    """基于真实 DOM 审计（runtime/cnki_advanced_dom_audit.json）的定位回归。"""

    def test_row_input_selector_excludes_hidden(self):
        """行输入选择器必须排除 hidden input（#showField 等）。"""
        from tju_info_retrieval.sources.cnki_advanced import ROW_INPUT_SELECTOR
        self.assertNotEqual(ROW_INPUT_SELECTOR, "div.input-box input")
        self.assertIn('input[type="text"]', ROW_INPUT_SELECTOR)
        self.assertIn(":visible", ROW_INPUT_SELECTOR)

    def test_row_input_selector_used_with_nth(self):
        """_fill_row 使用 ROW_INPUT_SELECTOR + nth(row_index) 定位三行。"""
        from tju_info_retrieval.sources import cnki_advanced
        from tju_info_retrieval.sources.cnki_advanced import (
            CNKIAdvancedQueryBuilder,
            ROW_INPUT_SELECTOR,
        )

        page = mock.Mock()
        # 模拟真实 DOM：每行 4 个 hidden + 1 个可见 text input
        row_input = mock.Mock()
        row_input.count.return_value = 5
        row_input.input_value.return_value = ""
        row_input_nth = mock.Mock()
        row_input_nth.count.return_value = 1
        row_input_nth.click.return_value = None
        row_input_nth.fill.return_value = None
        row_input_nth.input_value.return_value = ""
        row_input_nth.press_sequentially.return_value = None
        row_input.nth.return_value = row_input_nth
        page.locator.return_value = row_input

        builder = CNKIAdvancedQueryBuilder(mock.Mock())
        builder._goto = mock.Mock()
        builder._dismiss_ecp = mock.Mock()
        builder._fill_dates = mock.Mock()
        builder._click_search = mock.Mock()

        # 主题行（row 0）
        builder._fill_row(page, 0, "太赫兹")
        # 作者行（row 1）
        builder._fill_row(page, 1, "张三")
        # 单位行（row 2）
        builder._fill_row(page, 2, "天津大学")

        self.assertEqual(page.locator.call_count, 3)
        for call in page.locator.call_args_list:
            self.assertEqual(call.args[0], ROW_INPUT_SELECTOR)
        # nth 依次为 0,1,2
        nth_indices = [c.args[0] for c in row_input.nth.call_args_list]
        self.assertEqual(nth_indices, [0, 1, 2])
        # 填充值依次传入
        fill_values = [c.args[0] for c in row_input_nth.fill.call_args_list]
        self.assertEqual(fill_values, ["太赫兹", "张三", "天津大学"])
        # 逐键兜底（readback 为空时）被调用
        self.assertEqual(row_input_nth.press_sequentially.call_count, 3)

    def test_field_option_locator_uses_title(self):
        """字段下拉选项用 a[title='字段名']:visible，而非 get_by_text。"""
        from tju_info_retrieval.sources.cnki_advanced import (
            CNKIAdvancedQueryBuilder,
            ROW_SELECTOR,
        )

        page = mock.Mock()
        # 第一次 page.locator(ROW_SELECTOR) → 行容器；行内找触发器
        row = mock.Mock()
        trigger = mock.Mock()
        trigger.count.return_value = 1
        trigger.click.return_value = None
        row.locator.return_value.first = trigger
        # 第二次 page.locator('a[title=...]') → 字段选项
        option = mock.Mock()
        option.count.return_value = 1
        option.click.return_value = None

        def locator_side(sel):
            if sel == ROW_SELECTOR:
                loc = mock.Mock()
                loc.nth.return_value = row
                return loc
            opt_loc = mock.Mock()
            opt_loc.first = option
            return opt_loc

        page.locator.side_effect = locator_side

        builder = CNKIAdvancedQueryBuilder(mock.Mock())
        builder._switch_field(page, 2, "作者单位")

        used_selectors = [c.args[0] for c in page.locator.call_args_list]
        self.assertEqual(used_selectors, [ROW_SELECTOR, 'a[title="作者单位"]:visible'])
        option.click.assert_called_once()

    def test_field_option_absent_raises(self):
        """字段下拉无目标选项时抛 SearchError（不静默）。"""
        from tju_info_retrieval.sources.base import SearchError
        from tju_info_retrieval.sources.cnki_advanced import (
            CNKIAdvancedQueryBuilder,
            ROW_SELECTOR,
        )

        page = mock.Mock()
        row = mock.Mock()
        trigger = mock.Mock()
        trigger.count.return_value = 1
        row.locator.return_value.first = trigger
        option = mock.Mock()
        option.count.return_value = 0

        def locator_side(sel):
            if sel == ROW_SELECTOR:
                loc = mock.Mock()
                loc.nth.return_value = row
                return loc
            opt_loc = mock.Mock()
            opt_loc.first = option
            return opt_loc

        page.locator.side_effect = locator_side

        builder = CNKIAdvancedQueryBuilder(mock.Mock())
        with self.assertRaises(SearchError):
            builder._switch_field(page, 2, "不存在的字段")


# ============================================================
# v0.17 Phase 1：作者检索——空主题行跳过与条件组合
# 真机探针（runtime/v0.17_author_search_probe/cnki_author_only_report.json）：
# 仅作者单条件提交有效，5/5 结果作者命中。
# ============================================================

class TestCnkiAuthorBuild(unittest.TestCase):
    """CNKIAdvancedQueryBuilder.build 的主题行条件填充。"""

    def _build(self, keyword, query):
        builder = CNKIAdvancedQueryBuilder(mock.Mock())
        page = mock.Mock()
        with mock.patch.object(builder, "_goto"), \
                mock.patch.object(builder, "_dismiss_ecp"), \
                mock.patch.object(builder, "_fill_row") as fill, \
                mock.patch.object(builder, "_switch_field") as switch, \
                mock.patch.object(builder, "_fill_dates"), \
                mock.patch.object(builder, "_click_search"):
            builder.build(page, keyword, query)
        return fill, switch

    def test_person_only_skips_theme_row(self):
        """纯作者检索：主题行（row0）不触碰，仅填作者行（row1）。"""
        fill, switch = self._build("", QueryRequest(author_name="张三"))
        self.assertEqual([c.args[1] for c in fill.call_args_list], [1])
        self.assertEqual(fill.call_args_list[0].args[2], "张三")
        switch.assert_not_called()

    def test_topic_and_author(self):
        """topic + person：主题 row0 + 作者 row1。"""
        q = QueryRequest(research_direction="太赫兹", author_name="张三")
        fill, switch = self._build("太赫兹", q)
        self.assertEqual(
            [(c.args[1], c.args[2]) for c in fill.call_args_list],
            [(0, "太赫兹"), (1, "张三")],
        )
        switch.assert_not_called()

    def test_person_and_affiliation(self):
        """person + affiliation：作者 row1 + 作者单位 row2（row0 留空）。"""
        q = QueryRequest(author_name="张三", author_affiliation="天津大学")
        fill, switch = self._build("", q)
        self.assertEqual(
            [(c.args[1], c.args[2]) for c in fill.call_args_list],
            [(1, "张三"), (2, "天津大学")],
        )
        switch.assert_called_once()
        self.assertEqual(switch.call_args.args[1:], (2, "作者单位"))

    def test_topic_person_affiliation_three_conditions(self):
        """topic + person + affiliation：三行三条件。"""
        q = QueryRequest(
            research_direction="太赫兹", author_name="张三",
            author_affiliation="天津大学",
        )
        fill, switch = self._build("太赫兹", q)
        self.assertEqual(
            [(c.args[1], c.args[2]) for c in fill.call_args_list],
            [(0, "太赫兹"), (1, "张三"), (2, "天津大学")],
        )

    def test_person_only_routes_to_advanced(self):
        """author_name 非空 → CNKI 走高级检索（路由回归）。"""
        session = _session_with(mock.Mock())
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_advanced_result_page",
            return_value=_fake_result_page(),
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder",
        ) as builder_cls, mock.patch.object(
            CNKIAdapter,
            "parse_results",
            return_value=[SearchResult(rank=1, title="A")],
        ):
            adapter = CNKIAdapter(session)
            adapter.search("", 10, query_context=QueryRequest(author_name="张三"))
        builder_cls.return_value.build.assert_called_once()
        # 空主题 → keyword 为空串传入 build
        self.assertEqual(builder_cls.return_value.build.call_args.args[1], "")


# ============================================================
# v0.13 Phase 8.5 回归：日期跨年策略（箭头 / 年月下拉 / 回退）
# ============================================================

class TestDateCrossYearStrategy(unittest.TestCase):
    """基于 xdsoft DOM 审计（runtime/cnki_xdsoft_audit.json）的策略回归。"""

    def _builder_with_mocks(self, page):
        builder = CNKIAdvancedQueryBuilder(mock.Mock())
        builder._navigate_by_arrows = mock.Mock()
        builder._pick_by_select = mock.Mock(return_value=True)
        return builder

    def _make_page(self):
        """模拟已弹出日历的页面：#datebox0 可点、年月 label 可读、日格可点。

        生产实现改用 page.locator(...).first（:visible 仅 locator 支持）。
        """
        page = mock.Mock()
        datebox = mock.Mock()
        year_el = mock.Mock()
        year_el.inner_text.return_value = "2026"
        year_el.count.return_value = 1
        month_el = mock.Mock()
        month_el.inner_text.return_value = "九月"  # _CN_MONTHS.index=8 → 9月
        month_el.count.return_value = 1
        day_cell = mock.Mock()
        day_cell.count.return_value = 1

        def loc_side(sel):
            loc = mock.Mock()
            if sel == "#datebox0":
                return datebox
            if sel == ".xdsoft_label.xdsoft_year span:visible":
                loc.first = year_el
            elif sel == ".xdsoft_label.xdsoft_month span:visible":
                loc.first = month_el
            elif "td.xdsoft_date" in sel:
                loc.first = day_cell
            return loc

        page.locator.side_effect = loc_side
        page.query_selector.return_value = datebox
        return page, day_cell

    def test_small_span_uses_arrows(self):
        """2026.09 → 2025.12（diff=-9 ≤24）→ 走箭头导航，不用下拉。"""
        page, _day_cell = self._make_page()
        builder = self._builder_with_mocks(page)
        builder._pick_date(page, 0, 2025, 12, 31)
        builder._navigate_by_arrows.assert_called_once()
        builder._pick_by_select.assert_not_called()

    def test_large_span_uses_select(self):
        """2026.09 → 2023.01（diff=-44 >24）→ 走年月下拉快速跳转。"""
        page, _day_cell = self._make_page()
        builder = self._builder_with_mocks(page)
        builder._pick_date(page, 0, 2023, 1, 1)
        builder._pick_by_select.assert_called_once()
        builder._navigate_by_arrows.assert_not_called()

    def test_very_large_span_uses_select(self):
        """2026.09 → 2018.06（diff=-103 >24）→ 走年月下拉。"""
        page, _day_cell = self._make_page()
        builder = self._builder_with_mocks(page)
        builder._pick_date(page, 0, 2018, 6, 30)
        builder._pick_by_select.assert_called_once()
        builder._navigate_by_arrows.assert_not_called()

    def test_select_failure_falls_back_to_arrows(self):
        """年月下拉失败 → 回退箭头导航。"""
        page, _day_cell = self._make_page()
        builder = CNKIAdvancedQueryBuilder(mock.Mock())
        builder._navigate_by_arrows = mock.Mock()
        builder._pick_by_select = mock.Mock(return_value=False)
        builder._pick_date(page, 0, 2023, 1, 1)
        builder._pick_by_select.assert_called_once()
        builder._navigate_by_arrows.assert_called_once()

    def test_month_diff_helper(self):
        from tju_info_retrieval.sources.cnki_advanced import CNKIAdvancedQueryBuilder
        self.assertEqual(CNKIAdvancedQueryBuilder._month_diff((2026, 9), 2025, 12), -9)
        self.assertEqual(CNKIAdvancedQueryBuilder._month_diff((2026, 9), 2023, 1), -44)
        self.assertEqual(CNKIAdvancedQueryBuilder._month_diff((2026, 9), 2018, 6), -99)
        self.assertEqual(CNKIAdvancedQueryBuilder._month_diff((2026, 9), 2027, 3), 6)


# ============================================================
# v0.13 Phase 8.5 回归：日期直接注入（_inject_date 主路径）
# ============================================================

class TestDateInjection(unittest.TestCase):
    """直接注入 input 值 + change 事件（真实会话验证 CNKI 接受）。"""

    def test_inject_success_returns_true(self):
        from tju_info_retrieval.sources.cnki_advanced import CNKIAdvancedQueryBuilder

        page = mock.Mock()
        loc = mock.Mock()
        loc.count.return_value = 1
        loc.input_value.return_value = "2023-01-01"
        page.locator.return_value = loc

        builder = CNKIAdvancedQueryBuilder(mock.Mock())
        ok = builder._inject_date(page, 0, 2023, 1, 1)
        self.assertTrue(ok)
        # 传入格式化后的值（YYYY-MM-DD）
        inject_arg = loc.evaluate.call_args[0][1]
        self.assertEqual(inject_arg, "2023-01-01")
        # 触发 input/change 事件
        self.assertIn("dispatchEvent(new Event('input'", loc.evaluate.call_args[0][0])
        self.assertIn("dispatchEvent(new Event('change'", loc.evaluate.call_args[0][0])

    def test_inject_readback_mismatch_returns_false(self):
        from tju_info_retrieval.sources.cnki_advanced import CNKIAdvancedQueryBuilder

        page = mock.Mock()
        loc = mock.Mock()
        loc.count.return_value = 1
        loc.input_value.return_value = ""  # 注入未被接受
        page.locator.return_value = loc

        builder = CNKIAdvancedQueryBuilder(mock.Mock())
        self.assertFalse(builder._inject_date(page, 0, 2023, 1, 1))

    def test_inject_missing_box_returns_false(self):
        from tju_info_retrieval.sources.cnki_advanced import CNKIAdvancedQueryBuilder

        page = mock.Mock()
        loc = mock.Mock()
        loc.count.return_value = 0
        page.locator.return_value = loc

        builder = CNKIAdvancedQueryBuilder(mock.Mock())
        self.assertFalse(builder._inject_date(page, 0, 2023, 1, 1))

    def test_fill_dates_inject_primary_fallback_calendar(self):
        """YYYY.MM → 注入优先；注入失败回退 _pick_date。"""
        from tju_info_retrieval.sources.cnki_advanced import CNKIAdvancedQueryBuilder

        page = mock.Mock()
        builder = CNKIAdvancedQueryBuilder(mock.Mock())
        builder._inject_date = mock.Mock(side_effect=[True, False])  # start ok, end fail
        builder._pick_date = mock.Mock()

        builder._fill_dates(page, "2023.01", "2025.12")
        self.assertEqual(builder._inject_date.call_count, 2)
        # start 注入成功不触发日历；end 失败回退日历
        builder._pick_date.assert_called_once()
        call_args = builder._pick_date.call_args[0]
        self.assertEqual(call_args[0], page)
        self.assertEqual(call_args[1], 1)  # end datebox index
        self.assertEqual(call_args[2], 2025)
        self.assertEqual(call_args[3], 12)
        self.assertEqual(call_args[4], 31)  # 12 月最后一天


if __name__ == "__main__":
    unittest.main(verbosity=2)
