#!/usr/bin/env python3
"""v0.17 Phase 2.2-C：万方专利 Adapter 测试。

覆盖：
- 列表页 parser（title/applicant/公开号/申请号/date/abstract/法律状态）
- 查询路由（topic/person/组合）与字段语义（发明/设计人、申请/专利权人，
  禁止当普通关键词）
- SearchResult 映射（artifact_type/database/citation_count/metadata）
- resolver（成功/超时降级/失败降级/cache/hard cap/不伪造）
- FilterService 集成（inventor/applicant/date 匹配、缺失保留）
- 回归：论文 WanfangAdapter 行为由既有测试锁定（本文件不改动其对象）
"""
from __future__ import annotations

import sys
import time
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
from tju_info_retrieval.sources.base import SearchError
from tju_info_retrieval.sources.wanfang_patent import (
    DATABASE_NAME,
    WanfangPatentAdapter,
    WanfangPatentMetadataResolver,
    parse_patent_detail_fields,
    parse_patent_list_row,
)

# 列表页 fixture（selector 与 PoC 04/12 json 对齐）
LIST_FIXTURE = """<html><body>
<div class="normal-list">
  <div class="title-area">
    <div class="ajust">
      <span class="index">1.</span>
      <span class="title">一种太赫兹时域光谱检测方法</span>
      <span class="pre-publish">发明公开</span>
      <span class="title-id-hidden">patent_ZL_CN202610546754.X_CN122409566A_20260717</span>
    </div>
  </div>
  <div class="author-area">
    <span class="essay-type">专利</span>
    <span class="t-ML6">发明专利</span>
    <span class="t-ML6">CN122409566A</span>
    <span class="authors">桂林电子科技大学</span>
    <span class="applyDate">申请日：2026-04-23 &nbsp; 公开日：2026-07-17</span>
  </div>
  <div class="abstract-area">摘要：本方法采用太赫兹时域光谱进行定量检测。</div>
</div>
<div class="normal-list">
  <div class="title-area">
    <div class="ajust">
      <span class="index">2.</span>
      <span class="title">太赫兹安检系统</span>
      <span class="sync-publish">发明授权</span>
      <span class="title-id-hidden">patent_ZL_CN202610208835.9_CN121720966B_20260519</span>
    </div>
  </div>
  <div class="author-area">
    <span class="essay-type">专利</span>
    <span class="t-ML6">发明专利</span>
    <span class="t-ML6">CN121720966B</span>
    <span class="authors">安徽中科太赫兹科技有限公司</span>
    <span class="applyDate">申请日：2026-02-13 &nbsp; 公开日：2026-05-19</span>
  </div>
  <div class="abstract-area">摘要：本发明公开了太赫兹安检成像系统。</div>
</div>
</body></html>"""

# 详情页 fixture（og: 前缀 meta，PoC 15 json 取证）
DETAIL_FIXTURE = """<html><head>
<meta property="og:article:author" content="殷贤华,孙傲,李康,游骁璇,马政帅,田义山">
<meta property="og:article:section" content="发明专利">
<meta property="og:article:tag" content="G01N21/3586,G01N21/3563,G06N20/00">
<meta property="og:article:published_time" content="2026-07-17 00:00:00">
</head><body><div>详情页</div></body></html>"""


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


def _patent_result(title="一种太赫兹检测方法", applicant="天津大学",
                   pub="CN122409566A", app="CN202610546754.X",
                   date="2026-07-17", inventors=None, year="2026"):
    return SearchResult(
        rank=1, title=title, authors=[], source=applicant, year=year,
        detail_url=None, document_type="发明公开", database=DATABASE_NAME,
        artifact_type="patent",
        artifact_metadata={
            "inventors": list(inventors or []),
            "applicant": applicant,
            "publication_number": pub,
            "application_number": app,
            "date": date,
        },
        citation_count=None,
    )


def _request(**kw):
    data = dict(research_direction="太赫兹")
    data.update(kw)
    return QueryRequest(**data)


# ============================================================
# 1. 列表页 parser
# ============================================================

