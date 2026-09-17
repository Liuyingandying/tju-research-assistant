#!/usr/bin/env python3
"""正式测试：SearchService 接入 QueryExpansionService。

覆盖：expansion 被调用 / 扩展串传给 adapter / 综合方向保持原行为 /
状态消息含实际检索串 / last_expanded 保留扩展信息 /
多源候选配额均衡（_allocate_counts 纯函数与调度传递）。
"""
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.models.query import QueryRequest, RankingMode
from tju_info_retrieval.services.citation_selector import candidate_quota
from tju_info_retrieval.services.query_expansion import ExpandedQuery
from tju_info_retrieval.services.search_service import SearchService


def _make_service():
    """构造 mock 化的 SearchService（无真实浏览器）。"""
    session = mock.Mock()
    session.check_tju_auth.return_value = "logged_in"
    adapter = mock.Mock()
    adapter.search.return_value = []
    # mock registry：get 返回一个类，实例化后返回 mock adapter
    registry = mock.Mock()
    mock_cls = mock.Mock()
    mock_cls.return_value = adapter
    registry.get.return_value = mock_cls
    service = SearchService(session, registry=registry)
    return service, adapter, registry


def _request(direction="太赫兹", expansion="综合"):
    return QueryRequest(
        research_direction=direction,
        expansion_direction=expansion,
        result_count=10,
    )


