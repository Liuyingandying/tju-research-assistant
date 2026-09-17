#!/usr/bin/env python3
"""v0.17 Phase 2.2-F：CNKI 专利 Adapter 测试。

覆盖：列表 parser（title/inventors/applicant/date/url/status）、查询路由
（SU/AU/SQR + 组合 local intersection）、SearchResult 映射、详情解析
（publication_number/application_number/IPC/abstract）、resolver
（success/cache/timeout/CAPTCHA degrade/hard cap）、Filter 集成、
Offline Summary 集成、SearchService 路由。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.filtering import FilterService
from tju_info_retrieval.services.offline_summary import OfflineSummaryEngine
from tju_info_retrieval.services.search_service import SearchService
from tju_info_retrieval.sources.base import SearchError
from tju_info_retrieval.sources.cnki_patent import (
    DATABASE_NAME,
    CnkiPatentAdapter,
    CnkiPatentMetadataResolver,
    parse_detail_abstract,
    parse_detail_fields,
    parse_detail_row,
)

# 列表 fixture（selector 与探针 05 json 对齐）
LIST_FIXTURE = """<html><body>
智能搜索：太赫兹，找到 30,800 条
<table class="result-table-list">
<tr><th>专利名称</th><th>发明人</th><th>申请人</th><th>数据库</th><th>申请日</th><th>公开日</th></tr>
<tr class="odd">
  <td class="name"><a class="fz14 inline" href="https://kns.cnki.net/kcms2/article/abstract?v=AAA">一种机器人用户坐标系校准装置及方法</a>
      <b class="marktip">发明授权</b></td>
  <td class="inventor"><a>钟舜聪;</a><a>易深海;</a><a>李劲林;</a></td>
  <td class="applicant">福州大学</td>
  <td class="data">中国专利</td>
  <td class="date">2023-12-18</td>
  <td class="date">2026-09-01</td>
</tr>
<tr>
  <td class="name"><a class="fz14 inline" href="https://kns.cnki.net/kcms2/article/abstract?v=BBB">一种太赫兹双光梳光谱仪稳定控制系统及方法</a>
      <b class="marktip">发明授权</b></td>
  <td class="inventor"><a>曾和平;</a><a>李敏;</a></td>
  <td class="applicant">华东师范大学</td>
  <td class="data">中国专利</td>
  <td class="date">2023-01-13</td>
  <td class="date">2026-09-01</td>
