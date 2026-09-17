#!/usr/bin/env python3
"""v0.13 Phase 3：FilterService 高级筛选系统测试（全部 mock）。

覆盖：
1. 作者匹配成功
2. 作者大小写忽略
3. 作者部分匹配
4. 单位匹配
5. 单位大小写忽略
6. 缺少 Metadata 时不崩溃（保留 + 日志）
7. 年份开始过滤
8. 年份结束过滤
9. 开始结束组合过滤
10. 无筛选条件保持原结果
11. FilterService 接入 SearchService
"""
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.models.metadata import MetadataRecord
from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.filtering import FilterService
from tju_info_retrieval.services.metadata_store import MetadataStore


def _result(**kw):
    data = dict(
        rank=1, title="T", authors=["Zhang San"], year="2022",
        database="CNKI", detail_url="https://kns.cnki.net/detail/1",
    )
    data.update(kw)
    return SearchResult(**data)


def _request(**kw):
    data = dict(research_direction="太赫兹")
    data.update(kw)
    return QueryRequest(**data)


def _record(result_id, *affiliations):
    return MetadataRecord(result_id=result_id, affiliations=list(affiliations))


def _service(session=None, registry=None, metadata_store=None):
    from tju_info_retrieval.services.search_service import SearchService
    session = session or mock.Mock()
    session.check_tju_auth.return_value = "logged_in"
    return SearchService(session, registry=registry, metadata_store=metadata_store)


# ============================================================
# 1/2/3. 作者筛选
# ============================================================

class TestAuthorFilter(unittest.TestCase):
    def setUp(self):
        self.svc = FilterService()

    def test_author_match_success(self):
        q = _request(author_name="Zhang San")
        kept = _result(title="A", authors=["Zhang San"])
        dropped = _result(title="B", authors=["Li Si"])
        out = self.svc.filter([kept, dropped], q)
        self.assertEqual([r.title for r in out], ["A"])

    def test_author_case_insensitive(self):
        q = _request(author_name="zhang san")
        r = _result(authors=["ZHANG SAN"])
        out = self.svc.filter([r], q)
        self.assertEqual(len(out), 1)

    def test_author_partial_match(self):
        q = _request(author_name="zhang")
        r = _result(authors=["Zhang San"])
        out = self.svc.filter([r], q)
        self.assertEqual(len(out), 1)

    def test_author_no_match_removes(self):
        q = _request(author_name="王五")
        r = _result(authors=["Zhang San"])
        out = self.svc.filter([r], q)
        self.assertEqual(out, [])

    def test_author_empty_authors_safe(self):
        q = _request(author_name="张三")
        r = _result(authors=[])
        out = self.svc.filter([r], q)
        self.assertEqual(out, [])

    def test_empty_author_condition_skips(self):
        q = _request(author_name="  ")
        r = _result(title="Any")
        out = self.svc.filter([r], q)
        self.assertEqual([x.title for x in out], ["Any"])


# ============================================================
# 4/5. 单位筛选
# ============================================================

class TestAffiliationFilter(unittest.TestCase):
    def setUp(self):
        self.svc = FilterService()

    def test_affiliation_match(self):
        q = _request(author_affiliation="Tianjin University")
        meta = {
            "https://kns.cnki.net/detail/1": _record(
                "https://kns.cnki.net/detail/1", "Tianjin University"
            ),
        }
        r = _result()
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(len(out), 1)

    def test_affiliation_case_insensitive(self):
        q = _request(author_affiliation="tianjin university")
        meta = {
            "u1": _record("u1", "TIANJIN UNIVERSITY"),
        }
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(len(out), 1)

    def test_affiliation_punctuation_normalized(self):
        """去标点 + 空格归一：Tianjin University. 匹配 tianjin university。"""
        q = _request(author_affiliation="Tianjin University.")
        meta = {"u1": _record("u1", "Tianjin University")}
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(len(out), 1)

    def test_affiliation_partial_match(self):
        q = _request(author_affiliation="Tianjin")
        meta = {"u1": _record("u1", "School of Tianjin University, China")}
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(len(out), 1)

    def test_affiliation_no_match_removes(self):
        q = _request(author_affiliation="Peking University")
        meta = {"u1": _record("u1", "Tianjin University")}
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(out, [])

    def test_affiliation_empty_condition_skips(self):
        q = _request(author_affiliation="")
        r = _result()
        out = self.svc.filter([r], q)
        self.assertEqual(len(out), 1)


