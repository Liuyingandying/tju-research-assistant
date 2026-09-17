#!/usr/bin/env python3
"""正式测试：IeeeAdapter（fixture 解析 / mock 搜索 / 字段映射）。"""
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest
from playwright.sync_api import sync_playwright

from tju_info_retrieval.sources.ieee import IeeeAdapter, _YEAR_RE, _DOC_TYPE_RE, compose_ieee_query

FIXTURE = """<html><body>
<div class="List-results-items">
  <xpl-results-item>
    <div class="d-flex result-item">
      <div class="col result-item-align px-3">
        <h3><a class="fw-bold" href="/document/1/">Title A</a></h3>
        <xpl-authors-name-list>
          <p class="author text-base-md-lh">
            <span><a>Author1</a><span class="separator">;</span></span>
            <span><a>Author2</a><span class="separator">;</span></span>
          </p>
        </xpl-authors-name-list>
        2024 Conference on Terahertz
        Year: 2024 | Conference Paper | Publisher: IEEE
      </div>
    </div>
  </xpl-results-item>
</div>
<div class="List-results-items">
  <xpl-results-item>
    <div class="d-flex result-item">
      <div class="col result-item-align px-3">
        <h3><a class="fw-bold" href="/document/2/">Title B</a></h3>
        <xpl-authors-name-list>
          <p class="author text-base-md-lh">
            <span><a>Author3</a><span class="separator">;</span></span>
          </p>
        </xpl-authors-name-list>
        2025 Journal of Applied Physics
        Year: 2025 | Journals & Magazines | Publisher: IEEE
      </div>
    </div>
  </xpl-results-item>
</div>
</body></html>"""


@pytest.fixture(scope="module")
def pw_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(FIXTURE)
        yield page
        browser.close()


class TestIeeeParsing:
    def test_regex_year(self):
        assert _YEAR_RE.search("Year: 2024").group(1) == "2024"
        assert _YEAR_RE.search("2025 Conference").group(1) == "2025"

    def test_regex_doc_type(self):
        assert _DOC_TYPE_RE.search("Conference Paper").group(0) == "Conference Paper"
        assert _DOC_TYPE_RE.search("Journals & Magazines").group(0) == "Journals & Magazines"

    def test_parse_full_fields(self, pw_page):
        results = IeeeAdapter._parse_results(pw_page, 10)
        assert len(results) == 2
        r = results[0]
        assert r.rank == 1
        assert r.title == "Title A"
        assert r.authors == ["Author1", "Author2"]
        assert r.year == "2024"
        assert r.detail_url == "/document/1/"
        assert r.document_type == "Conference Paper"
        assert r.database == "IEEE Xplore"
        assert r.venue is not None

    def test_max_count(self, pw_page):
        results = IeeeAdapter._parse_results(pw_page, 1)
        assert len(results) == 1
        assert results[0].title == "Title A"

    def test_second_result(self, pw_page):
        results = IeeeAdapter._parse_results(pw_page, 10)
        r = results[1]
        assert r.title == "Title B"
        assert r.authors == ["Author3"]
        assert r.year == "2025"
        assert r.document_type == "Journals & Magazines"


class TestIeeeSearch:
    @mock.patch("tju_info_retrieval.sources.ieee.PortalNavigator")
    def test_search_calls_portal_and_parse(self, mock_portal_cls):
        session = mock.Mock()
        adapter = IeeeAdapter(session)
        portal = mock_portal_cls.return_value
        page = mock.Mock()
        # 模拟 _parse_results 的 evaluate 返回值
        page.evaluate.return_value = [
            {"title": "Test Paper", "authors": ["A"], "year": "2024",
             "venue": "Conference", "url": "/doc/1", "document_type": "Conference Paper"},
        ]
        portal.open_resource.return_value = page

        results = adapter.search("terahertz", 10)
        assert len(results) == 1
        assert results[0].title == "Test Paper"
        portal.open_portal.assert_called_once()
        portal.open_resource.assert_called_once_with("IEEE/IET Electronic Library", "外文资源")

    def test_empty_results_raise(self):
        adapter = IeeeAdapter.__new__(IeeeAdapter)
        adapter._portal = mock.Mock()
        adapter._session = mock.Mock()
        page = mock.Mock()
        page.evaluate.return_value = []
        adapter._portal.open_resource.return_value = page
        with pytest.raises(Exception, match="为空"):
            adapter.search("nothing", 10)


class TestIeeeAuthorQuery:
    """v0.17 Phase 1：IEEE 真实 author 字段语法。

    真机取证（runtime/v0.17_author_search_probe/ieee_author_probe_report.json、
    ieee_author_combo_probe.json）：官方搜索框原生解释 ("Authors":"姓名")
    字段语法；作者+主题 AND 组合双条件同时生效；中文姓名原样传入诚实降级
    （返回 0 条，不伪造拼音）。
    """

    def test_person_only_author_syntax(self):
        assert compose_ieee_query("", "张三") == '("Authors":"张三")'

    def test_topic_only_passthrough(self):
        assert compose_ieee_query("(terahertz OR THz)", "") == "(terahertz OR THz)"
        assert compose_ieee_query("terahertz", None) == "terahertz"

    def test_topic_and_person_combined(self):
        combined = compose_ieee_query("(terahertz OR THz)", "Xiaodong Feng")
        assert combined == '("Authors":"Xiaodong Feng") AND (terahertz OR THz)'

    def test_cjk_name_not_romanized(self):
        """中文姓名原样进入 author 语法，不擅自生成拼音。"""
        assert compose_ieee_query("", "王圣麟") == '("Authors":"王圣麟")'

    def test_quote_in_name_sanitized(self):
        assert compose_ieee_query("", 'Li "Nick" Wang') == '("Authors":"Li  Nick  Wang")'

    def test_whitespace_name_ignored(self):
        assert compose_ieee_query("(terahertz)", "   ") == "(terahertz)"

    @mock.patch("tju_info_retrieval.sources.ieee.PortalNavigator")
    def test_search_person_only_fills_author_query(self, mock_portal_cls):
        """纯人名 → 搜索框收到 author 字段语法，人名不当普通关键词。"""
        adapter = IeeeAdapter(mock.Mock())
        page = mock.Mock()
        page.evaluate.return_value = [
            {"title": "P", "authors": ["Zhang San"], "year": "2024",
             "venue": "J", "url": "/document/1/", "document_type": None},
        ]
        mock_portal_cls.return_value.open_resource.return_value = page
        ctx = mock.Mock(author_name="张三", ranking_mode="COMPREHENSIVE")
        adapter.search("", 10, query_context=ctx)
        fill = page.locator.return_value.first.fill
        fill.assert_called_once_with('("Authors":"张三")')

    @mock.patch("tju_info_retrieval.sources.ieee.PortalNavigator")
    def test_search_topic_and_person_combined_fill(self, mock_portal_cls):
        adapter = IeeeAdapter(mock.Mock())
        page = mock.Mock()
        page.evaluate.return_value = []
        mock_portal_cls.return_value.open_resource.return_value = page
        ctx = mock.Mock(author_name="Xiaodong Feng", ranking_mode="COMPREHENSIVE")
        with pytest.raises(Exception, match="为空"):
            adapter.search("(terahertz OR THz)", 10, query_context=ctx)
        fill = page.locator.return_value.first.fill
        fill.assert_called_once_with(
            '("Authors":"Xiaodong Feng") AND (terahertz OR THz)'
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])