class TestListParser:
    def test_row_pure_function_fields(self):
        fields = parse_patent_list_row({
            "title": "太赫兹检测方法", "status": "发明公开",
            "tml6": ["发明专利", "CN122409566A"],
            "applicant": "桂林电子科技大学",
            "apply_date": "申请日：2026-04-23   公开日：2026-07-17",
            "abstract": "摘要：本方法…",
            "id_hidden": "patent_ZL_CN202610546754.X_CN122409566A_20260717",
        })
        assert fields["title"] == "太赫兹检测方法"
        assert fields["document_type"] == "发明公开"
        assert fields["applicant"] == "桂林电子科技大学"
        assert fields["publication_number"] == "CN122409566A"
        assert fields["application_number"] == "CN202610546754.X"
        assert fields["date"] == "2026-07-17"  # 公开日优先
        assert fields["abstract"] == "本方法…"

    def test_row_pure_function_empty_id_falls_back_tml6(self):
        fields = parse_patent_list_row({
            "title": "X", "status": "", "tml6": ["发明专利", "CN121720966B"],
            "applicant": "A", "apply_date": "", "abstract": "",
            "id_hidden": "",
        })
        assert fields["publication_number"] == "CN121720966B"
        assert fields["date"] is None

    def test_parse_list_from_fixture(self, list_page):
        adapter = WanfangPatentAdapter.__new__(WanfangPatentAdapter)
        results = adapter._parse_list(list_page, 10)
        assert len(results) == 2
        r0 = results[0]
        assert r0.title == "一种太赫兹时域光谱检测方法"
        assert r0.source == "桂林电子科技大学"  # source = 申请人（展示语义）
        assert r0.authors == []                 # 不镜像 applicant
        assert r0.artifact_type == "patent"
        assert r0.database == DATABASE_NAME
        assert r0.citation_count is None
        assert r0.artifact_metadata["applicant"] == "桂林电子科技大学"
        assert r0.artifact_metadata["publication_number"] == "CN122409566A"
        assert r0.artifact_metadata["application_number"] == "CN202610546754.X"
        assert r0.artifact_metadata["date"] == "2026-07-17"
        assert r0.year == "2026"
        assert r0.document_type == "发明公开"
        assert r0.abstract == "本方法采用太赫兹时域光谱进行定量检测。"
        assert r0.detail_url is None  # 详情 URL 由 resolver 回填

    def test_max_count(self, list_page):
        adapter = WanfangPatentAdapter.__new__(WanfangPatentAdapter)
        assert len(adapter._parse_list(list_page, 1)) == 1


# ============================================================
# 2/3. 查询路由 + 字段语义（fake page 状态机）
# ============================================================

class _FakeLocator:
    def __init__(self, page, sel, idx=None):
        self.page = page
        self.sel = sel
        self.idx = idx

    @property
    def first(self):
        # 保留父级 nth(i) 行索引（scoped .first 语义）
        return _FakeLocator(self.page, self.sel, idx=self.idx)

    def nth(self, i):
        return _FakeLocator(self.page, self.sel, idx=i)

    def locator(self, sel):
        return _FakeLocator(self.page, f"{self.sel} {sel}", idx=self.idx)

    def count(self):
        return self.page.locator_count(self.sel)

    def click(self, **kw):
        self.page.events.append(("click", self.sel, self.idx))
        self.page.on_click(self.sel, self.idx)

    def fill(self, text, **kw):
        self.page.events.append(("fill", self.sel, None, text))

    def clear(self, **kw):
        self.page.events.append(("clear", self.sel, None))

    def press_sequentially(self, text, **kw):
        self.page.events.append(("press_sequentially", self.sel, self.idx, text))
        self.page.on_type(self.sel, self.idx, text)


