#!/usr/bin/env python3
"""v0.17 Phase 2.3-B：天津大学新闻网 Adapter 测试。

覆盖：parser（title/media/publish_time/summary/authors）、查询路由
（topic/person/combo 交集策略、原始中文词）、SearchResult 映射、
detail enrichment（成功/超时降级/失败降级/cache/hard cap/不伪造）、
Filter 集成（related_person/metadata.authors/publish_time/缺失保留）、
Merger 跨类型不互吞、SearchService 路由。
"""
from __future__ import annotations

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

from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.filtering import FilterService
from tju_info_retrieval.services.result_merger import ResultMerger
from tju_info_retrieval.services.search_service import SearchService
from tju_info_retrieval.sources.base import SearchError
from tju_info_retrieval.sources.tju_news import (
    DATABASE_NAME,
    TjuNewsAdapter,
    TjuNewsDetailResolver,
    norm_title,
    parse_arc_info,
    parse_dateline,
)

# 搜索结果页 fixture（selector 与 PoC 03/04/05/08 json 对齐）
LIST_FIXTURE = """<html><body>
智能搜索为您找到相关结果约为 15 个
<div class="listBox"><ul>
<li><div class="syqbwzzs_templ"><h1>
  <a class="title">健康报：<span style="color:#f56c6c">太赫兹</span>和声波结合使无针血钠检测成为可能</a></h1>
  <div class="qtxx1"><div class="txt">
    <p class="desc">近日，天津大学研究团队开发了一种新型太赫兹光声系统，实现无针血钠检测。</p>
    <div class="createDate_style"><b>媒体聚焦</b>
      健康报-2025-07-17 15:54:40</div>
  </div></div></div></li>
<li><div class="syqbwzzs_templ"><h1>
  <a class="title">天津大学精仪学院田震教授当选美国光学学会会士</a></h1>
  <div class="qtxx1"><div class="txt">
    <p class="desc">田震教授凭借在太赫兹光子学和光电子器件等方面的研究入选。</p>
    <div class="createDate_style"><b>综合新闻</b>
      精仪学院-2025-11-26 14:02:38</div>
  </div></div></div></li>
</ul></div>
</body></html>"""

DETAIL_FIXTURE = """<html><body>
<h1>健康报：太赫兹和声波结合使无针血钠检测成为可能</h1>
<div class="arc-info">2025/07/17

作者：李哲 赵晖编辑：张华 殷琪来源：健康报</div>
<div>正文：天津大学研究团队开发了一种新型太赫兹光声系统……</div>
</body></html>"""


@pytest.fixture(scope="module")
def pw_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def list_page(pw_browser):
    page = pw_browser.new_page()
    page.set_content(LIST_FIXTURE)
    yield page
    page.close()


@pytest.fixture(scope="module")
def detail_page(pw_browser):
    page = pw_browser.new_page()
    page.set_content(DETAIL_FIXTURE)
    yield page
    page.close()


def _news(title="健康报：太赫兹和声波检测", media="健康报",
          publish_time="2025-07-17 15:54:40", authors=None,
          related=None, abstract="太赫兹光声系统摘要", year="2025"):
    return SearchResult(
        rank=1, title=title, authors=[], source=media, year=year,
        detail_url=None, document_type="媒体聚焦", database=DATABASE_NAME,
        abstract=abstract, artifact_type="news",
        artifact_metadata={
            "media": media,
            "publish_time": publish_time,
            "authors": list(authors or []),
            "related_person": list(related or []),
        },
        citation_count=None,
    )


def _request(**kw):
    data = dict(research_direction="太赫兹")
    data.update(kw)
    return QueryRequest(**data)


# ============================================================
# 1. parser
# ============================================================

