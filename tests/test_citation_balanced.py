#!/usr/bin/env python3
"""v0.16 Phase B：来源均衡高引用 Top-K 纯逻辑测试。

覆盖（任务要求九项）：
1. 输出配额：20/2→10+10、15/2→8+7、20/3→7+7+6、2/3→1+1+0；
2. 候选配额规则 candidate_quota；
3. 来源内排序：citation 降序；None 恒排已知之后（20,5,0,None）；
4. 整来源全 None：回退综合排序、保留 quota、不为空；
5. 双源 Top-K（20 候选/源，N=20 → 10+10）；
6. 奇数（N=15 → 8+7）；
7. 三源（N=20 → 7+7+6）；
8. 缺额回填（CNKI 4 篇 + 万方 30 篇 → 4+16）；
9. 跨库重复不为 quota 保留；
10. 普通模式回归：候选数与排序管线完全不变。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tju_info_retrieval.models.query import QueryRequest, RankingMode
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.citation_selector import (
    CitationBalancedSelector,
    candidate_quota,
)
from tju_info_retrieval.services.result_merger import ResultMerger
from tju_info_retrieval.services.search_service import SearchService


def _result(title, database, citation_count=None, year="2020", doi=None):
    return SearchResult(
        rank=0,
        title=title,
        authors=[],
        source=None,
        year=year,
        detail_url=f"https://example.com/{database}/{title}",
        database=database,
        doi=doi,
        citation_count=citation_count,
    )


def _query(mode=RankingMode.CITATION.value, count=20, sources=("CNKI", "万方")):
    return QueryRequest(
        research_direction="太赫兹",
        result_count=count,
        ranking_mode=mode,
        sources=list(sources),
        databases=list(sources),
    )


class TestOutputQuota(unittest.TestCase):
    """B1：复用 _allocate_counts 的输出配额。"""

    def test_20_over_2(self):
        self.assertEqual(SearchService._allocate_counts(20, 2), [10, 10])

    def test_15_over_2(self):
        self.assertEqual(SearchService._allocate_counts(15, 2), [8, 7])

    def test_20_over_3(self):
        self.assertEqual(SearchService._allocate_counts(20, 3), [7, 7, 6])

    def test_2_over_3(self):
        self.assertEqual(SearchService._allocate_counts(2, 3), [1, 1, 0])


class TestCandidateQuota(unittest.TestCase):
    """B2：候选配额 = max(3×output, output+10)，上限 50。"""

    def test_10(self):
        self.assertEqual(candidate_quota(10), 30)

    def test_7(self):
        self.assertEqual(candidate_quota(7), 21)

    def test_small_output_plus_buffer(self):
        # output=2 → max(6, 12) = 12（保证缓冲）
        self.assertEqual(candidate_quota(2), 12)

    def test_cap_50(self):
        self.assertEqual(candidate_quota(20), 50)
        self.assertEqual(candidate_quota(100), 50)

    def test_zero(self):
        self.assertEqual(candidate_quota(0), 0)


class TestPerSourceOrdering(unittest.TestCase):
    """B3：来源内排序语义。"""

    def setUp(self):
        self.query = _query()

    def _select_one_source(self, cites, database="CNKI", quota=10):
        items = [
            _result(f"论文{i}", database, citation_count=c)
            for i, c in enumerate(cites)
        ]
        out = CitationBalancedSelector.select(items, self.query, {database: quota})
        return [r.citation_count for r in out]

    def test_known_citations_descending(self):
        """[100, 50, 3] → 100, 50, 3 降序。"""
        self.assertEqual(self._select_one_source([100, 50, 3]), [100, 50, 3])

    def test_none_after_known_zero(self):
        """[20, None, 0, 5] → 20, 5, 0, None（None 恒排已知之后）。"""
        self.assertEqual(self._select_one_source([20, None, 0, 5]), [20, 5, 0, None])

    def test_none_not_treated_as_zero(self):
        """None 不得排在 0 之前（None ≠ 0）。"""
        ordered = self._select_one_source([None, 0])
        self.assertEqual(ordered, [0, None])

    def test_all_none_falls_back_to_comprehensive_and_keeps_quota(self):
        """整来源全 None：按综合分回退排序（新论文在前），保留 quota 不为空。"""
        items = [
            _result("旧论文", "CNKI", citation_count=None, year="2001"),
            _result("新论文", "CNKI", citation_count=None, year="2026"),
            _result("中论文", "CNKI", citation_count=None, year="2015"),
        ]
        out = CitationBalancedSelector.select(items, self.query, {"CNKI": 3})
        self.assertEqual(len(out), 3)  # 不为空、quota 保留
        self.assertEqual([r.title for r in out], ["新论文", "中论文", "旧论文"])


class TestBalancedTopK(unittest.TestCase):
    """B3/B4：多源均衡 + 缺额回填。"""

    def setUp(self):
        self.query = _query(count=20)

    def test_two_sources_topk(self):
        """双源各 20 候选，N=20 → CNKI 10 + 万方 10。"""
        items = (
            [_result(f"c{i}", "CNKI", citation_count=100 - i) for i in range(20)]
            + [_result(f"w{i}", "万方", citation_count=200 - i) for i in range(20)]
        )
        out = CitationBalancedSelector.select(
            items, self.query, {"CNKI": 10, "万方": 10}
        )
        by_db = {"CNKI": 0, "万方": 0}
        for r in out:
            by_db[r.database] += 1
        self.assertEqual(by_db, {"CNKI": 10, "万方": 10})
        # 每源取的是各自被引最高的前 10
        cnki = [r.citation_count for r in out if r.database == "CNKI"]
        wanfang = [r.citation_count for r in out if r.database == "万方"]
        self.assertEqual(sorted(cnki, reverse=True), cnki)
        self.assertEqual(min(cnki), 91)  # 100..91
        self.assertEqual(min(wanfang), 191)

    def test_odd_15(self):
        """N=15，CNKI+万方 → 8+7。"""
        query = _query(count=15)
        items = (
            [_result(f"c{i}", "CNKI", citation_count=50 - i) for i in range(20)]
            + [_result(f"w{i}", "万方", citation_count=50 - i) for i in range(20)]
        )
        out = CitationBalancedSelector.select(
            items, query, {"CNKI": 8, "万方": 7}
        )
        by_db = {"CNKI": 0, "万方": 0}
        for r in out:
            by_db[r.database] += 1
        self.assertEqual(by_db, {"CNKI": 8, "万方": 7})

    def test_three_sources(self):
        """N=20，三源 → 7+7+6。"""
        query = _query(count=20, sources=("CNKI", "万方", "IEEE Xplore"))
        items = []
        for db, base in (("CNKI", 300), ("万方", 200), ("IEEE Xplore", 100)):
            items.extend(
                _result(f"{db}{i}", db, citation_count=base - i) for i in range(20)
            )
        out = CitationBalancedSelector.select(
            items, query, {"CNKI": 7, "万方": 7, "IEEE Xplore": 6}
        )
        by_db = {"CNKI": 0, "万方": 0, "IEEE Xplore": 0}
        for r in out:
            by_db[r.database] += 1
        self.assertEqual(by_db, {"CNKI": 7, "万方": 7, "IEEE Xplore": 6})

    def test_shortage_backfill(self):
        """CNKI 仅 4 篇候选、万方 30 篇，N=20 → 4+16（缺额全部回填）。"""
        items = [_result(f"c{i}", "CNKI", citation_count=10 - i) for i in range(4)]
        items += [_result(f"w{i}", "万方", citation_count=100 - i) for i in range(30)]
        out = CitationBalancedSelector.select(
            items, self.query, {"CNKI": 10, "万方": 10}
        )
        by_db = {"CNKI": 0, "万方": 0}
        for r in out:
            by_db[r.database] += 1
        self.assertEqual(by_db, {"CNKI": 4, "万方": 16})
        self.assertEqual(len(out), 20)

    def test_backfill_round_robin_deterministic(self):
        """多个足额源之间按配额表顺序轮询回填（确定性）。

        N=15 三源 → 配额 [5,5,5]；CNKI 仅 3 篇（缺 2），
        万方/IEEE 各 20 篇足额且有剩余 → 轮询 万方+1、IEEE+1 → 3+6+6。
        """
        query = _query(count=15, sources=("CNKI", "万方", "IEEE Xplore"))
        items = [_result(f"c{i}", "CNKI", citation_count=9 - i) for i in range(3)]
        items += [_result(f"w{i}", "万方", citation_count=50 - i) for i in range(20)]
        items += [
            _result(f"i{i}", "IEEE Xplore", citation_count=50 - i) for i in range(20)
        ]
        out = CitationBalancedSelector.select(
            items, query, {"CNKI": 5, "万方": 5, "IEEE Xplore": 5}
        )
        by_db = {"CNKI": 0, "万方": 0, "IEEE Xplore": 0}
        for r in out:
            by_db[r.database] += 1
        self.assertEqual(by_db, {"CNKI": 3, "万方": 6, "IEEE Xplore": 6})
        self.assertEqual(len(out), 15)

    def test_backfill_respects_source_order(self):
        """单源缺额、其余两源有剩余：按顺序从万方先补、补满再到 IEEE。

        N=15 三源 → [5,5,5]；CNKI 3 篇、万方仅剩 1 篇候选（6 篇总）、
        IEEE 20 篇 → CNKI 3 + 万方 5+1 + IEEE 5+1 = 15。
        """
        query = _query(count=15, sources=("CNKI", "万方", "IEEE Xplore"))
        items = [_result(f"c{i}", "CNKI", citation_count=9 - i) for i in range(3)]
        items += [_result(f"w{i}", "万方", citation_count=50 - i) for i in range(6)]
        items += [
            _result(f"i{i}", "IEEE Xplore", citation_count=50 - i) for i in range(20)
        ]
        out = CitationBalancedSelector.select(
            items, query, {"CNKI": 5, "万方": 5, "IEEE Xplore": 5}
        )
        by_db = {"CNKI": 0, "万方": 0, "IEEE Xplore": 0}
        for r in out:
            by_db[r.database] += 1
        self.assertEqual(by_db, {"CNKI": 3, "万方": 6, "IEEE Xplore": 6})

    def test_backfill_does_not_exceed_target(self):
        """候选充足时输出不超过 N。"""
        items = (
            [_result(f"c{i}", "CNKI", citation_count=100 - i) for i in range(20)]
            + [_result(f"w{i}", "万方", citation_count=100 - i) for i in range(20)]
        )
        out = CitationBalancedSelector.select(
            items, self.query, {"CNKI": 10, "万方": 10}
        )
        self.assertEqual(len(out), 20)


class TestDedupPrinciple(unittest.TestCase):
    """跨库重复：合并去重后分组，不为 quota 保留重复。"""

    def test_duplicate_not_double_counted(self):
        query = _query(count=4)
        dup_title = "太赫兹通信综述"
        items = [
            _result(dup_title, "CNKI", citation_count=30, doi="10.1/x"),
            # 同 DOI 跨库重复（万方侧）
            _result(dup_title, "万方", citation_count=30, doi="10.1/x"),
            _result("独有论文", "万方", citation_count=10),
        ]
        merged = ResultMerger.merge(items)  # 先合并去重（SearchService 同序）
        out = CitationBalancedSelector.select(
            merged, query, {"CNKI": 2, "万方": 2}
        )
        titles = [r.title for r in out]
        self.assertEqual(len(titles), len(set(titles)))  # 无重复展示
        self.assertEqual(len(out), 2)  # 去重后唯一论文只有 2 篇

    def test_merge_order_keeps_first_database_attribution(self):
        """去重保留首现来源（CNKI 在前 → 重复论文计入 CNKI 组）。"""
        dup = "太赫兹通信综述"
        items = [
            _result(dup, "CNKI", citation_count=30, doi="10.1/x"),
            _result(dup, "万方", citation_count=30, doi="10.1/x"),
            _result("万方独有", "万方", citation_count=5),
        ]
        merged = ResultMerger.merge(items)
        out = CitationBalancedSelector.select(
            merged, _query(count=2), {"CNKI": 1, "万方": 1}
        )
        by_db = {r.database: r.title for r in out}
        self.assertEqual(by_db.get("CNKI"), dup)
        self.assertEqual(by_db.get("万方"), "万方独有")


class _CapturingAdapter:
    """记录 search 调用参数的假适配器（普通模式回归验证用）。"""

    called_counts: list[int] = []

    def __init__(self, session):
        pass

    def search(self, keyword, count=10, query_context=None):
        _CapturingAdapter.called_counts.append(count)
        return []


class TestNormalModeRegression(unittest.TestCase):
    """九：普通模式候选数/排序管线完全不变。"""

    def test_normal_mode_uses_effective_candidate_count(self):
        """COMPREHENSIVE/RECENT：候选配额 = effective_candidate_count 均分（原行为）。"""
        session = mock.Mock()
        registry = mock.Mock()
        registry.get.return_value = _CapturingAdapter
        service = SearchService(session, registry=registry)
        _CapturingAdapter.called_counts = []
        query = QueryRequest(
            research_direction="太赫兹",
            result_count=20,
            ranking_mode=RankingMode.RECENT.value,
            sources=["CNKI", "万方"],
            databases=["CNKI", "万方"],
        )
        service.check_tju_auth = mock.Mock(return_value="logged_in")
        service.search(query)
        self.assertEqual(_CapturingAdapter.called_counts, [10, 10])

    def test_citation_mode_expands_candidates(self):
        """CITATION：候选配额按 output_quota 扩大（N=20 双源 → 各 30）。"""
        session = mock.Mock()
        registry = mock.Mock()
        registry.get.return_value = _CapturingAdapter
        service = SearchService(session, registry=registry)
        _CapturingAdapter.called_counts = []
        query = QueryRequest(
            research_direction="太赫兹",
            result_count=20,
            ranking_mode=RankingMode.CITATION.value,
            sources=["CNKI", "万方"],
            databases=["CNKI", "万方"],
        )
        service.check_tju_auth = mock.Mock(return_value="logged_in")
        service.search(query)
        self.assertEqual(_CapturingAdapter.called_counts, [30, 30])

    def test_citation_mode_three_sources_candidates(self):
        """CITATION：N=20 三源 → 输出 7+7+6 → 候选 21+21+18。"""
        session = mock.Mock()
        registry = mock.Mock()
        registry.get.return_value = _CapturingAdapter
        service = SearchService(session, registry=registry)
        _CapturingAdapter.called_counts = []
        query = QueryRequest(
            research_direction="太赫兹",
            result_count=20,
            ranking_mode=RankingMode.CITATION.value,
            sources=["CNKI", "万方", "IEEE Xplore"],
            databases=["CNKI", "万方", "IEEE Xplore"],
        )
        service.check_tju_auth = mock.Mock(return_value="logged_in")
        service.search(query)
        self.assertEqual(
            _CapturingAdapter.called_counts,
            [candidate_quota(7), candidate_quota(7), candidate_quota(6)],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