class _FakePage:
    """专利检索假页：landing → 普通检索/高级检索 → 提交 → 结果。"""

    DEFAULT_FIELDS = [None, None, "主题", "题名", "摘要",
                      None, None, None, None, None, None]

    def __init__(self, sync_inputs=True):
        self.url = "https://p.lib.tju.edu.cn/resource"
        self.events: list = []
        self.row_fields = list(self.DEFAULT_FIELDS)
        self.input_values = [""] * len(self.row_fields)
        self.sync_inputs = sync_inputs
        self.title_count = 0
        self.advanced_visited = False
        self.visible_positions = [
            i for i, f in enumerate(self.row_fields) if f is not None
        ]

    def locator(self, sel):
        return _FakeLocator(self, sel)

    def fill(self, sel, text, **kw):
        self.events.append(("fill", sel, None, text))

    def goto(self, url, **kw):
        self.events.append(("goto", url, None, None))
        self.advanced_visited = True
        if "/advanced-search/patent" in url:
            self.url = "https://x.p.lib.tju.edu.cn/advanced-search/patent?t=1"
        else:
            self.url = url

    def evaluate(self, js, *args):
        if "rows.push" in js:
            rows = []
            for i, fld in enumerate(self.row_fields):
                if fld is None:
                    continue
                rows.append({"field": fld,
                             "value": self.input_values[i]})
            return rows
        if "loading" in js:
            return False
        if "normal-list" in js:
            return [{
                "title": "一种太赫兹专利",
                "status": "发明公开",
                "tml6": ["发明专利", "CN122409566A"],
                "applicant": "天津大学",
                "apply_date": "申请日：2026-04-23   公开日：2026-07-17",
                "abstract": "摘要：测试摘要",
                "id_hidden": "patent_ZL_CN202610546754.X_CN122409566A_20260717",
            }]
        return None

    def wait_for_timeout(self, ms):
        pass

    def wait_for_selector(self, *a, **k):
        pass

    def wait_for_load_state(self, *a, **k):
        pass

    def inner_text(self, sel):
        return "找到59条" if sel == "body" else ""

    def locator_count(self, sel):
        if "title-area span.title" in sel or "normal-list" in sel:
            return self.title_count
        return 1

    def on_click(self, sel, idx):
        if ".search-option.field" in sel and "ivu-select-item" in sel:
            field = sel.split(":text-is('", 1)[1].split("')", 1)[0]
            if idx is not None and 0 <= idx < len(self.visible_positions):
                self.row_fields[self.visible_positions[idx]] = field
        if "submit-btn" in sel:
            self.title_count = 1
            self.url = "https://x.p.lib.tju.edu.cn/advanced-search/patent?t=2"
        if "检索" in sel and "button" in sel:
            self.url = "https://x.p.lib.tju.edu.cn/paper?q=nav"
        if "text-is('专利')" in sel:
            self.url = "https://x.p.lib.tju.edu.cn/patent?q=x&p=1"
            self.title_count = 1

    def on_type(self, sel, idx, text):
        if "ivu-input" in sel and self.sync_inputs:
            if idx is not None and idx < len(self.visible_positions):
                self.input_values[self.visible_positions[idx]] = text


def _adapter(fake):
    adapter = WanfangPatentAdapter(mock.Mock())
    portal = mock.Mock()
    portal.open_portal.return_value = fake
    portal.open_resource.return_value = fake
    adapter._portal = portal
    return adapter


def _types(events):
    return [(e[2], e[3]) for e in events if e[0] == "press_sequentially"]


