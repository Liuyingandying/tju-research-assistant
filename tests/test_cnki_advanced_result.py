#!/usr/bin/env python3
"""v0.13 Phase 8.6：CNKI 高级检索结果页检测修复测试（全部 mock，不访问真实 CNKI）。

根因：kns8s 高级检索提交成功后 URL 不变（结果在 AdvSearch SPA 内渲染
``table.result-table-list``），旧判定 ``wait_for_result_page`` 只认
defaultresult URL → 必然超时 → 误报"未检测到 CNKI 高级检索结果页"→
回退普通搜索。

覆盖：
1. URL 不变（AdvSearch）但 ``table.result-table-list`` 出现 → 成功（A 判定）
2. defaultresult URL 出现 → 成功（普通 ``wait_for_result_page`` 原逻辑保留，
   新高级判定亦兼容该 URL）
3. 无 table 容器但结果区域出现有效 tr 数据行 → 成功（B 判定）
4. 结果统计条/分页条出现 → 成功（C 判定）
5. 超时无任何结果信号 → ``wait_for_advanced_result_page`` 返回 None，
   ``_advanced_search`` 抛 SearchError
6. 高级失败（含超时）→ fallback 普通搜索行为保持
7. 非 CNKI 页面（含 table）不误判
"""
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from poc_cnki_search import wait_for_result_page  # 普通搜索原逻辑（未修改）
from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.sources.base import SearchError
from tju_info_retrieval.sources.cnki import CNKIAdapter
from tju_info_retrieval.sources.cnki_advanced import wait_for_advanced_result_page

KEYWORD = "太赫兹"
# kns8s 高级检索页：提交成功后停留于此，URL 不含 defaultresult
ADV_URL = "https://kns.cnki.net/kns8s/AdvSearch"
RESULT_URL = "https://kns.cnki.net/kns8s/defaultresult/index?x=1"


def _request(**kw) -> QueryRequest:
    data = dict(research_direction=KEYWORD)
    data.update(kw)
    return QueryRequest(**data)


def _cnki_page(url=ADV_URL, table=None, row=None, state=None):
    """模拟 CNKI 页面：按 selector 返回对应元素（None=尚未渲染）。"""
    page = mock.Mock()
    page.url = url

    def qs(sel):
        if sel == "table.result-table-list":
            return table
        if sel == "tr td.name a":
            return row
        if sel in (".pagerTitleBar", "div.pager", "[class*='pagerTitle']"):
            return state
        return None

    page.query_selector.side_effect = qs
    return page


def _context_with(page):
    context = mock.Mock()
    context.pages = [page]
    return context


def _fake_result_page():
    """旧普通路径共用的结果页 mock（defaultresult URL）。"""
    page = mock.Mock()
    page.url = RESULT_URL
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
    session.context = _context_with(page)
    return session


class TestAdvancedResultDetection(unittest.TestCase):
    """wait_for_advanced_result_page 的 A/B/C 判定（真实函数执行）。"""

    def test_spa_url_table_appears_success(self):
        """1. URL 不变（AdvSearch，无 defaultresult）但结果表格出现 → 成功。"""
        page = _cnki_page(ADV_URL, table=object())
        out = wait_for_advanced_result_page(_context_with(page), timeout_s=1.0, poll_interval=0.05)
        self.assertIs(out, page)

    def test_result_rows_fallback_success(self):
        """3. table 容器缺失但结果区域出现有效数据行 → 成功（B 判定）。"""
        page = _cnki_page(ADV_URL, table=None, row=object())
        out = wait_for_advanced_result_page(_context_with(page), timeout_s=1.0, poll_interval=0.05)
        self.assertIs(out, page)

    def test_state_change_fallback_success(self):
        """4. 结果统计条/分页条出现 → 成功（C 判定）。"""
        page = _cnki_page(ADV_URL, table=None, row=None, state=object())
        out = wait_for_advanced_result_page(_context_with(page), timeout_s=1.0, poll_interval=0.05)
        self.assertIs(out, page)

    def test_timeout_no_result_returns_none(self):
        """5. 超时无任何结果信号 → 返回 None（调用方抛 SearchError）。"""
        page = _cnki_page(ADV_URL)  # 全部 selector 未渲染
        out = wait_for_advanced_result_page(_context_with(page), timeout_s=0.3, poll_interval=0.05)
        self.assertIsNone(out)

    def test_non_cnki_page_ignored(self):
        """7. 非 CNKI 站点页面即使有同名 table 也不判定。"""
        page = _cnki_page("https://www.example.com/search", table=object())
        out = wait_for_advanced_result_page(_context_with(page), timeout_s=0.3, poll_interval=0.05)
        self.assertIsNone(out)

    def test_defaultresult_url_compatible(self):
        """2b. 新高级判定对 defaultresult 结果页同样命中（向后兼容）。"""
        page = _cnki_page(RESULT_URL, table=object())
        out = wait_for_advanced_result_page(_context_with(page), timeout_s=1.0, poll_interval=0.05)
        self.assertIs(out, page)