class TestSearchServiceExpansion(unittest.TestCase):
    def test_search_calls_expansion_with_direction(self):
        service, adapter, _registry = _make_service()
        service._expansion = mock.Mock()
        service._expansion.expand.return_value = ExpandedQuery(
            original="太赫兹", expansion_direction="理论",
            terms=["太赫兹"], filters=["理论"], query_string="Q",
        )
        service.search(_request("太赫兹", "理论"))
        service._expansion.expand.assert_called_once_with("太赫兹", "理论")
        adapter.search.assert_called_once_with("Q", 10, query_context=mock.ANY)

    def test_expanded_query_passed_to_adapter(self):
        service, adapter, _registry = _make_service()
        service.search(_request("太赫兹", "理论"))
        query_string = adapter.search.call_args[0][0]
        self.assertIn("太赫兹", query_string)
        self.assertIn("OR", query_string)
        self.assertIn("AND", query_string)
        self.assertIn("理论", query_string)

    def test_general_direction_keeps_original_behavior(self):
        # 综合 + 未命中词表 → 原词直传（完全原行为）
        service, adapter, _registry = _make_service()
        service.search(_request("量子计算", "综合"))
        self.assertEqual(adapter.search.call_args[0][0], "量子计算")

    def test_general_direction_no_direction_filter(self):
        # 综合 + 命中词表 → 仅同义词扩展，无方向限定（无 AND）
        service, adapter, _registry = _make_service()
        service.search(_request("太赫兹", "综合"))
        query_string = adapter.search.call_args[0][0]
        self.assertIn("OR", query_string)
        self.assertNotIn("AND", query_string)

    def test_status_reports_actual_query_string(self):
        service, _adapter, _registry = _make_service()
        messages: list[str] = []
        service.search(_request("太赫兹", "理论"), status=messages.append)
        actual = adapter_qs = None
        joined = "\n".join(messages)
        self.assertIn("检索词：太赫兹", joined)
        self.assertIn("方向：理论", joined)
        # 实际发送给 CNKI 的检索串出现在状态消息中
        eq = service.last_expanded
        self.assertIsNotNone(eq)
        self.assertIn(eq.query_string, joined)

    def test_last_expanded_preserves_expansion_info(self):
        service, _adapter, _registry = _make_service()
        service.search(_request("太赫兹", "理论"))
        eq = service.last_expanded
        self.assertIsNotNone(eq)
        self.assertEqual(eq.original, "太赫兹")
        self.assertEqual(eq.expansion_direction, "理论")
        self.assertEqual(eq.query_string, service.last_expanded.query_string)

    def test_adapter_receives_result_count(self):
        service, adapter, _registry = _make_service()
        req = _request("量子计算", "综合")
        req.result_count = 20
        service.search(req)
        self.assertEqual(adapter.search.call_args[0][1], 20)

    # ---------- 多源路由 ----------

    def test_multi_source_calls_each_adapter(self):
        session = mock.Mock()
        session.check_tju_auth.return_value = "logged_in"
        adapter1 = mock.Mock()
        adapter1.search.return_value = [mock.Mock(title="A")]
        adapter2 = mock.Mock()
        adapter2.search.return_value = [mock.Mock(title="B")]
        registry = mock.Mock()
        cls1 = mock.Mock(); cls1.return_value = adapter1
        cls2 = mock.Mock(); cls2.return_value = adapter2
        side = {"S1": cls1, "S2": cls2}
        registry.get.side_effect = lambda name: side[name]
        service = SearchService(session, registry=registry)
        req = _request("太赫兹", "综合")
        req.sources = ["S1", "S2"]
        results = service.search(req)
        assert len(results) == 2
        assert results[0].title == "A"
        assert results[1].title == "B"
        assert results[0].rank == 1
        assert results[1].rank == 2

    def test_multi_source_uses_translated_query_per_source(self):
        """多源：每个 Adapter 收到对应语言的检索串（中文透传 / 英文 Boolean）。"""
        session = mock.Mock()
        session.check_tju_auth.return_value = "logged_in"
        adapter_cnki = mock.Mock()
        adapter_cnki.search.return_value = []
        adapter_ieee = mock.Mock()
        adapter_ieee.search.return_value = []
        registry = mock.Mock()
        cls1 = mock.Mock(); cls1.return_value = adapter_cnki
        cls2 = mock.Mock(); cls2.return_value = adapter_ieee
        side = {"CNKI": cls1, "IEEE Xplore": cls2}
        registry.get.side_effect = lambda name: side[name]
        service = SearchService(session, registry=registry)
        req = _request("通感一体化", "综合")
        req.sources = ["CNKI", "IEEE Xplore"]
        service.search(req)
        # v0.13 多源配额均衡：result_count=10 双源 → 各源配额 5
        adapter_cnki.search.assert_called_once_with(
            "通感一体化", 5, query_context=mock.ANY
        )
        adapter_ieee.search.assert_called_once_with(
            '("integrated sensing and communication" OR ISAC)', 5,
            query_context=mock.ANY,
        )

    # ---------- Merger 集成 ----------

    def test_single_source_unchanged(self):
        """单源：结果数量不变，经过 merger 后仍为 1 条。"""
        service, adapter, _registry = _make_service()
        from tju_info_retrieval.models.result import SearchResult
        adapter.search.return_value = [SearchResult(rank=1, title="A")]
        req = _request("太赫兹", "综合")
        req.sources = ["CNKI"]
        results = service.search(req)
        assert len(results) == 1
        assert results[0].title == "A"

    def test_multi_source_dedup_by_title(self):
        """多源相同标题 → merger 去重后仅保留 1 条。"""
        session = mock.Mock()
        session.check_tju_auth.return_value = "logged_in"
        from tju_info_retrieval.models.result import SearchResult
        adapter1 = mock.Mock()
        adapter1.search.return_value = [SearchResult(rank=1, title="Same Title")]
        adapter2 = mock.Mock()
        adapter2.search.return_value = [SearchResult(rank=1, title="Same Title")]
        registry = mock.Mock()
        cls1 = mock.Mock(); cls1.return_value = adapter1
        cls2 = mock.Mock(); cls2.return_value = adapter2
        side = {"CNKI": cls1, "万方": cls2}
        registry.get.side_effect = lambda name: side[name]
        service = SearchService(session, registry=registry)
        req = _request("太赫兹", "综合")
        req.sources = ["CNKI", "万方"]
        results = service.search(req)
        assert len(results) == 1
        assert results[0].title == "Same Title"
        assert results[0].rank == 1

    def test_multi_source_rank_renumbered(self):
        """多源不同标题 → 合并后 rank 连续 1, 2, 3..."""
        session = mock.Mock()
        session.check_tju_auth.return_value = "logged_in"
        from tju_info_retrieval.models.result import SearchResult
        adapter1 = mock.Mock()
        adapter1.search.return_value = [SearchResult(rank=99, title="A")]
        adapter2 = mock.Mock()
        adapter2.search.return_value = [SearchResult(rank=88, title="B")]
        adapter3 = mock.Mock()
        adapter3.search.return_value = [SearchResult(rank=77, title="C")]
        registry = mock.Mock()
        cls1 = mock.Mock(); cls1.return_value = adapter1
        cls2 = mock.Mock(); cls2.return_value = adapter2
        cls3 = mock.Mock(); cls3.return_value = adapter3
        side = {"S1": cls1, "S2": cls2, "S3": cls3}
        registry.get.side_effect = lambda name: side[name]
        service = SearchService(session, registry=registry)
        req = _request("太赫兹", "综合")
        req.sources = ["S1", "S2", "S3"]
        results = service.search(req)
        assert len(results) == 3
        assert [r.rank for r in results] == [1, 2, 3]

    # ---------- Ranking 集成 ----------

    def test_search_service_applies_ranking_after_merge(self):
        """Merger 后应用 RankingService：标题匹配优先于较新但不相关的结果。"""
        service, adapter, _registry = _make_service()
        from tju_info_retrieval.models.result import SearchResult
        matched = SearchResult(rank=2, title="太赫兹成像方法", year="2015", database="CNKI")
        unmatched = SearchResult(rank=1, title="量子计算方法", year="2026", database="CNKI")
        adapter.search.return_value = [unmatched, matched]
        req = _request("太赫兹", "综合")
        req.sources = ["CNKI"]
        results = service.search(req)
        assert results[0] is matched
        assert results[0].rank == 1
        assert results[1].rank == 2

    # ---------- v0.11 候选池解耦 ----------

    def test_candidate_count_passed_to_adapter(self):
        """candidate_count=100 时，adapter 收到 100 而非 result_count。"""
        service, adapter, _registry = _make_service()
        req = _request("量子计算", "综合")
        req.result_count = 10
        req.candidate_count = 100
        service.search(req)
        self.assertEqual(adapter.search.call_args[0][1], 100)

    def test_result_count_truncates_after_ranking(self):
        """候选池 50 条但 result_count=10 时，返回仅 10 条。"""
        service, adapter, _registry = _make_service()
        from tju_info_retrieval.models.result import SearchResult
        # 模拟 adapter 返回 50 条候选
        candidates = [SearchResult(rank=i, title=f"Paper {i}") for i in range(1, 51)]
        adapter.search.return_value = candidates
        req = _request("量子计算", "综合")
        req.result_count = 10
        req.candidate_count = 50
        results = service.search(req)
        self.assertEqual(len(results), 10)
        # 截断后的 rank 应连续 1..10
        self.assertEqual([r.rank for r in results], list(range(1, 11)))

    def test_default_candidate_count_equals_result_count(self):
        """未显式设置 candidate_count 时，effective_candidate_count == result_count。"""
        service, adapter, _registry = _make_service()
        req = _request("量子计算", "综合")
        req.result_count = 20
        # candidate_count 默认为 0
        self.assertEqual(req.candidate_count, 0)
        self.assertEqual(req.effective_candidate_count, 20)
        service.search(req)
        # adapter 应收到 20
        self.assertEqual(adapter.search.call_args[0][1], 20)

    def test_truncation_preserves_ranking_order(self):
        """截断后前 N 条保持 ranking 排序顺序。"""
        service, adapter, _registry = _make_service()
        from tju_info_retrieval.models.result import SearchResult
        # 构造 30 条：前 10 条标题含"太赫兹"，后 20 条不含
        candidates = [
            SearchResult(rank=i, title=f"太赫兹相关研究 {i}", database="CNKI")
            for i in range(1, 11)
        ] + [
            SearchResult(rank=i, title=f"其他领域 {i}", database="CNKI")
            for i in range(11, 31)
        ]
        adapter.search.return_value = candidates
        req = _request("太赫兹", "综合")
        req.result_count = 10
        req.candidate_count = 30
        results = service.search(req)
        self.assertEqual(len(results), 10)
        # 所有返回结果标题都应含"太赫兹"（ranking 将相关结果排在前面）
        for r in results:
            self.assertIn("太赫兹", r.title)