</tr>
</table>
</body></html>"""

# 详情 fixture（探针 17 json：span.rowtit 行 + 摘要）
DETAIL_FIXTURE = """<html><body>
<div>申请(专利)号： CN202311735382.8</div>
<div>授权公告号： CN118081733B</div>
<div>申请日： 2023-12-18</div>
<div>授权公告日： 2026-09-01</div>
<div>申请人： 福州大学</div>
<div>发明人： 钟舜聪; 易深海</div>
<div>主分类号： B25J9/16</div>
<div>分类号： B25J9/16;G01B21/04</div>
<div>摘要： 本发明提供了一种机器人用户坐标系校准装置及方法，装置包括机器人、PSD夹具、二维PSD位置传感器、激光光源、三维位移台、采集卡和上位机。所述PSD夹具固定在机器人末端，通过三点示教法标定用户坐标系。</div>
</body></html>"""


class _FakeResultsPage:
    def __init__(self, rows):
        self.rows = rows
        self.url = "https://kns.cnki.net/kns8s/search?classid=VUDIXAIY&kw=x"
        self.closed = False

    def goto(self, url, **kw):
        self.url = url

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, *a, **k):
        pass

    def inner_text(self, sel):
        return "找到 30,800 条" if sel == "body" else ""

    def locator(self, sel):
        loc = mock.Mock()
        loc.count.return_value = len(self.rows)
        return loc

    def evaluate(self, js, *args):
        if "result-table-list" in js:
            return list(self.rows)
        return None

    def close(self):
        self.closed = True


class _PagedResultsPage:
    """按 click 切换 fixture 页，供 bounded paging 行为测试。"""

    def __init__(self, pages):
        self.pages = pages
        self.page_index = 0
        self.page_requests = 0
        self.url = "https://kns.cnki.net/kns8s/search?page=1"
        self.context = mock.Mock()

    def goto(self, url, **kw):
        self.url = url
        self.page_index = 0

    def wait_for_timeout(self, ms):
        pass

    def inner_text(self, sel):
        return "找到 30,800 条" if sel == "body" else ""

    def locator(self, sel):
        loc = mock.Mock()
        if "下一页" in sel:
            has_next = self.page_index + 1 < len(self.pages)
            loc.count.return_value = 1 if has_next else 0
            loc.is_visible.return_value = has_next
            loc.click.side_effect = self._next
            loc.first = loc
        else:
            loc.count.return_value = len(self.pages[self.page_index])
        return loc

    def _next(self, **kw):
        self.page_index += 1
        self.page_requests += 1
        self.url = f"https://kns.cnki.net/kns8s/search?page={self.page_index + 1}"

    def evaluate(self, js, *args):
        return list(self.pages[self.page_index])


def _row(title="一种机器人用户坐标系校准装置及方法",
         inventors=("钟舜聪", "易深海", "李劲林"), applicant="福州大学",
         url="https://kns.cnki.net/kcms2/article/abstract?v=AAA",
         status="发明授权", dates=("2023-12-18", "2026-09-01")):
    return {"title": title, "url": url, "status": status,
            "inventors": list(inventors), "applicant": applicant,
            "dates": list(dates)}


def _patent_result(title="一种机器人用户坐标系校准装置及方法",
                   inventors=("钟舜聪",), applicant="福州大学",
                   date="2026-09-01", url="https://kns.cnki.net/kcms2/article/abstract?v=AAA"):
    return SearchResult(
        rank=1, title=title, authors=list(inventors), source=DATABASE_NAME,
        year="2026", detail_url=url, document_type="发明授权",
        database=DATABASE_NAME, artifact_type="patent",
        artifact_metadata={
            "inventors": list(inventors), "applicant": applicant,
            "publication_number": None, "application_number": None,
            "date": date,
        }, citation_count=None)


def _request(**kw):
    data = dict(research_direction="太赫兹")
    data.update(kw)
    return QueryRequest(**data)


# ============================================================
# 1. 列表 parser
# ============================================================

class TestListParser(unittest.TestCase):
    def test_parse_list_from_fixture(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.set_content(LIST_FIXTURE)
            adapter = CnkiPatentAdapter.__new__(CnkiPatentAdapter)
            rows = adapter._parse_list(page)
            browser.close()
        assert len(rows) == 2
        r0 = rows[0]
        assert r0["title"] == "一种机器人用户坐标系校准装置及方法"
        assert r0["inventors"] == ["钟舜聪", "易深海", "李劲林"]
        assert r0["applicant"] == "福州大学"
        assert r0["dates"] == ["2023-12-18", "2026-09-01"]
        assert r0["url"].startswith("https://kns.cnki.net/kcms2/article/abstract?v=")
        assert r0["status"] == "发明授权"

    def test_map_rows(self):
        adapter = CnkiPatentAdapter.__new__(CnkiPatentAdapter)
        results = adapter._map_rows([_row(), _row(title="第二种专利",
                                                  inventors=("曾和平",),
                                                  applicant="华东师范大学",
                                                  url="https://kns.cnki.net/kcms2/article/abstract?v=BBB")], 10)
        assert len(results) == 2
        r0 = results[0]
        assert r0.artifact_type == "patent"
        assert r0.database == DATABASE_NAME
        assert r0.authors == ["钟舜聪", "易深海", "李劲林"]  # 发明人镜像
        assert r0.artifact_metadata["inventors"] == ["钟舜聪", "易深海", "李劲林"]
        assert r0.artifact_metadata["applicant"] == "福州大学"
        assert r0.artifact_metadata["date"] == "2026-09-01"  # 公开日优先
        assert r0.citation_count is None
        assert r0.year == "2026"
        assert r0.detail_url == "https://kns.cnki.net/kcms2/article/abstract?v=AAA"

    def test_max_count(self):
        adapter = CnkiPatentAdapter.__new__(CnkiPatentAdapter)
        rows = [_row(title=f"专利{i}", url=f"u{i}") for i in range(3)]
        assert len(adapter._map_rows(rows, 2)) == 2


# ============================================================
# 2. 查询路由（SU/AU/SQR + 组合 local intersection）
# ============================================================

class TestQueryRouting(unittest.TestCase):
    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_topic_uses_su(self, resolver_cls):
        resolver_cls.return_value.enrich.return_value = 0
        adapter = CnkiPatentAdapter(mock.Mock())
        page = _FakeResultsPage([_row()])
        adapter._session = mock.Mock(page=mock.Mock(return_value=page))
        adapter._session.page.return_value.context = mock.Mock()
        results = adapter.search("太赫兹", 10, query_context=_request())
        assert "korder=SU" in page.url
        assert len(results) == 1

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_person_uses_au(self, resolver_cls):
        resolver_cls.return_value.enrich.return_value = 0
        adapter = CnkiPatentAdapter(mock.Mock())
        page = _FakeResultsPage([_row()])
        adapter._session = mock.Mock(page=mock.Mock(return_value=page))
        adapter._session.page.return_value.context = mock.Mock()
        req = mock.Mock()
        req.research_direction = ""
        req.author_name = "钟舜聪"
        req.author_affiliation = ""
        adapter.search("", 10, query_context=req)
        assert "korder=AU" in page.url and "钟舜聪" in unquote(page.url)

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_affiliation_uses_sqr(self, resolver_cls):
        resolver_cls.return_value.enrich.return_value = 0
        adapter = CnkiPatentAdapter(mock.Mock())
        page = _FakeResultsPage([_row()])
        adapter._session = mock.Mock(page=mock.Mock(return_value=page))
        adapter._session.page.return_value.context = mock.Mock()
        req = mock.Mock()
        req.research_direction = ""
        req.author_name = ""
        req.author_affiliation = "天津大学"
        adapter.search("", 10, query_context=req)
        assert "korder=SQR" in page.url and "天津大学" in unquote(page.url)

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_topic_person_local_intersection(self, resolver_cls):
        resolver_cls.return_value.enrich.return_value = 0
        adapter = CnkiPatentAdapter(mock.Mock())
        rows = [
            _row(title="一种机器人用户坐标系校准装置及方法", inventors=("钟舜聪",)),
            _row(title="太赫兹双光梳光谱仪稳定控制系统及方法",
                 inventors=("曾和平", "钟舜聪"),
                 url="https://kns.cnki.net/kcms2/article/abstract?v=BBB"),
            _row(title="无关专利标题", inventors=("钟舜聪",),
                 url="https://kns.cnki.net/kcms2/article/abstract?v=CCC"),
        ]
        page = _FakeResultsPage(rows)
        adapter._session = mock.Mock(page=mock.Mock(return_value=page))
        adapter._session.page.return_value.context = mock.Mock()
        req = mock.Mock()
        req.research_direction = "太赫兹"
        req.author_name = "钟舜聪"
        req.author_affiliation = ""
        results = adapter.search("太赫兹", 10, query_context=req)
        # AU 基准 + 标题含 太赫兹 的 local intersection
        assert "korder=AU" in page.url
        assert len(results) == 1
        assert results[0].title == "太赫兹双光梳光谱仪稳定控制系统及方法"

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_person_applicant_local_intersection(self, resolver_cls):
        resolver_cls.return_value.enrich.return_value = 0
        adapter = CnkiPatentAdapter(mock.Mock())
        rows = [
            _row(title="专利A", inventors=("钟舜聪",), applicant="福州大学"),
            _row(title="专利B", inventors=("张三",), applicant="福州大学",
                 url="https://kns.cnki.net/kcms2/article/abstract?v=BBB"),
        ]
        page = _FakeResultsPage(rows)
        adapter._session = mock.Mock(page=mock.Mock(return_value=page))
        adapter._session.page.return_value.context = mock.Mock()
        req = mock.Mock()
        req.research_direction = ""
        req.author_name = "钟舜聪"
        req.author_affiliation = "福州大学"
        results = adapter.search("", 10, query_context=req)
        assert "korder=AU" in page.url  # hotfix：person 优先于 applicant
        assert len(results) == 1
        assert results[0].title == "专利A"

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_topic_applicant_su_base_and_double_verify(self, resolver_cls):
        """Hotfix 根因回归：topic+applicant 必须 base=SU 且双条件 positive-match。"""
        def fake_enrich(results):
            # 模拟 bounded 详情摘要（case B 摘要无 topic；case C 无摘要）
            for r in results:
                if "机器人" in r.title:
                    r.abstract = "本发明涉及机器人控制领域的方法。"
            return len(results)
        resolver_cls.return_value.enrich.side_effect = fake_enrich
        adapter = CnkiPatentAdapter(mock.Mock())
        rows = [
            _row(title="太赫兹探测器及制备方法", applicant="天津大学",
                 inventors=("张三",), url="https://kns.cnki.net/kcms2/article/abstract?v=A"),
            _row(title="机器人控制方法", applicant="天津大学",
                 inventors=("李四",), url="https://kns.cnki.net/kcms2/article/abstract?v=B"),
            _row(title="手术器械定位方法", applicant="天津大学",
                 inventors=("王五",), url="https://kns.cnki.net/kcms2/article/abstract?v=C"),
            _row(title="太赫兹光谱分析系统", applicant="其他大学",
                 inventors=("赵六",), url="https://kns.cnki.net/kcms2/article/abstract?v=D"),
        ]
        page = _FakeResultsPage(rows)
        adapter._session = mock.Mock(page=mock.Mock(return_value=page))
        adapter._session.page.return_value.context = mock.Mock()
        req = mock.Mock()
        req.research_direction = "太赫兹"
        req.author_name = ""
        req.author_affiliation = "天津大学"
        results = adapter.search("太赫兹", 10, query_context=req)
        # base = SU(topic)（Hotfix 禁止 SQR 基准）
        assert "korder=SU" in page.url
        # 仅 A 保留：标题含太赫兹 且 申请人=天津大学
        # B（摘要无太赫兹）C（无摘要=UNKNOWN）D（申请人不符）均删除
        assert len(results) == 1
        assert results[0].title == "太赫兹探测器及制备方法"
        assert results[0].artifact_metadata["applicant"] == "天津大学"

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_topic_applicant_unknown_abstract_removed(self, resolver_cls):
        """topic+applicant：标题无 topic、摘要未取得（UNKNOWN）→ 删除。"""
        resolver_cls.return_value.enrich.return_value = 0  # 无摘要
        adapter = CnkiPatentAdapter(mock.Mock())
        rows = [
            _row(title="电池负极材料改性方法", applicant="天津大学",
                 inventors=("张三",), url="https://kns.cnki.net/kcms2/article/abstract?v=E"),
        ]
        page = _FakeResultsPage(rows)
        adapter._session = mock.Mock(page=mock.Mock(return_value=page))
        adapter._session.page.return_value.context = mock.Mock()
        req = mock.Mock()
        req.research_direction = "太赫兹"
        req.author_name = ""
        req.author_affiliation = "天津大学"
        assert adapter.search("太赫兹", 10, query_context=req) == []

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_topic_person_applicant_three_conditions(self, resolver_cls):
        resolver_cls.return_value.enrich.return_value = 0
        adapter = CnkiPatentAdapter(mock.Mock())
        rows = [
            _row(title="太赫兹探测器", inventors=("钟舜聪",), applicant="福州大学",
                 url="https://kns.cnki.net/kcms2/article/abstract?v=F"),
            _row(title="太赫兹探测器", inventors=("钟舜聪",), applicant="其他单位",
                 url="https://kns.cnki.net/kcms2/article/abstract?v=G"),
            _row(title="机器人控制", inventors=("钟舜聪",), applicant="福州大学",
                 url="https://kns.cnki.net/kcms2/article/abstract?v=H"),
        ]
        page = _FakeResultsPage(rows)
        adapter._session = mock.Mock(page=mock.Mock(return_value=page))
        adapter._session.page.return_value.context = mock.Mock()
        req = mock.Mock()
        req.research_direction = "太赫兹"
        req.author_name = "钟舜聪"
        req.author_affiliation = "福州大学"
        results = adapter.search("太赫兹", 10, query_context=req)
        assert "korder=AU" in page.url
        assert len(results) == 1
        assert results[0].title == "太赫兹探测器"
        assert results[0].artifact_metadata["applicant"] == "福州大学"

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_person_applicant_applicant_verified(self, resolver_cls):
        """person+applicant：applicant 必须 positive-match（缺失/不符删除）。"""
        resolver_cls.return_value.enrich.return_value = 0
        adapter = CnkiPatentAdapter(mock.Mock())
        rows = [
            _row(title="专利A", inventors=("钟舜聪",), applicant="福州大学",
                 url="https://kns.cnki.net/kcms2/article/abstract?v=I"),
            _row(title="专利B", inventors=("钟舜聪",), applicant="其他单位",
                 url="https://kns.cnki.net/kcms2/article/abstract?v=J"),
            _row(title="专利C", inventors=("张三",), applicant="福州大学",
                 url="https://kns.cnki.net/kcms2/article/abstract?v=K"),
        ]
        page = _FakeResultsPage(rows)
        adapter._session = mock.Mock(page=mock.Mock(return_value=page))
        adapter._session.page.return_value.context = mock.Mock()
        req = mock.Mock()
        req.research_direction = ""
        req.author_name = "钟舜聪"
        req.author_affiliation = "福州大学"
        results = adapter.search("", 10, query_context=req)
        assert len(results) == 1
        assert results[0].title == "专利A"

    def test_no_keyword_raises(self):
        adapter = CnkiPatentAdapter(mock.Mock())
        req = mock.Mock()
        req.research_direction = ""
        req.author_name = ""
        req.author_affiliation = ""
        with self.assertRaises(SearchError):
            adapter.search("", 10, query_context=req)


# ============================================================
# 3. 详情解析与 resolver
# ============================================================

class TestPagination(unittest.TestCase):
    """bounded 分页（Hotfix 召回修正）：去重 + 页数上限。"""

    class _PagyPage:
        def __init__(self):
            self.calls = 0
            self.url = "u0"

        def locator(self, sel):
            loc = mock.Mock()
            loc.count.return_value = 1
            loc.is_visible.return_value = True
            loc.click.side_effect = self._clicked
            loc.first = loc  # .first 返回自身（携带副作用）
            return loc

        def _clicked(self, **kw):
            self.calls += 1
            self.url = f"u{self.calls}"

        def wait_for_timeout(self, ms):
            pass

        def evaluate(self, js, *args):
            # 模拟 result-table-list 行提取（标题随翻页次数变化）
            base = self.calls * 20
            return [{"title": f"专利{base + i}", "url": f"u{self.calls}_{i}",
                     "status": "发明授权", "inventors": ["张三"],
                     "applicant": "测试单位", "dates": ["2023-01-01", "2026-01-01"]}
                    for i in range(20)]

    def test_collect_stops_at_count(self):
        from tju_info_retrieval.sources.cnki_patent import CnkiPatentAdapter
        adapter = CnkiPatentAdapter.__new__(CnkiPatentAdapter)
        page = self._PagyPage()
        first = adapter._parse_list(page)
        rows = adapter._collect_pages(page, list(first), count=50)
        assert len(rows) >= 50, "候选池应覆盖 count（按整页可能略超）"
        assert len(adapter._map_rows(rows, 50)) == 50  # 最终按 count 截断
        assert page.calls == 2  # 首页 + 2 次翻页（3 页覆盖 50）
        assert len({r["title"] for r in rows}) == len(rows)  # 去重无重复

    def test_collect_caps_at_five_pages(self):
        from tju_info_retrieval.sources.cnki_patent import CnkiPatentAdapter
        adapter = CnkiPatentAdapter.__new__(CnkiPatentAdapter)
        page = self._PagyPage()
        rows = adapter._collect_pages(page, list(adapter._parse_list(page)),
                                      count=1000)
        assert len(rows) == 100  # 硬上限 5 页
        assert page.calls == 4


class TestBoundedIntersectionPaging(unittest.TestCase):
    """Hotfix-2：requested output 与 intersection candidate budget 解耦。"""

    @staticmethod
    def _adapter(page):
        adapter = CnkiPatentAdapter(mock.Mock())
        adapter._session = mock.Mock(page=mock.Mock(return_value=page))
        return adapter

    @staticmethod
    def _nonmatches(page_no, n=20):
        return [
            _row(title=f"太赫兹候选{page_no}-{i}", applicant="其他大学",
                 inventors=("张三",), url=f"https://example/p{page_no}-{i}")
            for i in range(n)
        ]

    @staticmethod
    def _req():
        req = mock.Mock()
        req.research_direction = "太赫兹"
        req.author_name = ""
        req.author_affiliation = "天津大学"
        return req

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_requested_20_finds_first_match_on_page_2(self, resolver_cls):
        pages = [self._nonmatches(1), [
            _row(title="太赫兹第二页真实命中", applicant="天津大学",
                 url="https://example/real")]]
        page = _PagedResultsPage(pages)
        adapter = self._adapter(page)
        results = adapter.search("太赫兹", 20, query_context=self._req())
        assert [r.title for r in results] == ["太赫兹第二页真实命中"]
        assert page.page_requests == 1
        assert adapter._last_search_stats["candidate_count"] == 21
        resolver_cls.assert_not_called()  # 标题已证明 topic，无需详情

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_requested_5_still_finds_first_match_on_page_2(self, resolver_cls):
        pages = [self._nonmatches(1), [
            _row(title="太赫兹第二页真实命中", applicant="天津大学",
                 url="https://example/real")]]
        page = _PagedResultsPage(pages)
        adapter = self._adapter(page)
        results = adapter.search("太赫兹", 5, query_context=self._req())
        assert [r.title for r in results] == ["太赫兹第二页真实命中"]
        assert page.page_requests == 1
        assert adapter._last_search_stats["candidate_count"] == 21
        resolver_cls.assert_not_called()

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_enough_matches_on_first_page_stops_immediately(self, resolver_cls):
        page1 = [
            _row(title=f"太赫兹真实命中{i}", applicant="天津大学",
                 url=f"https://example/m{i}") for i in range(5)
        ] + self._nonmatches(1, 15)
        page = _PagedResultsPage([page1, self._nonmatches(2)])
        adapter = self._adapter(page)
        results = adapter.search("太赫兹", 5, query_context=self._req())
        assert len(results) == 5
        assert page.page_requests == 0
        resolver_cls.assert_not_called()

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_no_match_stops_at_five_pages(self, resolver_cls):
        pages = [self._nonmatches(i) for i in range(1, 7)]
        page = _PagedResultsPage(pages)
        adapter = self._adapter(page)
        assert adapter.search("太赫兹", 5, query_context=self._req()) == []
        assert page.page_requests == 4
        assert adapter._last_search_stats["pages_fetched"] == 5
        assert adapter._last_search_stats["candidate_count"] == 100
        resolver_cls.assert_not_called()

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_candidate_hard_cap_stops_even_when_more_pages_exist(self, resolver_cls):
        pages = [self._nonmatches(i, 30) for i in range(1, 7)]
        page = _PagedResultsPage(pages)
        adapter = self._adapter(page)
        assert adapter.search("太赫兹", 5, query_context=self._req()) == []
        assert adapter._last_search_stats["candidate_count"] == 100
        assert page.page_requests == 3  # 第 4 页中达到 100，不再请求第 5 页
        resolver_cls.assert_not_called()

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_single_topic_requested_5_does_not_page(self, resolver_cls):
        page = _PagedResultsPage([
            [_row(title=f"太赫兹专利{i}", url=f"https://example/s{i}")
             for i in range(20)],
            [_row(title="不应访问", url="https://example/no")],
        ])
        adapter = self._adapter(page)
        results = adapter.search("太赫兹", 5, query_context=_request())
        assert len(results) == 5
        assert page.page_requests == 0
        resolver_cls.return_value.enrich.assert_called_once()

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_detail_budget_is_global_across_pages(self, resolver_cls):
        # applicant 匹配但标题无 topic，需摘要才能证明；resolver 全部失败。
        pages = [[
            _row(title=f"其它领域{i}-{j}", applicant="天津大学",
                 url=f"https://example/d{i}-{j}") for j in range(20)
        ] for i in range(5)]
        resolver_cls.return_value.enrich.return_value = 0
        page = _PagedResultsPage(pages)
        adapter = self._adapter(page)
        assert adapter.search("太赫兹", 5, query_context=self._req()) == []
        submitted = sum(len(call.args[0])
                        for call in resolver_cls.return_value.enrich.call_args_list)
        assert submitted == 8
        assert adapter._last_search_stats["detail_candidates"] == 8

    @mock.patch("tju_info_retrieval.sources.cnki_patent.CnkiPatentMetadataResolver")
    def test_cross_page_duplicate_is_returned_once(self, resolver_cls):
        duplicate = _row(title="太赫兹重复命中", applicant="天津大学",
                         url="https://example/dup")
        page1 = [duplicate] + self._nonmatches(1, 19)
        page2 = [duplicate, _row(title="太赫兹第二命中", applicant="天津大学",
                                 url="https://example/second")]
        page = _PagedResultsPage([page1, page2])
        adapter = self._adapter(page)
        results = adapter.search("太赫兹", 2, query_context=self._req())
        assert [r.title for r in results] == ["太赫兹重复命中", "太赫兹第二命中"]
        assert page.page_requests == 1
        resolver_cls.assert_not_called()

class TestDetailParser(unittest.TestCase):
    def test_parse_detail_row(self):
        assert parse_detail_row("授权公告号： CN118081733B") == "CN118081733B"
        assert parse_detail_row("主分类号： B25J9/16") == "B25J9/16"

    def test_parse_detail_fields(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.set_content(DETAIL_FIXTURE)
            text = page.inner_text("body")
            browser.close()
        fields = parse_detail_fields(text)
        assert fields["application_number"] == "CN202311735382.8"
        assert fields["publication_number"] == "CN118081733B"
        assert fields["ipc"] == "B25J9/16"

    def test_parse_detail_abstract(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.set_content(DETAIL_FIXTURE)
            text = page.inner_text("body")
            browser.close()
        abstract = parse_detail_abstract(text)
        assert "机器人用户坐标系校准装置" in abstract

    def test_parse_missing_fields_degrade(self):
        assert parse_detail_fields("只有正文没有字段") == {
            "application_number": "", "publication_number": "", "ipc": ""}
        assert parse_detail_abstract("无摘要") == ""


class TestResolver(unittest.TestCase):
    def _context(self, detail_text):
        detail = mock.Mock()
        detail.inner_text.return_value = detail_text
        ctx = mock.Mock()
        ctx.new_page.return_value = detail
        return ctx

    def _detail_text(self):
        return ("申请(专利)号： CN202311735382.8\n授权公告号： CN118081733B\n"
                "主分类号： B25J9/16\n摘要： 本发明提供了一种机器人用户坐标系校准装置。\n")

    def test_enrich_applies_fields_and_abstract(self):
        ctx = self._context(self._detail_text())
        resolver = CnkiPatentMetadataResolver(
            ctx, cache={}, max_items=5,
            per_item_timeout_s=1.0, total_budget_s=5.0)
        r = _patent_result()
        n = resolver.enrich([r])
        assert n == 1
        assert r.artifact_metadata["publication_number"] == "CN118081733B"
        assert r.artifact_metadata["application_number"] == "CN202311735382.8"
        assert r.artifact_metadata["ipc"] == "B25J9/16"
        assert "机器人用户坐标系校准装置" in r.abstract

    def test_cache_hit_no_open(self):
        ctx = mock.Mock()  # new_page 不应被调用
        resolver = CnkiPatentMetadataResolver(
            ctx, cache={"https://kns.cnki.net/kcms2/article/abstract?v=AAA": {
                "publication_number": "CN1", "application_number": "CN2",
                "ipc": "G01N", "abstract": "缓存摘要"}},
            max_items=5)
        r = _patent_result()
        n = resolver.enrich([r])
        assert n == 1
        assert r.artifact_metadata["publication_number"] == "CN1"
        ctx.new_page.assert_not_called()

    def test_hard_cap(self):
        ctx = self._context(self._detail_text())
        resolver = CnkiPatentMetadataResolver(
            ctx, cache={}, max_items=2,
            per_item_timeout_s=1.0, total_budget_s=10.0)
        results = [_patent_result(title=f"专利{i}",
                                  url=f"https://kns.cnki.net/kcms2/article/abstract?v={i}")
                   for i in range(5)]
        n = resolver.enrich(results)
        assert n == 2
        assert ctx.new_page.call_count == 2

    def test_captcha_blocks_degrades(self):
        detail = mock.Mock()
        detail.inner_text.return_value = ("申请(专利)号： CN1\n"
                                          "安全验证 拖动滑块完成验证")
        ctx = mock.Mock()
        ctx.new_page.return_value = detail
        resolver = CnkiPatentMetadataResolver(ctx, cache={}, max_items=5)
        r = _patent_result()
        n = resolver.enrich([r])
        assert n == 0
        assert r.artifact_metadata["publication_number"] is None
        assert r.abstract is None

    def test_dormant_captcha_with_readable_content_succeeds(self):
        """休眠验证码元素（探针 18 json：内容可读）→ 字段可得即成功，不误拦。"""
        detail = mock.Mock()
        detail.inner_text.return_value = (
            "申请(专利)号： CN202311735382.8\n"
            "授权公告号： CN118081733B\n"
            "主分类号： B25J9/16\n"
            "摘要： 本发明提供了一种检测装置。\n"
            "安全验证 拖动滑块完成验证")
        ctx = mock.Mock()
        ctx.new_page.return_value = detail
        resolver = CnkiPatentMetadataResolver(ctx, cache={}, max_items=5)
        r = _patent_result()
        n = resolver.enrich([r])
        assert n == 1
        assert r.artifact_metadata["publication_number"] == "CN118081733B"
        assert "检测装置" in r.abstract

    def test_failure_degrades(self):
        ctx = mock.Mock()
        ctx.new_page.side_effect = RuntimeError("net err")
        resolver = CnkiPatentMetadataResolver(ctx, cache={}, max_items=5)
        r = _patent_result()
        n = resolver.enrich([r])
        assert n == 0
        assert r.artifact_metadata["publication_number"] is None


# ============================================================
# 4. Filter 集成
# ============================================================

class TestFilterIntegration(unittest.TestCase):
    def test_inventor_match_kept(self):
        r = _patent_result(inventors=("钟舜聪",))
        assert len(FilterService().filter([r], _request(author_name="钟舜聪"))) == 1

    def test_applicant_match_kept(self):
        r = _patent_result(applicant="福州大学")
        assert len(FilterService().filter(
            [r], _request(author_affiliation="福州大学"))) == 1

    def test_date_range(self):
        r = _patent_result(date="2026-09-01")
        assert len(FilterService().filter(
            [r], _request(start_date="2020.01", end_date="2027.12"))) == 1


# ============================================================
# 5. Offline Summary 集成（CNKI patent abstract 可被现有引擎整理）
# ============================================================

class TestOfflineSummaryIntegration(unittest.TestCase):
    def test_cnki_patent_abstract_summarized(self):
        r = _patent_result(title="一种太赫兹检测方法")
        r.abstract = ("本发明提供了一种太赫兹检测方法，该方法采用太赫兹时域光谱"
                      "技术，实现了对样品的快速定量检测，可应用于工业检测场景。")
        s = OfflineSummaryEngine(cache={}).summarize(r)
        assert s.core_technology != "当前材料未提供足够信息，无法可靠提取。"
        assert s.main_results != "当前材料未提供足够信息，无法可靠提取。"
        assert "太赫兹" in s.research_content + s.core_technology


# ============================================================
# 6. SearchService 路由
# ============================================================

class TestSearchServiceRouting(unittest.TestCase):
    def test_registry_has_cnki_patent(self):
        registry = SearchService._default_registry()
        assert "CNKI专利" in registry.available_sources()
        from tju_info_retrieval.sources.cnki_patent import CnkiPatentAdapter
        assert registry.get("CNKI专利") is CnkiPatentAdapter

    def test_search_service_routes_cnki_patent(self):
        from tju_info_retrieval.sources.cnki_patent import CnkiPatentAdapter
        session = mock.Mock()
        session.check_tju_auth.return_value = "logged_in"
        req = QueryRequest(research_direction="太赫兹",
                           sources=["CNKI专利"], result_count=5)
        with mock.patch.object(CnkiPatentAdapter, "search", return_value=[]) as called:
            SearchService(session).search(req)
        called.assert_called_once()
        assert called.call_args.args[1] == 5
        assert called.call_args.kwargs["query_context"] is req


if __name__ == "__main__":
    unittest.main(verbosity=2)