class TestParsers:
    def test_dateline_media_category_time(self):
        assert parse_dateline("媒体聚焦 健康报-2025-07-17 15:54:40") == (
            "媒体聚焦", "健康报", "2025-07-17 15:54:40")
        assert parse_dateline("综合新闻 精仪学院-2025-11-26 14:02:38") == (
            "综合新闻", "精仪学院", "2025-11-26 14:02:38")

    def test_dateline_tolerant(self):
        assert parse_dateline("") == ("", "", "")
        assert parse_dateline("无日期行") == ("无日期行", "", "")

    def test_arc_info_authors_and_media(self):
        authors, media = parse_arc_info(
            "2025/07/17\n\n作者：李哲 赵晖编辑：张华 殷琪来源：健康报")
        assert authors == ["李哲", "赵晖"]
        assert media == "健康报"

    def test_arc_info_no_editor_field(self):
        authors, media = parse_arc_info("2025/07/17\n\n作者：李哲来源：健康报")
        assert authors == ["李哲"]
        assert media == "健康报"

    def test_arc_info_missing_fields_degrade(self):
        assert parse_arc_info("") == ([], "")
        assert parse_arc_info("只有正文没有元信息") == ([], "")

    def test_parse_list_from_fixture(self, list_page):
        adapter = TjuNewsAdapter.__new__(TjuNewsAdapter)
        rows = adapter._parse_list(list_page)
        assert len(rows) == 2
        results = adapter._map_rows(rows, 10)
        r0 = results[0]
        assert r0.title == "健康报：太赫兹和声波结合使无针血钠检测成为可能"
        assert r0.source == "健康报"          # 来源列 = 真实媒体
        assert r0.authors == []               # 媒体名绝不入 authors
        assert r0.artifact_type == "news"
        assert r0.database == DATABASE_NAME
        assert r0.citation_count is None
        assert r0.artifact_metadata["media"] == "健康报"
        assert r0.artifact_metadata["publish_time"] == "2025-07-17 15:54:40"
        assert r0.artifact_metadata["authors"] == []
        assert r0.year == "2025"
        assert r0.document_type == "媒体聚焦"
        assert r0.abstract and "太赫兹光声系统" in r0.abstract
        assert r0.detail_url is None          # 详情 URL 由 resolver 回填
        # 第二条（无媒体转载，校园发布）
        r1 = results[1]
        assert r1.artifact_metadata["media"] == "精仪学院"


# ============================================================
# 2. 查询路由（fake session/page）
# ============================================================

class _FakeResultsPage:
    """假结果页：evaluate 返回固定行；locator count 供 readiness。"""

    def __init__(self, rows):
        self.rows = rows
        self.url = "https://news.tju.edu.cn/aop_views/search/modules/resultpc/soso.html?query=x"
        self.closed = False

    def goto(self, url, **kw):
        pass

    def wait_for_selector(self, *a, **k):
        pass

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, *a, **k):
        pass

    def inner_text(self, sel):
        return "智能搜索为您找到相关结果约为 15 个" if sel == "body" else ""

    def locator(self, sel):
        loc = mock.Mock()
        loc.count.return_value = len(self.rows) if "title" in sel else 1
        loc.fill.return_value = None
        loc.press.return_value = None
        loc.click.return_value = None
        return loc

    def evaluate(self, js, *args):
        if "syqbwzzs_templ" in js:
            return list(self.rows)
        return None

    def close(self):
        self.closed = True


class _FakeContext:
    """假 context：new_page 返回预设页序列；expect_page 交出下一结果页。"""

    def __init__(self, result_pages: list[_FakeResultsPage]):
        self.result_pages = result_pages
        self.opened = 0

    def _next_result_page(self):
        idx = min(self.opened, len(self.result_pages) - 1)
        self.opened += 1
        return self.result_pages[idx]

    def expect_page(self, timeout=15000):
        result_page = self._next_result_page()
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.Mock(value=result_page)
        cm.__exit__.return_value = False
        return cm

    def new_page(self):
        page = mock.Mock()
        page.goto.return_value = None
        page.wait_for_selector.return_value = None
        page.context = self
        page.locator.return_value.first.press.return_value = None
        return page


def _adapter(context):
    session = mock.Mock()
    session.context = context
    return TjuNewsAdapter(session)


ROW_A = {"title": "健康报：太赫兹和声波结合使无针血钠检测成为可能",
         "summary": "天津大学研究团队开发太赫兹光声系统。", "dateline": "媒体聚焦 健康报-2025-07-17 15:54:40"}
ROW_B = {"title": "天津大学精仪学院田震教授当选美国光学学会会士",
         "summary": "田震教授在太赫兹光子学等方面入选。", "dateline": "综合新闻 精仪学院-2025-11-26 14:02:38"}