# ============================================================
# 6. 缺少 Metadata 不崩溃（保留 + 日志）
# ============================================================

class TestMissingMetadata(unittest.TestCase):
    def setUp(self):
        self.svc = FilterService()

    def test_no_metadata_keeps_result(self):
        q = _request(author_affiliation="Tianjin University")
        r = _result(detail_url="no-record")
        with self.assertLogs("tju_info_retrieval.services.filtering", level="DEBUG") as cm:
            out = self.svc.filter([r], q, metadata_map={})
        self.assertEqual(len(out), 1)  # 保留
        self.assertTrue(any("MetadataRecord" in line for line in cm.output))

    def test_none_metadata_map_keeps_result(self):
        q = _request(author_affiliation="Tianjin University")
        r = _result(detail_url="x")
        out = self.svc.filter([r], q, metadata_map=None)
        self.assertEqual(len(out), 1)

    def test_result_without_url_no_crash(self):
        q = _request(author_affiliation="Tianjin University")
        r = _result(detail_url=None)
        out = self.svc.filter([r], q, metadata_map={})
        self.assertEqual(len(out), 1)


# ============================================================
# 7/8/9. 年份筛选
# ============================================================

class TestYearFilter(unittest.TestCase):
    def setUp(self):
        self.svc = FilterService()

    def test_start_year_only(self):
        q = _request(start_date="2020.06")
        old = _result(title="2019", year="2019")
        boundary = _result(title="2020", year="2020")
        new = _result(title="2023", year="2023")
        out = self.svc.filter([old, boundary, new], q)
        self.assertEqual([r.title for r in out], ["2020", "2023"])

    def test_end_year_only(self):
        q = _request(end_date="2023.08")
        old = _result(title="2022", year="2022")
        boundary = _result(title="2023", year="2023")
        new = _result(title="2024", year="2024")
        out = self.svc.filter([old, boundary, new], q)
        self.assertEqual([r.title for r in out], ["2022", "2023"])

    def test_start_end_combined(self):
        q = _request(start_date="2020.06", end_date="2023.08")
        too_old = _result(title="2019", year="2019")
        inside = _result(title="2021", year="2021")
        too_new = _result(title="2025", year="2025")
        out = self.svc.filter([too_old, inside, too_new], q)
        self.assertEqual([r.title for r in out], ["2021"])

    def test_year_missing_with_restriction_dropped(self):
        q = _request(start_date="2020.06")
        no_year = _result(title="NoYear", year=None)
        out = self.svc.filter([no_year], q)
        self.assertEqual(out, [])

    def test_no_date_condition_keeps_all(self):
        q = _request()
        r1 = _result(title="NoYear", year=None)
        r2 = _result(title="2024", year="2024")
        out = self.svc.filter([r1, r2], q)
        self.assertEqual(len(out), 2)

    def test_full_date_string_year(self):
        """SearchResult.year 可能带完整日期，仍按年份提取。"""
        q = _request(start_date="2020.06", end_date="2023.08")
        r = _result(title="Mid", year="2021-05-30")
        out = self.svc.filter([r], q)
        self.assertEqual([x.title for x in out], ["Mid"])


# ============================================================
# 10. 无筛选条件保持原结果
# ============================================================

class TestNoConditionPassthrough(unittest.TestCase):
    def setUp(self):
        self.svc = FilterService()

    def test_no_condition_returns_original(self):
        q = _request()
        items = [_result(title="A"), _result(title="B"), _result(title="C")]
        out = self.svc.filter(items, q)
        self.assertIs(out[0], items[0])
        self.assertIs(out[1], items[1])
        self.assertIs(out[2], items[2])
        self.assertEqual([r.title for r in out], ["A", "B", "C"])

    def test_empty_input(self):
        q = _request(author_name="张三")
        self.assertEqual(self.svc.filter([], q), [])