class TestAllocateCounts(unittest.TestCase):
    """v0.13 多源候选配额均衡：_allocate_counts 纯函数测试。"""

    def test_single_source_gets_full_count(self):
        """单源：配额 = 完整数量，行为与旧版一致。"""
        self.assertEqual(SearchService._allocate_counts(20, 1), [20])
        self.assertEqual(SearchService._allocate_counts(1, 1), [1])

    def test_two_sources_even_split(self):
        """双源偶数：精确对半。"""
        self.assertEqual(SearchService._allocate_counts(20, 2), [10, 10])

    def test_two_sources_odd_front_gets_extra(self):
        """双源奇数：相差 1，前置数据源多 1。"""
        self.assertEqual(SearchService._allocate_counts(15, 2), [8, 7])
        self.assertEqual(SearchService._allocate_counts(11, 2), [6, 5])

    def test_three_sources_balanced(self):
        """三源：尽量均分，余数给前置源。"""
        self.assertEqual(SearchService._allocate_counts(20, 3), [7, 7, 6])
        self.assertEqual(SearchService._allocate_counts(21, 3), [7, 7, 7])

    def test_total_less_than_sources_tail_zero(self):
        """N < 源数：尾部源配额为 0（调用方跳过）。"""
        self.assertEqual(SearchService._allocate_counts(2, 3), [1, 1, 0])
        self.assertEqual(SearchService._allocate_counts(1, 3), [1, 0, 0])

    def test_sum_always_equals_total(self):
        """性质测试：任意 N/源数组合，配额之和恒等于 N。"""
        for total in range(1, 25):
            for n in (1, 2, 3):
                quotas = SearchService._allocate_counts(total, n)
                self.assertEqual(sum(quotas), total)
                self.assertEqual(len(quotas), n)
                self.assertTrue(all(q >= 0 for q in quotas))
                self.assertLessEqual(max(quotas) - min(quotas), 1)