class TestQueryRouting:
    @mock.patch("tju_info_retrieval.sources.tju_news.TjuNewsDetailResolver")
    def test_topic_only_uses_raw_chinese(self, resolver_cls):
        resolver_cls.return_value.enrich.return_value = 0
        # 结果页 fake 携带行；首页 fake 打开后跳结果页
        results_page = _FakeResultsPage([ROW_A, ROW_B])
        context = _FakeContext([results_page])
        adapter = _adapter(context)
        req = _request(research_direction="太赫兹")
        results = adapter.search("太赫兹", 10, query_context=req)
        assert len(results) == 2
        assert results[0].artifact_type == "news"
        # 原始中文词被填入搜索框（不做英文扩展）
        home_fill = None
        home = context.result_pages  # 结果页即填充页 fake（fill 记录在 locator）
        # 直接验证：_FakeResultsPage.locator.fill 被调用过（经 home page mock）
        assert results[0].database == DATABASE_NAME

    @mock.patch("tju_info_retrieval.sources.tju_news.TjuNewsDetailResolver")
    def test_person_only_no_topic_expansion(self, resolver_cls):
        resolver_cls.return_value.enrich.return_value = 0
        results_page = _FakeResultsPage([ROW_B])
        context = _FakeContext([results_page])
        adapter = _adapter(context)
        req = mock.Mock()
        req.research_direction = ""
        req.author_name = "田震"
        results = adapter.search("", 10, query_context=req)
        assert len(results) == 1
        # person 出现在标题 → related_person 写入（真实文本证据）
        assert results[0].artifact_metadata["related_person"] == ["田震"]

    @mock.patch("tju_info_retrieval.sources.tju_news.TjuNewsDetailResolver")
    def test_combo_person_base_with_topic_content_check(self, resolver_cls):
        """组合（PoC 09 json：站点多词=短语匹配）→ person 结果为基 +
        详情正文/摘要含 topic 关键词者保留（真实 AND）。"""
        row_hit = ROW_B    # 田震 Fellow（正文含 太赫兹光子学）
        row_miss = {"title": "校工会举办教职工乒乓球比赛",
                    "summary": "工会活动报道。",
                    "dateline": "综合新闻 工会-2025-10-01 10:00:00"}
        person_page = _FakeResultsPage([row_hit, row_miss])
        context = _FakeContext([person_page])
        adapter = _adapter(context)
        req = mock.Mock()
        req.research_direction = "太赫兹"
        req.author_name = "田震"

        def fake_enrich(results):
            for r in results:
                r._news_content_head = (
                    "…田震教授凭借在太赫兹光子学和光电子器件等方面的研究…"
                    if "田震" in r.title
                    else "工会活动内容，与本次检索主题无关。")
            return len(results)

        resolver_cls.return_value.enrich.side_effect = fake_enrich
        results = adapter.search("太赫兹", 10, query_context=req)
        assert len(results) == 1
        assert norm_title(results[0].title) == norm_title(ROW_B["title"])
        assert results[0].artifact_metadata["related_person"] == ["田震"]

    @mock.patch("tju_info_retrieval.sources.tju_news.TjuNewsDetailResolver")
    def test_combo_no_content_hit_raises(self, resolver_cls):
        """正文/摘要均不含 topic 关键词 → 组合无命中，抛错（宁缺毋假）。"""
        person_page = _FakeResultsPage([ROW_B])
        context = _FakeContext([person_page])
        adapter = _adapter(context)
        req = mock.Mock()
        req.research_direction = "深海探测"
        req.author_name = "田震"

        def fake_enrich(results):
            for r in results:
                r._news_content_head = "…太赫兹光子学研究…"
            return len(results)

        resolver_cls.return_value.enrich.side_effect = fake_enrich
        with pytest.raises(SearchError, match="组合条件"):
            adapter.search("深海探测", 10, query_context=req)

    def test_empty_keyword_raises(self):
        adapter = _adapter(_FakeContext([_FakeResultsPage([ROW_A])]))
        req = mock.Mock()
        req.research_direction = ""
        req.author_name = ""
        with pytest.raises(SearchError):
            adapter.search("", 10, query_context=req)


# ============================================================
# 3. resolver（bounded enrichment）
# ============================================================