# ============================================================
# MetadataStore
# ============================================================

class TestMetadataStore(unittest.TestCase):
    def test_put_get(self):
        store = MetadataStore()
        rec = _record("u1", "Tianjin University")
        store.put(rec)
        self.assertEqual(store.get("u1"), rec)

    def test_get_missing(self):
        store = MetadataStore()
        self.assertIsNone(store.get("nope"))

    def test_put_ignores_empty_result_id(self):
        store = MetadataStore()
        store.put(_record("", "Tianjin University"))
        self.assertEqual(len(store), 0)

    def test_as_map_snapshot(self):
        store = MetadataStore()
        store.put(_record("u1", "Tianjin University"))
        snapshot = store.as_map()
        self.assertEqual(len(snapshot), 1)
        snapshot["u2"] = _record("u2", "Peking University")  # 不污染内部
        self.assertIsNone(store.get("u2"))


# ============================================================
# 11. FilterService 接入 SearchService
# ============================================================

class TestSearchServiceIntegration(unittest.TestCase):
    @staticmethod
    def _make_service(adapter_results, metadata_store=None):
        session = mock.Mock()
        session.check_tju_auth.return_value = "logged_in"
        adapter = mock.Mock()
        adapter.search.return_value = adapter_results
        registry = mock.Mock()
        cls = mock.Mock()
        cls.return_value = adapter
        registry.get.return_value = cls
        return _service(session, registry=registry, metadata_store=metadata_store), adapter

    def test_author_filter_applied_in_pipeline(self):
        from tju_info_retrieval.services.search_service import SearchService
        # 用真实 SearchService，仅 mock 浏览器与 adapter
        results = [
            _result(title="匹配", authors=["Zhang San"]),
            _result(title="不匹配", authors=["Li Si"]),
        ]
        service, _adapter = self._make_service(results)
        q = _request(author_name="zhang")
        out = service.search(q)
        self.assertEqual([r.title for r in out], ["匹配"])

    def test_affiliation_filter_applied_in_pipeline(self):
        store = MetadataStore()
        store.put(_record("u1", "Tianjin University"))
        store.put(_record("u2", "Peking University"))
        results = [
            _result(title="天大", detail_url="u1"),
            _result(title="北大", detail_url="u2"),
        ]
        service, _adapter = self._make_service(results, metadata_store=store)
        q = _request(author_affiliation="Tianjin University")
        out = service.search(q)
        self.assertEqual([r.title for r in out], ["天大"])

    def test_missing_metadata_kept_in_pipeline(self):
        """单位筛选时缺少 MetadataRecord 的结果保留（延迟增强数据不误删）。"""
        store = MetadataStore()
        store.put(_record("u1", "Tianjin University"))
        results = [
            _result(title="天大", detail_url="u1"),
            _result(title="无元数据", detail_url="u2"),
        ]
        service, _adapter = self._make_service(results, metadata_store=store)
        q = _request(author_affiliation="Tianjin University")
        out = service.search(q)
        self.assertEqual([r.title for r in out], ["天大", "无元数据"])

    def test_no_condition_keeps_all_in_pipeline(self):
        results = [
            _result(title="A", detail_url="https://kns.cnki.net/detail/a"),
            _result(title="B", detail_url="https://kns.cnki.net/detail/b"),
        ]
        service, _adapter = self._make_service(results)
        q = _request()
        out = service.search(q)
        self.assertEqual([r.title for r in out], ["A", "B"])


# ============================================================
# v0.13 万方来源保护：strict 模式不误删无单位元数据的万方结果
# ============================================================

