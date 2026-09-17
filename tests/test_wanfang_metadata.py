#!/usr/bin/env python3
"""v0.14 万方 metadata resolver 测试。

覆盖：
1. 详情页解析：年份 / 作者 / 引用量 / DOI / 标题（真实 DOM 结构 fixture）
2. 失败降级：无相关节点时字段为 None/空，不抛出
3. Resolver 缓存应用：命中即补齐且不打开页面；enrich 失败降级
4. 时间过滤恢复：补齐 year 后 FilterService 正常按区间过滤
（全部 mock / set_content，不开真实网络）
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest
from playwright.sync_api import sync_playwright

from tju_info_retrieval.models.metadata import MetadataRecord
from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.filtering import FilterService
from tju_info_retrieval.sources.wanfang_metadata_resolver import (
    WanfangMetadataResolver,
    _norm,
    parse_detail_page,
)

# 万方详情页 fixture（真实 DOM 结构：探针 detail_fields.json 实证）
DETAIL_FIXTURE = """<html><head><title>基于太赫兹成像技术的GFRP复合材料缺陷检测研究-期刊-万方数据知识服务平台</title></head>
<body>
<h1>基于太赫兹成像技术的GFRP复合材料缺陷检测研究</h1>
<div class="doiStyle">DOI:</div><span>10.11805/TFYDA2025297</span>
<div class="minerLine">被引 2</div>
<div class="author">
  <a class="test-detail-author">张元 1</a>
  <a class="test-detail-author">周文慧 2</a>
  <a class="test-detail-author">葛宏义 2</a>
</div>
<span class="item">文献发表日期：</span>
<div class="itemUrl">2026-02-28</div>
<span class="item">在线出版日期：</span>
<div class="itemUrl">2026-03-15</div>
<div>其他内容</div>
</body></html>"""


@pytest.fixture(scope="module")
def pw_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def detail_page(pw_browser):
    page = pw_browser.new_page()
    page.set_content(DETAIL_FIXTURE)
    yield page
    page.close()


class TestDetailParse:
    """详情页 DOM 解析：年份/作者/引用量/DOI/标题。"""

    def test_year_parsed_from_item_url(self, detail_page):
        meta = parse_detail_page(detail_page)
        assert meta["year"] == "2026", f"year={meta['year']!r}"

    def test_authors_parsed_with_superscript_stripped(self, detail_page):
        meta = parse_detail_page(detail_page)
        assert meta["authors"][:3] == ["张元", "周文慧", "葛宏义"]

    def test_citation_parsed(self, detail_page):
        meta = parse_detail_page(detail_page)
        assert meta["citation_count"] == 2

    def test_doi_parsed(self, detail_page):
        meta = parse_detail_page(detail_page)
        assert meta["doi"].startswith("10.11805/TFYDA2025297")

    def test_title_parsed(self, detail_page):
        meta = parse_detail_page(detail_page)
        assert meta["title"].startswith("基于太赫兹成像技术的GFRP复合材料缺陷检测研究")

    def test_degrade_on_unrelated_page(self, pw_browser):
        """无任何万方节点的页面：字段 None/空，不抛出（失败降级）。"""
        page = pw_browser.new_page()
        page.set_content("<html><body><div>无关内容</div></body></html>")
        try:
            meta = parse_detail_page(page)
            assert meta["year"] is None
            assert meta["authors"] == []
            assert meta["doi"] is None
            assert meta["citation_count"] is None
        finally:
            page.close()


class TestResolverEnrich(unittest.TestCase):
    """Resolver：缓存命中直接应用（不打开页面）；失败降级。"""

    def test_cache_hit_applies_metadata_without_clicks(self):
        page = mock.Mock()
        cache = {
            _norm("太赫兹论文X"): {
                "year": "2023", "authors": ["张三"], "doi": "10.1/x",
                "citation_count": 4, "detail_url": "https://d/1",
            },
        }
        resolver = WanfangMetadataResolver(page=page, cache=cache)
        r = SearchResult(rank=1, title="太赫兹论文X", database="万方")
        resolver.enrich([r])
        assert r.year == "2023"
        assert r.authors == ["张三"]
        assert r.doi == "10.1/x"
        assert r.citation_count == 4
        assert r.detail_url == "https://d/1"
        page.locator.assert_not_called()  # 缓存命中不打开任何页面

    def test_enrich_failure_degrades_silently(self):
        """locator 失败：不抛出，字段保持原值。"""
        page = mock.Mock()
        page.locator.side_effect = RuntimeError("boom")
        resolver = WanfangMetadataResolver(page=page, cache={})
        r = SearchResult(rank=1, title="T", database="万方")
        resolver.enrich([r])
        assert r.year is None and r.detail_url is None

    def test_no_results_no_op(self):
        page = mock.Mock()
        resolver = WanfangMetadataResolver(page=page, cache={})
        resolver.enrich([])
        page.locator.assert_not_called()


class TestTimeFilterRestored(unittest.TestCase):
    """补齐 year 后 FilterService 时间过滤恢复正常（含 strict 万方）。"""

    def _filter(self, rows, start="2020.01", end="2024.12", mode="strict"):
        q = QueryRequest(
            research_direction="太赫兹", author_affiliation="天津大学",
            sources=["万方"], start_date=start, end_date=end,
            affiliation_filter_mode=mode,
        )
        return FilterService().filter(rows, q, metadata_map={})

    def test_year_2026_out_of_range_deleted_even_strict(self):
        rows = [
            SearchResult(rank=1, title="A2026", database="万方", year="2026"),
            SearchResult(rank=2, title="B2023", database="万方", year="2023"),
        ]
        out = self._filter(rows, mode="strict")
        titles = [r.title for r in out]
        self.assertEqual(titles, ["B2023"])

    def test_year_2023_in_range_kept(self):
        rows = [SearchResult(rank=1, title="B2023", database="万方", year="2023")]
        out = self._filter(rows, mode="soft")
        assert len(out) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])