class TestNormalSearchDetectionUnchanged(unittest.TestCase):
    """普通搜索 wait_for_result_page 原逻辑不受本次修复影响。"""

    def test_defaultresult_url_still_succeeds(self):
        """2a. 普通搜索：defaultresult URL 出现 → 成功（原函数、原逻辑）。"""
        page = _cnki_page(RESULT_URL)
        context = _context_with(page)
        out = wait_for_result_page(context, timeout_s=2.0)
        self.assertIs(out, page)

    def test_normal_path_timeout_returns_none(self):
        """普通搜索：无 defaultresult URL → 原样超时返回 None。"""
        page = _cnki_page(ADV_URL)  # 高级页 URL 不含 defaultresult
        out = wait_for_result_page(_context_with(page), timeout_s=0.3)
        self.assertIsNone(out)


class TestAdvancedSearchIntegration(unittest.TestCase):
    """_advanced_search / search 路由层面的行为。"""

    def test_advanced_search_succeeds_when_table_renders_in_spa(self):
        """端到端：URL 不变 + SPA 内渲染 table → 高级检索成功（不再误回退）。"""
        page = _cnki_page(ADV_URL, table=object())
        session = mock.Mock()
        session.page.return_value = mock.Mock()
        session.context = _context_with(page)
        with mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder"
        ), mock.patch.object(
            CNKIAdapter,
            "parse_results",
            return_value=[SearchResult(rank=1, title="SPA结果")],
        ), mock.patch("tju_info_retrieval.sources.cnki.time.sleep"):
            adapter = CNKIAdapter(session)
            out = adapter._advanced_search(
                KEYWORD, 10, _request(author_affiliation="天津大学")
            )
        self.assertEqual(out[0].title, "SPA结果")

    def test_advanced_search_raises_searcherror_on_timeout(self):
        """5b. 高级检测超时 → SearchError（错误信息保持原语义）。"""
        session = _session_with(mock.Mock())
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_advanced_result_page",
            return_value=None,
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder"
        ), mock.patch("tju_info_retrieval.sources.cnki.time.sleep"):
            adapter = CNKIAdapter(session)
            with self.assertRaises(SearchError) as cm:
                adapter._advanced_search(KEYWORD, 10, _request(author_name="张三"))
        self.assertIn("未检测到 CNKI 高级检索结果页", str(cm.exception))

    def test_advanced_timeout_falls_back_to_normal_search(self):
        """6. 高级失败（超时）→ fallback 普通搜索行为保持。"""
        page, search_input, search_btn = _normal_search_page()
        session = _session_with(page)
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_advanced_result_page",
            return_value=None,
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_result_page",
            return_value=_fake_result_page(),
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder"
        ), mock.patch.object(
            CNKIAdapter,
            "parse_results",
            return_value=[SearchResult(rank=1, title="FB")],
        ), mock.patch("tju_info_retrieval.sources.cnki.time.sleep"):
            adapter = CNKIAdapter(session)
            out = adapter.search(KEYWORD, 10, query_context=_request(author_name="张三"))
        # 高级失败后普通搜索兜底成功
        search_input.fill.assert_called_once_with(KEYWORD)
        search_btn.click.assert_called_once()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].title, "FB")


if __name__ == "__main__":
    unittest.main(verbosity=2)