class TestQueryRouting:
    @mock.patch("tju_info_retrieval.sources.wanfang_patent.WanfangPatentMetadataResolver")
    def test_topic_only_plain_path(self, resolver_cls):
        fake = _FakePage()
        resolver_cls.return_value.enrich.return_value = 0
        adapter = _adapter(fake)
        with mock.patch("tju_info_retrieval.sources.wanfang_patent.time.sleep"):
            results = adapter.search("太赫兹", 10, query_context=_request())
        # 普通路径：landing 搜索框填 太赫兹、无高级检索、无字段切换
        assert not fake.advanced_visited
        fills = [e for e in fake.events if e[0] == "fill"]
        assert any(e[1] == "#search-input" and e[3] == "太赫兹" for e in fills)
        assert not any(
            ".search-option.field" in e[1] for e in fake.events if e[0] == "click"
        ), "topic-only 不得进行字段切换"
        resolver_cls.assert_called_once()

    @mock.patch("tju_info_retrieval.sources.wanfang_patent.WanfangPatentMetadataResolver")
    @pytest.mark.parametrize("person,affiliation,expected_fields,typed", [
        ("殷贤华", "", ["专利-发明/设计人"], [("太赫兹", "殷贤华")]),
        # topic+person: 主题(row0默认) + 发明/设计人(row1)
        # person+affiliation: row0 发明/设计人 + row1 申请/专利权人
    ])
    def test_person_uses_inventor_field(self, resolver_cls, person, affiliation,
                                        expected_fields, typed):
        fake = _FakePage()
        resolver_cls.return_value.enrich.return_value = 0
        adapter = _adapter(fake)
        req = _request(author_name=person, author_affiliation=affiliation)
        with mock.patch("tju_info_retrieval.sources.wanfang_patent.time.sleep"):
            adapter.search("太赫兹", 10, query_context=req)
        assert fake.advanced_visited, "person 必须进高级检索，不得当普通关键词"
        clicks = [e[1] for e in fake.events if e[0] == "click"]
        for f in expected_fields:
            assert any(f in c for c in clicks), f"应切换字段 {f}"

    @mock.patch("tju_info_retrieval.sources.wanfang_patent.WanfangPatentMetadataResolver")
    def test_person_only_no_topic_uses_inventor_row0(self, resolver_cls):
        fake = _FakePage()
        resolver_cls.return_value.enrich.return_value = 0
        adapter = _adapter(fake)
        req = mock.Mock()
        req.research_direction = ""
        req.author_name = "殷贤华"
        req.author_affiliation = ""
        with mock.patch("tju_info_retrieval.sources.wanfang_patent.time.sleep"):
            adapter.search("", 10, query_context=req)
        assert fake.advanced_visited
        # 空主题：row0 切发明/设计人，仅作者名被逐键输入
        types = [e[3] for e in fake.events if e[0] == "press_sequentially"]
        assert "殷贤华" in types
        assert "太赫兹" not in types

    @mock.patch("tju_info_retrieval.sources.wanfang_patent.WanfangPatentMetadataResolver")
    def test_person_affiliation_two_fields(self, resolver_cls):
        fake = _FakePage()
        resolver_cls.return_value.enrich.return_value = 0
        adapter = _adapter(fake)
        req = mock.Mock()
        req.research_direction = ""
        req.author_name = "殷贤华"
        req.author_affiliation = "天津大学"
        with mock.patch("tju_info_retrieval.sources.wanfang_patent.time.sleep"):
            adapter.search("", 10, query_context=req)
        clicks = [e[1] for e in fake.events if e[0] == "click"]
        assert any("专利-发明/设计人" in c for c in clicks)
        assert any("专利-申请/专利权人" in c for c in clicks)
        types = [e[3] for e in fake.events if e[0] == "press_sequentially"]
        assert "殷贤华" in types and "天津大学" in types

    @mock.patch("tju_info_retrieval.sources.wanfang_patent.WanfangPatentMetadataResolver")
    def test_topic_person_affiliation_three_conditions(self, resolver_cls):
        fake = _FakePage()
        resolver_cls.return_value.enrich.return_value = 0
        adapter = _adapter(fake)
        req = _request(author_name="殷贤华", author_affiliation="天津大学")
        with mock.patch("tju_info_retrieval.sources.wanfang_patent.time.sleep"):
            adapter.search("太赫兹", 10, query_context=req)
        clicks = [e[1] for e in fake.events if e[0] == "click"]
        assert any("专利-发明/设计人" in c for c in clicks)
        assert any("专利-申请/专利权人" in c for c in clicks)
        types = [e[3] for e in fake.events if e[0] == "press_sequentially"]
        assert "太赫兹" in types and "殷贤华" in types and "天津大学" in types

    @mock.patch("tju_info_retrieval.sources.wanfang_patent.WanfangPatentMetadataResolver")
    def test_pre_submit_readback_aborts_on_unsynced(self, resolver_cls):
        fake = _FakePage(sync_inputs=False)
        resolver_cls.return_value.enrich.return_value = 0
        adapter = _adapter(fake)
        req = _request(author_name="殷贤华")
        with mock.patch("tju_info_retrieval.sources.wanfang_patent.time.sleep"):
            with pytest.raises(SearchError, match="未就绪"):
                adapter.search("太赫兹", 10, query_context=req)


# ============================================================
# 4. resolver（详情 og meta 解析 + bounded enrichment）
# ============================================================

class TestDetailParser:
    def test_parse_inventors_from_og_meta(self, detail_page):
        fields = parse_patent_detail_fields(detail_page)
        assert fields["inventors"] == ["殷贤华", "孙傲", "李康", "游骁璇", "马政帅", "田义山"]
        assert fields["patent_type"] == "发明专利"
        assert fields["ipc"] == ["G01N21/3586", "G01N21/3563", "G06N20/00"]
        assert fields["published_time"] == "2026-07-17 00:00:00"

    def test_missing_meta_degrade(self, pw_browser):
        page = pw_browser.new_page()
        page.set_content("<html><body>无 meta</body></html>")
        fields = parse_patent_detail_fields(page)
        assert fields["inventors"] == []
        assert fields["patent_type"] is None
        page.close()