class TestWanfangStrictSourceProtection(unittest.TestCase):
    def setUp(self):
        self.svc = FilterService()

    def _wanfang(self, title="太赫兹论文A", **kw):
        kw.setdefault("database", "万方")
        kw.setdefault("detail_url", None)
        return _result(title=title, **kw)

    def test_strict_wanfang_without_metadata_kept(self):
        """万方无单位元数据 + strict：来源保护保留，不全删（CNKI 对照仍删）。"""
        wf = self._wanfang()
        cnki = _result(rank=2, title="太赫兹论文B")
        q = _request(author_affiliation="天津大学",
                     affiliation_filter_mode="strict")
        with self.assertLogs("tju_info_retrieval.services.filtering",
                             level="WARNING") as cm:
            out = self.svc.filter([wf, cnki], q, metadata_map={})
        titles = [r.title for r in out]
        self.assertIn("太赫兹论文A", titles, "万方来源保护：strict 不得删除")
        self.assertNotIn("太赫兹论文B", titles, "CNKI 无元数据仍按 strict 删除")
        self.assertTrue(any("来源保护" in line for line in cm.output), "应记录 warning")

    def test_strict_wanfang_empty_affiliations_kept(self):
        """万方 record 存在但 affiliations 为空：同样来源保护。"""
        wf = self._wanfang(detail_url="wf1")
        q = _request(author_affiliation="天津大学",
                     affiliation_filter_mode="strict")
        meta = {"wf1": _record("wf1")}  # 无 affiliations
        out = self.svc.filter([wf], q, metadata_map=meta)
        self.assertEqual(len(out), 1)

    def test_strict_wanfang_mismatched_affiliation_still_deleted(self):
        """万方有单位数据且不匹配：正常删除（保护仅限缺失场景）。"""
        wf = self._wanfang(detail_url="wf1")
        q = _request(author_affiliation="北京大学",
                     affiliation_filter_mode="strict")
        meta = {"wf1": _record("wf1", "清华大学")}
        out = self.svc.filter([wf], q, metadata_map=meta)
        self.assertEqual(len(out), 0)

    def test_soft_wanfang_without_metadata_kept(self):
        """soft 模式回归：万方无元数据保留（行为不变）。"""
        wf = self._wanfang()
        q = _request(author_affiliation="天津大学",
                     affiliation_filter_mode="soft")
        out = self.svc.filter([wf], q, metadata_map={})
        self.assertEqual(len(out), 1)


# ============================================================
# v0.13 万方年份保护：时间过滤不误删缺年份的万方结果
# ============================================================

class TestWanfangYearFilterProtection(unittest.TestCase):
    def setUp(self):
        self.svc = FilterService()

    def _wf(self, year, title="太赫兹论文A"):
        return _result(title=title, database="万方",
                       detail_url=None, year=year)

    def _q(self, start="2005.01", end="2024.11"):
        return _request(start_date=start, end_date=end)

    def test_wanfang_empty_year_kept_with_warning(self):
        """万方 year=""：时间范围下保留（缺元数据来源保护 + warning）。"""
        with self.assertLogs("tju_info_retrieval.services.filtering",
                             level="WARNING") as cm:
            out = self.svc.filter([self._wf("")], self._q())
        self.assertEqual(len(out), 1, "万方缺年份应保留")
        self.assertTrue(any("缺少年份元数据" in line for line in cm.output))

    def test_wanfang_none_year_kept(self):
        out = self.svc.filter([self._wf(None)], self._q())
        self.assertEqual(len(out), 1)

    def test_wanfang_year_in_range_kept(self):
        out = self.svc.filter([self._wf("2020")], self._q("2020.01", "2024.11"))
        self.assertEqual(len(out), 1)

    def test_wanfang_year_out_of_range_deleted(self):
        """万方年份可解析且在区间外：仍删除（不是关闭时间过滤）。"""
        out = self.svc.filter([self._wf("2010")], self._q("2020.01", "2024.11"))
        self.assertEqual(len(out), 0)

    def test_cnki_empty_year_still_deleted(self):
        """CNKI year=""：保持原行为（缺失即删除，不因本次修改改变）。"""
        r = _result(title="CNKI论文", year="")
        out = self.svc.filter([r], self._q())
        self.assertEqual(len(out), 0)


def _patent(**kw):
    data = dict(rank=1, title="专利", authors=[], year=None,
                database="万方专利", detail_url=None,
                artifact_type="patent", artifact_metadata={})
    data.update(kw)
    return SearchResult(**data)


