#!/usr/bin/env python3
"""v0.13 Phase 4：MetadataPipeline 闭环接入测试（全部 mock）。

覆盖：
1. 无单位条件不触发 fetcher
2. IEEE 结果调用 fetcher
3. 非 IEEE 结果跳过
4. 已有 MetadataRecord 不重复请求
5. fetch 失败返回空 metadata（不写入、不阻断）
6. 写入 MetadataStore
7. FilterService 收到正确 metadata_map（真实闭环）
8. 普通搜索流程不受影响（SearchService 集成）
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
from tju_info_retrieval.services.metadata_pipeline import MetadataPipeline
from tju_info_retrieval.services.metadata_store import MetadataStore

IEEE_URL = "https://ieeexplore.ieee.org/document/11275411/"
CNKI_URL = "https://kns.cnki.net/detail/1"


def _result(database="IEEE Xplore", **kw):
    data = dict(
        rank=1, title="T", authors=["Samuel Sani"], year="2022",
        database=database, detail_url=IEEE_URL,
    )
    data.update(kw)
    return SearchResult(**data)


def _request(**kw):
    data = dict(research_direction="太赫兹")
    data.update(kw)
    return QueryRequest(**data)


def _record(result_id, *affiliations):
    return MetadataRecord(result_id=result_id, affiliations=list(affiliations))


def _pipeline(fetcher=None, store=None):
    fetcher = fetcher or mock.Mock()
    fetcher.fetch_affiliations.return_value = ["Sabanci University, Istanbul, Turkey"]
    store = store or MetadataStore()
    return MetadataPipeline(fetchers={"IEEE Xplore": fetcher}, metadata_store=store), fetcher, store


# ============================================================
# 1. 无单位条件不触发 fetcher
# ============================================================

class TestNoConditionTriggersNothing(unittest.TestCase):
    def test_empty_affiliation_returns_without_fetch(self):
        pipeline, fetcher, store = _pipeline()
        q = _request()  # 无 author_affiliation
        out = pipeline.enrich([_result()], q)
        fetcher.fetch_affiliations.assert_not_called()
        self.assertEqual(out, {})

    def test_whitespace_affiliation_also_skips(self):
        pipeline, fetcher, _store = _pipeline()
        q = _request(author_affiliation="   ")
        pipeline.enrich([_result()], q)
        fetcher.fetch_affiliations.assert_not_called()


# ============================================================
# 2/3. IEEE 触发 / 非 IEEE 跳过
# ============================================================

class TestTriggerRules(unittest.TestCase):
    def test_ieee_result_calls_fetcher(self):
        pipeline, fetcher, store = _pipeline()
        q = _request(author_affiliation="Sabanci University")
        result = _result(database="IEEE Xplore", detail_url=IEEE_URL)
        out = pipeline.enrich([result], q)
        fetcher.fetch_affiliations.assert_called_once_with(IEEE_URL)
        self.assertIn(IEEE_URL, out)

    def test_non_ieee_result_skipped(self):
        pipeline, fetcher, _store = _pipeline()
        q = _request(author_affiliation="Tianjin University")
        cnki = _result(database="CNKI", detail_url=CNKI_URL)
        pipeline.enrich([cnki], q)
        fetcher.fetch_affiliations.assert_not_called()

    def test_ieee_without_url_skipped(self):
        pipeline, fetcher, _store = _pipeline()
        q = _request(author_affiliation="Sabanci University")
        no_url = _result(database="IEEE Xplore", detail_url=None)
        pipeline.enrich([no_url], q)
        fetcher.fetch_affiliations.assert_not_called()


# ============================================================
# 4. 已有记录不重复请求
# ============================================================

class TestNoDuplicateRequest(unittest.TestCase):
    def test_existing_record_skips_fetch(self):
        fetcher = mock.Mock()
        fetcher.fetch_affiliations.return_value = ["Should Not Be Called"]
        store = MetadataStore()
        store.put(_record(IEEE_URL, "Cached University"))
        pipeline = MetadataPipeline(fetchers={"IEEE Xplore": fetcher}, metadata_store=store)
        q = _request(author_affiliation="Cached University")
        pipeline.enrich([_result()], q)
        fetcher.fetch_affiliations.assert_not_called()

    def test_second_enrich_reuses_cache(self):
        fetcher = mock.Mock()
        fetcher.fetch_affiliations.return_value = ["Sabanci University"]
        store = MetadataStore()
        pipeline = MetadataPipeline(fetchers={"IEEE Xplore": fetcher}, metadata_store=store)
        q = _request(author_affiliation="Sabanci University")
        r = _result()
        pipeline.enrich([r], q)  # 第一次：抓取并写入
        pipeline.enrich([r], q)  # 第二次：命中缓存，不再抓取
        self.assertEqual(fetcher.fetch_affiliations.call_count, 1)


# ============================================================
# 5. fetch 失败静默降级
# ============================================================

class TestFetchFailure(unittest.TestCase):
    def test_fetch_empty_result_no_write(self):
        fetcher = mock.Mock()
        fetcher.fetch_affiliations.return_value = []
        store = MetadataStore()
        pipeline = MetadataPipeline(fetchers={"IEEE Xplore": fetcher}, metadata_store=store)
        q = _request(author_affiliation="Sabanci University")
        out = pipeline.enrich([_result()], q)
        self.assertEqual(out, {})  # 不写入空记录
        self.assertIsNone(store.get(IEEE_URL))

    def test_fetch_exception_no_break(self):
        fetcher = mock.Mock()
        fetcher.fetch_affiliations.side_effect = RuntimeError("network down")
        store = MetadataStore()
        pipeline = MetadataPipeline(fetchers={"IEEE Xplore": fetcher}, metadata_store=store)
        q = _request(author_affiliation="Sabanci University")
        # 不应抛异常
        out = pipeline.enrich([_result()], q)
        self.assertEqual(out, {})
        self.assertIsNone(store.get(IEEE_URL))


# ============================================================
# 6. 写入 MetadataStore
# ============================================================

class TestWriteToStore(unittest.TestCase):
    def test_affiliations_stored(self):
        fetcher = mock.Mock()
        fetcher.fetch_affiliations.return_value = [
            "Sabanci University, Istanbul, Turkey",
            "Friedrich-Alexander-Universität, Erlangen",
        ]
        store = MetadataStore()
        pipeline = MetadataPipeline(fetchers={"IEEE Xplore": fetcher}, metadata_store=store)
        q = _request(author_affiliation="Sabanci University")
        out = pipeline.enrich([_result()], q)
        record = store.get(IEEE_URL)
        self.assertIsNotNone(record)
        self.assertEqual(len(record.affiliations), 2)
        self.assertIn("Sabanci University", record.affiliations[0])
        self.assertIsNotNone(record.fetched_at)
        self.assertIn(IEEE_URL, out)


# ============================================================
# 7. FilterService 收到正确 metadata_map（真实闭环）
# ============================================================

class TestFilterReceivesMetadata(unittest.TestCase):
    def test_affiliation_filter_closes_loop(self):
        """enrich 产物 → FilterService：单位匹配的 IEEE 结果保留、无元数据的保留。"""
        fetcher = mock.Mock()
        fetcher.fetch_affiliations.return_value = ["Sabanci University, Istanbul, Turkey"]
        store = MetadataStore()
        pipeline = MetadataPipeline(fetchers={"IEEE Xplore": fetcher}, metadata_store=store)
        q = _request(author_affiliation="Sabanci University")
        match = _result(title="匹配", detail_url=IEEE_URL)
        # CNKI 结果：pipeline 不抓取 → 无元数据 → FilterService 走"保留"路径
        other = _result(title="其他", database="CNKI",
                        detail_url="https://kns.cnki.net/detail/2")
        metadata_map = pipeline.enrich([match, other], q)
        self.assertIn(IEEE_URL, metadata_map)
        self.assertNotIn("https://kns.cnki.net/detail/2", metadata_map)
        svc = FilterService()
        kept = svc.filter([match, other], q, metadata_map=metadata_map)
        self.assertEqual({r.title for r in kept}, {"匹配", "其他"})


# ============================================================
# 8. 普通搜索流程不受影响（SearchService 集成）
# ============================================================

class TestSearchServiceIntegration(unittest.TestCase):
    @staticmethod
    def _make_service(adapter_results, pipeline=None):
        from tju_info_retrieval.services.search_service import SearchService
        session = mock.Mock()
        session.check_tju_auth.return_value = "logged_in"
        adapter = mock.Mock()
        adapter.search.return_value = adapter_results
        registry = mock.Mock()
        cls = mock.Mock()
        cls.return_value = adapter
        registry.get.return_value = cls
        return SearchService(session, registry=registry, metadata_pipeline=pipeline), adapter

    def test_normal_search_pipeline_unaffected(self):
        """无 author_affiliation 时：结果全保留、fetcher 不被调用。"""
        fetcher = mock.Mock()
        pipeline = MetadataPipeline(fetchers={"IEEE Xplore": fetcher}, metadata_store=MetadataStore())
        results = [
            _result(title="A", detail_url="https://ieeexplore.ieee.org/document/a/"),
            _result(title="B", detail_url="https://ieeexplore.ieee.org/document/b/"),
        ]
        service, _adapter = self._make_service(results, pipeline=pipeline)
        q = _request()
        out = service.search(q)
        self.assertEqual({r.title for r in out}, {"A", "B"})
        fetcher.fetch_affiliations.assert_not_called()

    def test_author_affiliation_search_fetches_and_filters(self):
        """用户输入机构名 → IEEE 详情增强 → FilterService 过滤 → 结果变化。"""
        fetcher = mock.Mock()
        fetcher.fetch_affiliations.return_value = ["Sabanci University, Istanbul, Turkey"]
        pipeline = MetadataPipeline(fetchers={"IEEE Xplore": fetcher}, metadata_store=MetadataStore())
        results = [
            _result(title="Sabanci论文", detail_url=IEEE_URL),
            _result(title="CNKI论文", database="CNKI",
                    detail_url="https://kns.cnki.net/detail/7"),
        ]
        service, _adapter = self._make_service(results, pipeline=pipeline)
        q = _request(author_affiliation="Sabanci University")
        out = service.search(q)
        # 仅 IEEE 结果触发抓取；CNKI 论文无元数据走"保留"路径
        fetcher.fetch_affiliations.assert_called_once_with(IEEE_URL)
        self.assertEqual({r.title for r in out}, {"Sabanci论文", "CNKI论文"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