class TestQuotaIntegration(unittest.TestCase):
    """配额在 search() 调度中的实际传递。"""

    def _make_multi_source_service(self, returns: dict):
        """按源名构造 mock adapter 的 SearchService。

        returns: {源名: adapter.search 返回值}
        """
        session = mock.Mock()
        session.check_tju_auth.return_value = "logged_in"
        adapters: dict[str, mock.Mock] = {}
        registry = mock.Mock()
        side = {}
        for name, value in returns.items():
            adapter = mock.Mock()
            adapter.search.return_value = value
            adapters[name] = adapter
            cls = mock.Mock()
            cls.return_value = adapter
            side[name] = cls
        registry.get.side_effect = lambda name: side[name]
        return SearchService(session, registry=registry), adapters

    def test_odd_count_two_sources_front_gets_extra(self):
        """N=15 双源：CNKI 收到 8、万方收到 7（前置源多 1）。"""
        from tju_info_retrieval.models.result import SearchResult
        service, adapters = self._make_multi_source_service({
            "CNKI": [SearchResult(rank=1, title="A")],
            "万方": [SearchResult(rank=1, title="B")],
        })
        req = _request("太赫兹", "综合")
        req.result_count = 15
        req.sources = ["CNKI", "万方"]
        results = service.search(req)
        self.assertEqual(adapters["CNKI"].search.call_args[0][1], 8)
        self.assertEqual(adapters["万方"].search.call_args[0][1], 7)
        self.assertEqual(len(results), 2)

    def test_three_sources_each_gets_quota(self):
        """N=20 三源：配额 7 / 7 / 6。"""
        from tju_info_retrieval.models.result import SearchResult
        service, adapters = self._make_multi_source_service({
            "S1": [SearchResult(rank=1, title="A")],
            "S2": [SearchResult(rank=1, title="B")],
            "S3": [SearchResult(rank=1, title="C")],
        })
        req = _request("太赫兹", "综合")
        req.result_count = 20
        req.sources = ["S1", "S2", "S3"]
        service.search(req)
        self.assertEqual(adapters["S1"].search.call_args[0][1], 7)
        self.assertEqual(adapters["S2"].search.call_args[0][1], 7)
        self.assertEqual(adapters["S3"].search.call_args[0][1], 6)

    def test_zero_quota_source_skipped(self):
        """N=2 三源：尾部源配额 0 → 不调用其 Adapter，状态提示跳过。"""
        from tju_info_retrieval.models.result import SearchResult
        service, adapters = self._make_multi_source_service({
            "S1": [SearchResult(rank=1, title="A")],
            "S2": [SearchResult(rank=1, title="B")],
            "S3": [SearchResult(rank=1, title="C")],
        })
        req = _request("太赫兹", "综合")
        req.result_count = 2
        req.sources = ["S1", "S2", "S3"]
        messages: list[str] = []
        results = service.search(req, status=messages.append)
        self.assertEqual(adapters["S1"].search.call_args[0][1], 1)
        self.assertEqual(adapters["S2"].search.call_args[0][1], 1)
        self.assertEqual(adapters["S3"].search.call_count, 0)
        self.assertTrue(any("S3" in m and "配额为 0" in m for m in messages))
        self.assertEqual(len(results), 2)

    def test_single_source_quota_unchanged(self):
        """单源回归：配额 = 完整 result_count，不受均衡逻辑影响。"""
        from tju_info_retrieval.models.result import SearchResult
        service, adapters = self._make_multi_source_service({
            "CNKI": [SearchResult(rank=1, title="A")],
        })
        req = _request("太赫兹", "综合")
        req.result_count = 15
        req.sources = ["CNKI"]
        service.search(req)
        self.assertEqual(adapters["CNKI"].search.call_args[0][1], 15)