class TestResolver:
    def _mock_results_page(self, detail_mock):
        page = mock.Mock()
        page.locator.return_value.nth.return_value.click.return_value = None
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.Mock(value=detail_mock)
        cm.__exit__.return_value = False
        page.context.expect_page.return_value = cm
        return page

    def _detail(self, inventors=None, url="https://d/1"):
        d = mock.Mock()
        metas = {
            "meta[property='og:article:author']": ",".join(inventors) if inventors else None,
            "meta[property='og:article:section']": "发明专利",
            "meta[property='og:article:tag']": "G01N21/3586",
            "meta[property='og:article:published_time']": "2026-07-17 00:00:00",
        }
        d.get_attribute.side_effect = lambda sel, _k: metas.get(sel)
        d.url = url
        return d

    def test_enrich_applies_inventors_and_url(self):
        detail = self._detail(inventors=["殷贤华", "孙傲"])
        page = self._mock_results_page(detail)
        resolver = WanfangPatentMetadataResolver(
            page, cache={}, max_items=5,
            per_item_timeout_s=1.0, total_budget_s=5.0)
        r = _patent_result(inventors=[])
        n = resolver.enrich([r])
        assert n == 1
        assert r.artifact_metadata["inventors"] == ["殷贤华", "孙傲"]
        assert r.authors == ["殷贤华", "孙傲"]  # 镜像（UI 兼容）
        assert r.detail_url == "https://d/1"

    def test_cache_hit_no_open(self):
        page = mock.Mock()  # 无 context.expect_page
        resolver = WanfangPatentMetadataResolver(
            page, cache={"一种太赫兹检测方法": {"inventors": ["陈远"], "detail_url": "u"}},
            max_items=5)
        r = _patent_result(inventors=[])
        n = resolver.enrich([r])
        assert n == 1 and r.artifact_metadata["inventors"] == ["陈远"]

    def test_hard_cap_limits_detail_opens(self):
        detail = self._detail(inventors=["陈远"])
        page = self._mock_results_page(detail)
        resolver = WanfangPatentMetadataResolver(
            page, cache={}, max_items=2,
            per_item_timeout_s=1.0, total_budget_s=10.0)
        results = [_patent_result(title=f"专利{i}") for i in range(5)]
        n = resolver.enrich(results)
        assert n == 2
        assert page.context.expect_page.call_count == 2

    def test_detail_failure_degrades(self):
        page = mock.Mock()
        page.context.expect_page.side_effect = RuntimeError("no tab")
        resolver = WanfangPatentMetadataResolver(page, cache={})
        r = _patent_result()
        n = resolver.enrich([r])
        assert n == 0
        assert r.artifact_metadata["inventors"] == []  # 不伪造

    def test_not_fabricate_when_no_inventors(self):
        # og meta 全缺失：无可应用内容，不打开缓存、不伪造 inventors
        detail = mock.Mock()
        detail.get_attribute.return_value = None
        detail.url = "https://d/1"
        page = self._mock_results_page(detail)
        resolver = WanfangPatentMetadataResolver(
            page, cache={}, max_items=5,
            per_item_timeout_s=1.0, total_budget_s=5.0)
        r = _patent_result()
        n = resolver.enrich([r])
        assert n == 0
        assert r.artifact_metadata["inventors"] == []
        assert r.authors == []


# ============================================================
# 5. FilterService 集成
# ============================================================

class TestFilterIntegration:
    def test_inventor_match_kept(self):
        r = _patent_result(inventors=["殷贤华"])
        out = FilterService().filter([r], _request(author_name="殷贤华"))
        assert len(out) == 1

    def test_applicant_match_kept(self):
        r = _patent_result(applicant="天津大学")
        out = FilterService().filter([r], _request(author_affiliation="天津大学"))
        assert len(out) == 1

    def test_date_range_match(self):
        r = _patent_result(date="2023-05-01", year="2023")
        out = FilterService().filter(
            [r], _request(start_date="2020.01", end_date="2024.11"))
        assert len(out) == 1

    def test_missing_inventors_kept_unknown(self):
        r = _patent_result(inventors=[])
        out = FilterService().filter([r], _request(author_name="殷贤华"))
        assert len(out) == 1, "unknown != mismatch：缺失发明人保留"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])