def _news(**kw):
    data = dict(rank=1, title="新闻", authors=[], year=None,
                database="演示新闻", detail_url=None,
                artifact_type="news", artifact_metadata={})
    data.update(kw)
    return SearchResult(**data)


# ============================================================
# v0.17 Phase 2.2-A：多类型过滤（paper 行为逐项不变）
# ============================================================

class TestAuthorArtifactAware(unittest.TestCase):
    """作者条件 × patent/news。

    规则：patent 候选 = authors + inventors；news 候选 = authors +
    artifact_metadata.authors + related_person。有候选按部分匹配决定
    保留/删除；全部缺失 → 保留（unknown != mismatch）。
    """

    def setUp(self):
        self.svc = FilterService()

    def test_patent_inventor_list_hit_kept(self):
        r = _patent(artifact_metadata={"inventors": ["陈远", "刘明"]})
        out = self.svc.filter([r], _request(author_name="陈远"))
        self.assertEqual(len(out), 1)

    def test_patent_inventor_string_hit_kept(self):
        r = _patent(artifact_metadata={"inventors": "陈远,刘明"})
        out = self.svc.filter([r], _request(author_name="陈远"))
        self.assertEqual(len(out), 1)

    def test_patent_inventor_miss_deleted(self):
        r = _patent(artifact_metadata={"inventors": ["陈远"]})
        out = self.svc.filter([r], _request(author_name="张三"))
        self.assertEqual(out, [])

    def test_patent_all_person_fields_missing_kept_even_strict(self):
        """人物元数据全缺失：即使 affiliation mode=strict、无单位条件，
        也不能因 author 缺失被删除。"""
        r = _patent(artifact_metadata={})
        q = _request(author_name="张三", affiliation_filter_mode="strict")
        out = self.svc.filter([r], q)
        self.assertEqual(len(out), 1)

    def test_news_related_person_hit_kept(self):
        r = _news(artifact_metadata={"related_person": ["王圣麟"]})
        out = self.svc.filter([r], _request(author_name="王圣麟"))
        self.assertEqual(len(out), 1)

    def test_news_artifact_authors_hit_kept(self):
        """署名正式字段为 artifact_metadata.authors（非 byline）。"""
        r = _news(artifact_metadata={"authors": ["周晓"]})
        out = self.svc.filter([r], _request(author_name="周晓"))
        self.assertEqual(len(out), 1)

    def test_news_byline_key_ignored(self):
        """不存在的 byline 字段不得被依赖：仅有 byline 键 → 人物缺失 → 保留。"""
        r = _news(artifact_metadata={"byline": ["周晓"]})
        out = self.svc.filter([r], _request(author_name="周晓"))
        self.assertEqual(len(out), 1, "byline 不作为匹配来源；未知 → 保留")

    def test_news_all_person_fields_missing_kept_even_strict(self):
        r = _news(artifact_metadata={})
        q = _request(author_name="张三", affiliation_filter_mode="strict")
        out = self.svc.filter([r], q)
        self.assertEqual(len(out), 1)

    def test_person_value_forms_string_list_none_safe(self):
        """artifact_metadata 人物值 string / list / None 均安全处理。"""
        r_str = _patent(artifact_metadata={"inventors": "陈远,刘明"})
        r_list = _patent(artifact_metadata={"inventors": ["陈远"]})
        r_none = _patent(artifact_metadata={"inventors": None})
        q = _request(author_name="陈远")
        out = self.svc.filter([r_str, r_list, r_none], q)
        self.assertEqual(len(out), 3, "string/list 命中保留；None 视为缺失保留")

    def test_paper_author_behavior_unchanged(self):
        """回归：paper 无作者匹配仍删除（soft 下也是），不受类型分支影响。"""
        r = _result(title="论文", authors=["张三"])
        out = self.svc.filter([r], _request(author_name="李四"))
        self.assertEqual(out, [])
        no_author = _result(title="论文", authors=[])
        out2 = self.svc.filter([no_author], _request(author_name="张三"))
        self.assertEqual(out2, [])