class TestAuthorSearchOrchestration(unittest.TestCase):
    """v0.17 Phase 1：研究方向 OR 人员姓名的编排语义。

    - 纯人员姓名：不调用 topic expansion 链（expansion/translator/builder
      均不参与），Adapter 收到空检索串（无 ""/"()"/"( OR )" 伪查询）；
    - topic + person：topic expansion 正常运行；
    - CITATION 模式仅改候选配额，检索条件与综合推荐完全一致。
    """

    def _person_request(self, direction="", expansion="综合", person="张三"):
        req = _request(direction, expansion)
        req.author_name = person
        req.sources = ["CNKI"]
        return req

    def test_person_only_skips_expansion_chain(self):
        service, adapter, _registry = _make_service()
        service._expansion = mock.Mock()
        service._translator = mock.Mock()
        service._builder = mock.Mock()
        service.search(self._person_request())
        service._expansion.expand.assert_not_called()
        service._translator.translate_concepts.assert_not_called()
        service._builder.build.assert_not_called()
        self.assertIsNone(service.last_expanded)

    def test_person_only_no_pseudo_query(self):
        """Adapter 收到空检索串：无空括号/None/退化 Boolean。"""
        service, adapter, _registry = _make_service()
        service.search(self._person_request())
        keyword = adapter.search.call_args[0][0]
        self.assertEqual(keyword, "")
        self.assertNotIn("OR", keyword)
        self.assertNotIn("(", keyword)

    def test_person_only_status_message(self):
        service, _adapter, _registry = _make_service()
        messages: list[str] = []
        service.search(self._person_request(), status=messages.append)
        self.assertTrue(any("作者检索" in m and "张三" in m for m in messages))

    def test_expansion_direction_ignored_for_person_only(self):
        """纯人名时扩展方向不参与查询构造（综述/理论/应用均不附加）。"""
        service, adapter, _registry = _make_service()
        service._expansion = mock.Mock()
        service.search(self._person_request(expansion="综述"))
        service._expansion.expand.assert_not_called()
        self.assertEqual(adapter.search.call_args[0][0], "")

    def test_topic_and_person_expansion_runs(self):
        service, adapter, _registry = _make_service()
        service._expansion = mock.Mock()
        service._expansion.expand.return_value = ExpandedQuery(
            original="太赫兹", expansion_direction="理论",
            terms=["太赫兹"], filters=["理论"], query_string="Q",
        )
        service.search(self._person_request(direction="太赫兹", expansion="理论"))
        service._expansion.expand.assert_called_once_with("太赫兹", "理论")
        self.assertEqual(adapter.search.call_args[0][0], "Q")

    def test_both_empty_raises(self):
        service, _adapter, _registry = _make_service()
        with self.assertRaises(ValueError) as ctx:
            service.search(self._person_request(person=""))
        self.assertIn("请输入研究方向或人员姓名", str(ctx.exception))

    def test_affiliation_only_raises(self):
        """仅人员单位不能作为主检索条件（防御性校验第二道）。"""
        service, _adapter, _registry = _make_service()
        req = self._person_request(person="")
        req.author_affiliation = "天津大学"
        with self.assertRaises(ValueError):
            service.search(req)

    def test_whitespace_person_counts_as_empty(self):
        service, _adapter, _registry = _make_service()
        with self.assertRaises(ValueError):
            service.search(self._person_request(direction="  ", person="  "))

    def test_citation_person_only_same_query_as_comprehensive(self):
        """高引用与综合推荐的实际检索条件一致，仅候选配额不同。"""
        kw_plain, kw_cite, count_plain, count_cite = None, None, None, None
        service, adapter, _registry = _make_service()
        service.search(self._person_request())
        kw_plain = adapter.search.call_args[0][0]
        count_plain = adapter.search.call_args[0][1]
        service2, adapter2, _registry2 = _make_service()
        req = self._person_request()
        req.ranking_mode = RankingMode.CITATION.value
        service2.search(req)
        kw_cite = adapter2.search.call_args[0][0]
        count_cite = adapter2.search.call_args[0][1]
        self.assertEqual(kw_plain, "")
        self.assertEqual(kw_cite, "")
        # CITATION 扩大候选池（candidate_quota），检索串不变
        self.assertEqual(count_plain, 10)
        self.assertEqual(count_cite, candidate_quota(10))


    def test_person_only_citation_end_to_end(self):
        """纯人名 + 高引用：作者本地过滤 → 引用选择 → 排序全链路正常。"""
        from tju_info_retrieval.models.result import SearchResult
        session = mock.Mock()
        session.check_tju_auth.return_value = "logged_in"
        adapter = mock.Mock()
        adapter.search.return_value = [
            SearchResult(rank=1, title="张三的太赫兹论文", authors=["张三", "李四"],
                         database="CNKI", citation_count=5),
            SearchResult(rank=2, title="别人的论文", authors=["王五"],
                         database="CNKI", citation_count=50),
        ]
        registry = mock.Mock()
        cls = mock.Mock()
        cls.return_value = adapter
        registry.get.return_value = cls
        service = SearchService(session, registry=registry)
        req = self._person_request()
        req.ranking_mode = RankingMode.CITATION.value
        req.result_count = 2
        results = service.search(req)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "张三的太赫兹论文")
        self.assertIn("张三", results[0].authors)

    def test_person_only_multi_source_empty_keyword(self):
        """多源纯人名：每个 Adapter 都收到空检索串 + 完整 query_context。"""
        from tju_info_retrieval.models.result import SearchResult
        session = mock.Mock()
        session.check_tju_auth.return_value = "logged_in"
        adapters: dict[str, mock.Mock] = {}
        registry = mock.Mock()
        side = {}
        for name in ("CNKI", "万方"):
            a = mock.Mock()
            a.search.return_value = [SearchResult(rank=1, title=f"T-{name}",
                                                  authors=["张三"], database=name)]
            adapters[name] = a
            c = mock.Mock()
            c.return_value = a
            side[name] = c
        registry.get.side_effect = lambda name: side[name]
        service = SearchService(session, registry=registry)
        req = self._person_request()
        req.sources = ["CNKI", "万方"]
        results = service.search(req)
        for name, a in adapters.items():
            self.assertEqual(a.search.call_args[0][0], "")
            self.assertEqual(a.search.call_args.kwargs["query_context"], req)
        self.assertEqual(len(results), 2)