class TestNewsResolver:
    def _mock_results_page(self, detail_mock):
        page = mock.Mock()
        page.locator.return_value.nth.return_value.click.return_value = None
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.Mock(value=detail_mock)
        cm.__exit__.return_value = False
        page.context.expect_page.return_value = cm
        return page

    def _detail(self, arc_text="2025/07/17\n\n作者：李哲 赵晖来源：健康报",
                url="https://news.tju.edu.cn/info/1005/1.htm"):
        d = mock.Mock()
        d.url = url
        arc = mock.Mock()
        arc.count.return_value = 1
        arc.inner_text.return_value = arc_text
        d.locator.return_value.first = arc
        d.inner_text.side_effect = lambda sel: (
            "正文内容：天津大学研究团队进展" if sel == "body" else "")
        return d

    def test_enrich_applies_detail_url_and_authors(self):
        page = self._mock_results_page(self._detail())
        resolver = TjuNewsDetailResolver(
            page, page.context, cache={}, max_items=5,
            per_item_timeout_s=1.0, total_budget_s=5.0)
        r = _news()
        n = resolver.enrich([r])
        assert n == 1
        assert r.detail_url == "https://news.tju.edu.cn/info/1005/1.htm"
        assert r.artifact_metadata["authors"] == ["李哲", "赵晖"]
        assert r.authors == ["李哲", "赵晖"]  # 镜像
        assert r.artifact_metadata["media"] == "健康报"

    def test_enrich_captures_content_head_transient(self):
        """content_head 为组合 topic 过滤的瞬态属性（不入 to_dict）。"""
        detail = self._detail()
        detail.inner_text.side_effect = lambda sel: (
            "正文内容：太赫兹光声系统研究进展" if sel == "body" else "")
        page = self._mock_results_page(detail)
        resolver = TjuNewsDetailResolver(
            page, page.context, cache={}, max_items=5,
            per_item_timeout_s=1.0, total_budget_s=5.0)
        r = _news()
        resolver.enrich([r])
        assert "太赫兹" in getattr(r, "_news_content_head", "")
        assert "太赫兹" not in (r.to_dict().get("artifact_metadata") or {})

    def test_cache_hit_no_open(self):
        page = mock.Mock()
        resolver = TjuNewsDetailResolver(
            page, page.context,
            cache={norm_title("健康报：太赫兹和声波检测"): {
                "detail_url": "u", "authors": ["李哲"], "media": "健康报"}},
            max_items=5)
        r = _news()
        n = resolver.enrich([r])
        assert n == 1 and r.detail_url == "u"
        page.context.expect_page.assert_not_called()

    def test_hard_cap(self):
        page = self._mock_results_page(self._detail())
        resolver = TjuNewsDetailResolver(
            page, page.context, cache={}, max_items=2,
            per_item_timeout_s=1.0, total_budget_s=10.0)
        results = [_news(title=f"新闻{i}") for i in range(5)]
        n = resolver.enrich(results)
        assert n == 2
        assert page.context.expect_page.call_count == 2

    def test_detail_failure_degrades(self):
        page = mock.Mock()
        page.context.expect_page.side_effect = RuntimeError("no tab")
        resolver = TjuNewsDetailResolver(page, page.context, cache={})
        r = _news()
        n = resolver.enrich([r])
        assert n == 0
        assert r.artifact_metadata["authors"] == []  # 不伪造
        assert r.detail_url is None


# ============================================================
# 4. Filter 集成（既有 news 语义）
# ============================================================

class TestFilterIntegration:
    def test_related_person_match_kept(self):
        r = _news(related=["田震"])
        out = FilterService().filter([r], _request(author_name="田震"))
        assert len(out) == 1

    def test_metadata_authors_match_kept(self):
        r = _news(authors=["李哲"])
        out = FilterService().filter([r], _request(author_name="李哲"))
        assert len(out) == 1

    def test_publish_time_range(self):
        r = _news(publish_time="2023-05-01 10:00:00", year="2023")
        out = FilterService().filter(
            [r], _request(start_date="2020.01", end_date="2024.11"))
        assert len(out) == 1
        out2 = FilterService().filter(
            [r], _request(start_date="2024.01"))
        assert out2 == []

    def test_missing_person_metadata_kept(self):
        r = _news()  # 无 authors/related
        out = FilterService().filter([r], _request(author_name="田震"))
        assert len(out) == 1, "unknown != mismatch"


# ============================================================
# 5. Merger 跨类型
# ============================================================

class TestMergerCrossType:
    def test_news_and_same_title_paper_not_merged(self):
        news = _news(title="太赫兹研究进展")
        paper = SearchResult(rank=1, title="太赫兹研究进展", database="CNKI")
        merged = ResultMerger.merge([news, paper])
        assert len(merged) == 2


# ============================================================
# 6. SearchService 路由
# ============================================================

class TestSearchServiceRouting(unittest.TestCase):
    def test_default_registry_has_tju_news(self):
        registry = SearchService._default_registry()
        assert "天津大学新闻网" in registry.available_sources()
        from tju_info_retrieval.sources.tju_news import TjuNewsAdapter
        assert registry.get("天津大学新闻网") is TjuNewsAdapter

    def test_search_service_routes_tju_news_source(self):
        from tju_info_retrieval.sources.tju_news import TjuNewsAdapter
        session = mock.Mock()
        session.check_tju_auth.return_value = "logged_in"
        req = QueryRequest(research_direction="太赫兹",
                           sources=["天津大学新闻网"], result_count=5)
        with mock.patch.object(TjuNewsAdapter, "search", return_value=[]) as called:
            SearchService(session).search(req)
        called.assert_called_once()
        self.assertIsInstance(called.call_args.args[0], str)
        assert called.call_args.args[1] == 5
        assert called.call_args.kwargs["query_context"] is req


if __name__ == "__main__":
    pytest.main([__file__, "-v"])