class TestAffiliationArtifactAware(unittest.TestCase):
    """单位条件 × patent/news。

    规则：patent 优先匹配 applicant；缺失落入既有 soft/strict。
    news 无单位概念：soft 保留 / strict 删除。
    """

    def setUp(self):
        self.svc = FilterService()

    def test_patent_applicant_hit_kept(self):
        r = _patent(artifact_metadata={"applicant": "天津大学"})
        out = self.svc.filter([r], _request(author_affiliation="天津大学"))
        self.assertEqual(len(out), 1)

    def test_patent_applicant_miss_deleted(self):
        r = _patent(artifact_metadata={"applicant": "南开大学"})
        out = self.svc.filter([r], _request(author_affiliation="天津大学"))
        self.assertEqual(out, [])

    def test_patent_no_applicant_soft_kept(self):
        r = _patent(artifact_metadata={})
        out = self.svc.filter(
            [r], _request(author_affiliation="天津大学", affiliation_filter_mode="soft"))
        self.assertEqual(len(out), 1)

    def test_patent_no_applicant_strict_deleted(self):
        r = _patent(artifact_metadata={})
        out = self.svc.filter(
            [r], _request(author_affiliation="天津大学", affiliation_filter_mode="strict"))
        self.assertEqual(out, [])

    def test_news_affiliation_soft_kept_strict_deleted(self):
        soft = self.svc.filter(
            [_news()],
            _request(author_affiliation="天津大学", affiliation_filter_mode="soft"))
        self.assertEqual(len(soft), 1)
        strict = self.svc.filter(
            [_news()],
            _request(author_affiliation="天津大学", affiliation_filter_mode="strict"))
        self.assertEqual(strict, [])

    def test_paper_affiliation_behavior_unchanged(self):
        """回归：paper strict 无 MetadataRecord 仍删除。"""
        r = _result(title="论文", detail_url="https://kns.cnki.net/x")
        out = self.svc.filter(
            [r], _request(author_affiliation="天津大学", affiliation_filter_mode="strict"))
        self.assertEqual(out, [])


class TestYearArtifactAware(unittest.TestCase):
    """时间条件 × patent/news。

    规则：年份解析顺序 year → typed 日期（patent.date / news.publish_time）；
    完全无法取得年份 → 保留（strict 不参与时间判断）。
    """

    def setUp(self):
        self.svc = FilterService()

    def test_patent_year_field_used(self):
        r = _patent(year="2023", artifact_metadata={})
        out = self.svc.filter([r], _request(start_date="2020.01", end_date="2024.11"))
        self.assertEqual(len(out), 1)
        out_of_range = self.svc.filter(
            [_patent(year="2010")], _request(start_date="2020.01", end_date="2024.11"))
        self.assertEqual(out_of_range, [])

    def test_patent_artifact_date_used(self):
        r = _patent(year=None, artifact_metadata={"date": "2021-05-12"})
        out = self.svc.filter([r], _request(start_date="2020.01", end_date="2023.08"))
        self.assertEqual(len(out), 1)
        outside = self.svc.filter(
            [_patent(year=None, artifact_metadata={"date": "2018-01-01"})],
            _request(start_date="2020.01", end_date="2023.08"))
        self.assertEqual(outside, [])

    def test_news_publish_time_used(self):
        r = _news(year=None, artifact_metadata={"publish_time": "2022-11-30"})
        out = self.svc.filter([r], _request(start_date="2020.01", end_date="2023.08"))
        self.assertEqual(len(out), 1)

    def test_no_year_kept_even_strict(self):
        """patent/news 完全无年份：strict 不得影响 year 判断（保留）。"""
        q = _request(start_date="2020.01", end_date="2024.11",
                     affiliation_filter_mode="strict")
        rp = _patent(year=None, artifact_metadata={})
        rn = _news(year=None, artifact_metadata={})
        out = self.svc.filter([rp, rn], q)
        self.assertEqual(len(out), 2)

    def test_paper_year_behavior_unchanged(self):
        """回归：paper（非万方）缺年份在时间限制下仍删除。"""
        r = _result(title="论文", year=None)
        out = self.svc.filter([r], _request(start_date="2020.01"))
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