class TestSourceQueryRouting(unittest.TestCase):
    """v0.16 万方关键词路由回归（D）：IEEE 保持扩展 Boolean，不受万方修复影响。

    SearchService 边界契约：两源 Adapter 收到的都是扩展后的 query_string；
    「万方用原始词」的映射发生在 WanfangAdapter 内部（由
    tests/test_wanfang.py::TestWanfangKeywordRouting 单元覆盖）。
    """

    def test_ieee_keeps_expanded_boolean(self):
        service, adapters = TestQuotaIntegration()._make_multi_source_service({
            "万方": [],
            "IEEE Xplore": [],
        })
        req = _request("太赫兹", "综合")
        req.sources = ["万方", "IEEE Xplore"]
        service.search(req)
        ieee_kw = adapters["IEEE Xplore"].search.call_args[0][0]
        wanfang_kw = adapters["万方"].search.call_args[0][0]
        self.assertIn(" OR ", ieee_kw)          # 扩展 Boolean 仍在
        self.assertNotEqual(ieee_kw, "太赫兹")  # 未退化为中文原始词
        # 既有跨语言路由：IEEE 收英文概念扩展，万方收中文概念扩展
        # （都是扩展 query_string，万方 Adapter 内部再路由为原始词，
        # 由 tests/test_wanfang.py::TestWanfangKeywordRouting 覆盖）
        self.assertEqual(ieee_kw, "(terahertz OR THz)")
        self.assertEqual(wanfang_kw, "(太赫兹 OR THz OR 太赫兹波 OR Terahertz)")


if __name__ == "__main__":

    unittest.main(verbosity=2)
