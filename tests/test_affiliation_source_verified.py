#!/usr/bin/env python3
"""v0.13 Phase 8：单位筛选可信链（source_verified）测试。

覆盖：
1. CNKI 高级检索（天津大学）strict 模式 + source_verified → 保留
2. 普通 CNKI 搜索 strict 模式 无 metadata → 删除
3. IEEE metadata 匹配 strict → 保留
4. IEEE 无 metadata strict → 删除
5. soft 模式行为保持不变
6. QueryRequest.affiliation_source_verified 字段
7. MetadataRecord.source_verified 序列化
8. CNKIAdapter 高级成功后标记 source_verified_ids + 查询级字段
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
from tju_info_retrieval.sources.cnki import CNKIAdapter


def _result(**kw):
    data = dict(rank=1, title="T", authors=["A"], year="2022",
                database="CNKI", detail_url="https://kns.cnki.net/detail/1")
    data.update(kw)
    return SearchResult(**data)


def _request(**kw):
    data = dict(research_direction="太赫兹", author_affiliation="天津大学")
    data.update(kw)
    return QueryRequest(**data)


def _record(result_id, *affiliations, source_verified=False):
    return MetadataRecord(
        result_id=result_id, affiliations=list(affiliations),
        source_verified=source_verified,
    )


def _meta(*records):
    return {r.result_id: r for r in records}


# ============================================================
# 1/2/3/4. strict 模式信任链
# ============================================================

class TestStrictTrustChain(unittest.TestCase):
    def setUp(self):
        self.svc = FilterService()

    def test_cnki_advanced_source_verified_kept(self):
        """CNKI 高级检索结果（source_verified=True）在 strict 下保留。"""
        q = _request(affiliation_filter_mode="strict")
        meta = _meta(_record("u1", source_verified=True))
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual([x.title for x in out], ["T"])

    def test_cnki_plain_no_metadata_deleted(self):
        """普通 CNKI 搜索结果（无 metadata、无 source_verified）strict 下删除。"""
        q = _request(affiliation_filter_mode="strict")
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map={})
        self.assertEqual(out, [])

    def test_ieee_metadata_match_kept(self):
        """IEEE 结果 metadata 单位匹配 → strict 保留。"""
        q = _request(author_affiliation="Tianjin University",
                     affiliation_filter_mode="strict")
        meta = _meta(_record("u1", "Tianjin University"))
        r = _result(database="IEEE Xplore", detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(len(out), 1)

    def test_ieee_no_metadata_deleted(self):
        """IEEE 结果无 metadata → strict 删除。"""
        q = _request(affiliation_filter_mode="strict")
        r = _result(database="IEEE Xplore", detail_url="u1")
        out = self.svc.filter([r], q, metadata_map={})
        self.assertEqual(out, [])

    def test_source_verified_without_affiliations_kept(self):
        """source_verified=True 且无 affiliations 时 strict 仍保留（修复 0 结果问题）。"""
        q = _request(affiliation_filter_mode="strict")
        meta = _meta(_record("u1", source_verified=True))
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(len(out), 1)


# ============================================================
# 5. soft 模式保持不变
# ============================================================

class TestSoftUnchanged(unittest.TestCase):
    def setUp(self):
        self.svc = FilterService()

    def test_soft_unknown_metadata_kept(self):
        q = _request(affiliation_filter_mode="soft")
        r = _result(detail_url="no-record")
        out = self.svc.filter([r], q, metadata_map={})
        self.assertEqual(len(out), 1)

    def test_soft_mismatch_removed(self):
        q = _request(affiliation_filter_mode="soft")
        meta = _meta(_record("u1", "Peking University"))
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(out, [])

    def test_soft_match_kept(self):
        q = _request(author_affiliation="Tianjin University",
                     affiliation_filter_mode="soft")
        meta = _meta(_record("u1", "Tianjin University"))
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(len(out), 1)

    def test_soft_source_verified_kept(self):
        q = _request(affiliation_filter_mode="soft")
        meta = _meta(_record("u1", source_verified=True))
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(len(out), 1)


# ============================================================
# 6/7. 模型字段
# ============================================================

class TestModelFields(unittest.TestCase):
    def test_query_field_default_false(self):
        q = QueryRequest(research_direction="太赫兹")
        self.assertFalse(q.affiliation_source_verified)

    def test_query_field_settable(self):
        q = QueryRequest(research_direction="太赫兹", affiliation_source_verified=True)
        self.assertTrue(q.affiliation_source_verified)
        q.validate()  # 不抛异常

    def test_metadata_record_source_verified_round_trip(self):
        rec = _record("u1", source_verified=True)
        restored = MetadataRecord.from_dict(rec.to_dict())
        self.assertTrue(restored.source_verified)
        self.assertEqual(restored, rec)

    def test_metadata_record_default_false(self):
        rec = MetadataRecord.from_dict({"result_id": "u1"})
        self.assertFalse(rec.source_verified)


# ============================================================
# 8. CNKIAdapter 标记
# ============================================================

class TestCnkiAdapterMarking(unittest.TestCase):
    def _run_advanced(self, query, results):
        session = mock.Mock()
        page = mock.Mock()
        result_page = mock.Mock()
        result_page.url = "https://kns.cnki.net/kns8s/defaultresult/index?x=1"
        session.page.return_value = page
        context = mock.Mock()
        context.pages = [result_page]
        session.context = context
        adapter = CNKIAdapter(session)
        with mock.patch(
            "tju_info_retrieval.sources.cnki.wait_for_advanced_result_page",
            return_value=result_page,
        ), mock.patch(
            "tju_info_retrieval.sources.cnki.CNKIAdvancedQueryBuilder",
        ) as builder_cls, mock.patch.object(
            CNKIAdapter, "parse_results", return_value=results,
        ):
            adapter.search("太赫兹", 10, query_context=query)
        return adapter, query

    def test_advanced_with_affiliation_marks_verified(self):
        results = [_result(title="A", detail_url="u1")]
        query = _request()  # author_affiliation=天津大学
        adapter, query = self._run_advanced(query, results)
        self.assertEqual(adapter.source_verified_ids, {"u1"})
        self.assertTrue(query.affiliation_source_verified)

    def test_advanced_without_affiliation_not_marked(self):
        """高级检索但无单位条件（仅作者）→ 不标记来源验证。"""
        results = [_result(title="A", detail_url="u1")]
        query = _request(author_affiliation="", author_name="张三")
        adapter, query = self._run_advanced(query, results)
        self.assertEqual(adapter.source_verified_ids, set())
        self.assertFalse(query.affiliation_source_verified)


if __name__ == "__main__":
    unittest.main(verbosity=2)
