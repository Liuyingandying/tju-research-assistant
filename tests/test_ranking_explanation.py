#!/usr/bin/env python3
"""v0.12 Phase 1：RankingExplanation 可解释排序数据层测试。

覆盖：
1. RankingExplanation 创建与序列化
2. 评分拆解正确（各分项与总分一致，reasons 事实性）
3. rank_with_explanation 与 rank 排序一致
4. citation_count=None 不生成引用原因
5. 三种 RankingMode 都能生成解释
6. 旧调用方式不受影响（rank() 行为 / 旧 Query 对象 / year_range 过滤）
"""
import math
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.models.query import QueryRequest, RankingMode
from tju_info_retrieval.models.ranking import RankingExplanation
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.ranking import RankingService

_CURRENT_YEAR = datetime.now().year


def _result(**kw):
    data = dict(rank=99, title="T", authors=["A"], source="S", year=None, database="CNKI")
    data.update(kw)
    return SearchResult(**data)


def _spec():
    """结果构造模板（每次生成全新实例，避免 rank 字段互相污染）。"""
    return [
        dict(title="太赫兹成像方法", year="2015", database="CNKI"),
        dict(title="量子计算方法", year=str(_CURRENT_YEAR), database="CNKI"),
        dict(title="太赫兹通信综述", year="2020", database="IEEE Xplore", citation_count=150),
    ]


def _fresh(spec=None):
    return [SearchResult(rank=i + 1, **kw) for i, kw in enumerate(spec if spec is not None else _spec())]


# ============================================================
# 1. RankingExplanation 创建与序列化
# ============================================================

class TestRankingExplanationModel(unittest.TestCase):
    def test_create_with_fields(self):
        exp = RankingExplanation(
            total_score=10.0, relevance_score=5.0, year_score=2.0,
            citation_score=2.0, database_score=1.0,
            reasons=["标题高度匹配检索词"],
        )
        self.assertEqual(exp.total_score, 10.0)
        self.assertEqual(exp.relevance_score, 5.0)
        self.assertEqual(exp.year_score, 2.0)
        self.assertEqual(exp.citation_score, 2.0)
        self.assertEqual(exp.database_score, 1.0)
        self.assertEqual(exp.reasons, ["标题高度匹配检索词"])

    def test_default_reasons_empty(self):
        exp = RankingExplanation(
            total_score=0.0, relevance_score=0.0, year_score=0.0,
            citation_score=0.0, database_score=0.0,
        )
        self.assertEqual(exp.reasons, [])

    def test_round_trip(self):
        exp = RankingExplanation(
            total_score=9.0, relevance_score=6.0, year_score=1.0,
            citation_score=1.5, database_score=0.5, reasons=["a", "b"],
        )
        restored = RankingExplanation.from_dict(exp.to_dict())
        self.assertEqual(restored, exp)

    def test_from_dict_missing_keys(self):
        exp = RankingExplanation.from_dict({})
        self.assertEqual(exp.total_score, 0.0)
        self.assertEqual(exp.relevance_score, 0.0)
        self.assertEqual(exp.reasons, [])

    def test_does_not_affect_search_result(self):
        """SearchResult 模型不含解释字段。"""
        r = _result(title="太赫兹方法")
        self.assertFalse(hasattr(r, "total_score"))
        self.assertFalse(hasattr(r, "reasons"))


# ============================================================
# 2. 评分拆解正确
# ============================================================

class TestScoreBreakdown(unittest.TestCase):
    def test_full_breakdown(self):
        query = QueryRequest(research_direction="太赫兹")
        ranked, exps = RankingService.rank_with_explanation(
            _fresh([dict(title="太赫兹成像方法", year=str(_CURRENT_YEAR),
                         database="CNKI", citation_count=150)]),
            query,
        )
        exp = exps[0]
        expected_year = min(_CURRENT_YEAR - 2000, 25)
        expected_citation = math.log10(151) * 10
        self.assertAlmostEqual(exp.relevance_score, 60.0)
        self.assertAlmostEqual(exp.year_score, expected_year)
        self.assertAlmostEqual(exp.citation_score, expected_citation)
        self.assertAlmostEqual(exp.database_score, 3.0)
        self.assertAlmostEqual(
            exp.total_score, 60.0 + expected_year + expected_citation + 3.0,
        )

    def test_total_equals_sum_of_components(self):
        query = QueryRequest(research_direction="太赫兹")
        _ranked, exps = RankingService.rank_with_explanation(_fresh(), query)
        for exp in exps:
            self.assertAlmostEqual(
                exp.total_score,
                exp.relevance_score + exp.year_score
                + exp.citation_score + exp.database_score,
            )

    def test_reasons_match_contributions(self):
        query = QueryRequest(research_direction="太赫兹")
        _ranked, exps = RankingService.rank_with_explanation(
            _fresh([dict(title="太赫兹成像方法", year=str(_CURRENT_YEAR),
                         database="CNKI", citation_count=150)]),
            query,
        )
        self.assertEqual(
            exps[0].reasons,
            ["标题高度匹配检索词", "发表于近5年", "引用量较高（被引150次）", "CNKI来源"],
        )

    def test_partial_match_reason(self):
        query = QueryRequest(research_direction="太赫兹 成像")
        _ranked, exps = RankingService.rank_with_explanation(
            _fresh([dict(title="太赫兹方法", year="2005", database="CNKI")]),
            query,
        )
        exp = exps[0]
        self.assertAlmostEqual(exp.relevance_score, 30.0)
        self.assertIn("标题部分匹配检索词", exp.reasons)
        self.assertIn("发表于2005年", exp.reasons)
        self.assertAlmostEqual(exp.citation_score, 0.0)

    def test_no_reasons_when_no_contribution(self):
        query = QueryRequest(research_direction="x")
        _ranked, exps = RankingService.rank_with_explanation(
            _fresh([dict(title="无关论文", database="冷门库")]),
            query,
        )
        self.assertEqual(exps[0].reasons, [])
        self.assertEqual(exps[0].total_score, 0.0)

    def test_low_citation_reason(self):
        query = QueryRequest(research_direction="太赫兹")
        _ranked, exps = RankingService.rank_with_explanation(
            _fresh([dict(title="太赫兹方法", year="2024", citation_count=12)]),
            query,
        )
        self.assertIn("被引12次", exps[0].reasons)


# ============================================================
# 3. rank_with_explanation 与 rank 排序一致
# ============================================================

class TestConsistencyWithRank(unittest.TestCase):
    def _assert_same(self, spec, query):
        r1 = RankingService.rank(_fresh(spec), query)
        r2, exps = RankingService.rank_with_explanation(_fresh(spec), query)
        self.assertEqual([r.title for r in r1], [r.title for r in r2])
        self.assertEqual([r.rank for r in r1], [r.rank for r in r2])
        self.assertEqual(len(exps), len(r2))
        return r2, exps

    def test_comprehensive_mode_consistent(self):
        query = QueryRequest(research_direction="太赫兹")
        r2, exps = self._assert_same(_spec(), query)
        # 综合分：通信综述(匹配+引用+IEEE) > 成像方法(匹配) > 量子计算(仅年份)
        self.assertEqual([r.title for r in r2][0], "太赫兹通信综述")
        for r, e in zip(r2, exps):
            self.assertIsInstance(r, SearchResult)
            self.assertIsInstance(e, RankingExplanation)

    def test_recent_mode_consistent(self):
        query = QueryRequest(
            research_direction="太赫兹", ranking_mode=RankingMode.RECENT.value,
        )
        r2, _exps = self._assert_same(_spec(), query)
        # 年份：2026 > 2020 > 2015
        self.assertEqual(
            [r.title for r in r2],
            ["量子计算方法", "太赫兹通信综述", "太赫兹成像方法"],
        )

    def test_citation_mode_consistent(self):
        query = QueryRequest(
            research_direction="太赫兹", ranking_mode=RankingMode.CITATION.value,
        )
        r2, _exps = self._assert_same(_spec(), query)
        # 高引用（150）优先；None 视为 0，按综合分次序
        self.assertEqual([r.title for r in r2][0], "太赫兹通信综述")

    def test_empty_input(self):
        query = QueryRequest(research_direction="太赫兹")
        self.assertEqual(RankingService.rank_with_explanation([], query), ([], []))


# ============================================================
# 4. citation_count=None 不生成引用原因
# ============================================================

class TestCitationNoneNoReason(unittest.TestCase):
    def test_none_citation_no_reason(self):
        query = QueryRequest(research_direction="太赫兹")
        _ranked, exps = RankingService.rank_with_explanation(
            _fresh([dict(title="太赫兹方法", year="2024", citation_count=None)]),
            query,
        )
        exp = exps[0]
        self.assertEqual(exp.citation_score, 0.0)
        for reason in exp.reasons:
            self.assertNotIn("被引", reason)
            self.assertNotIn("引用", reason)

    def test_zero_citation_no_reason(self):
        query = QueryRequest(research_direction="太赫兹")
        _ranked, exps = RankingService.rank_with_explanation(
            _fresh([dict(title="太赫兹方法", year="2024", citation_count=0)]),
            query,
        )
        self.assertEqual(exps[0].citation_score, 0.0)
        self.assertNotIn("被引0次", exps[0].reasons)


# ============================================================
# 5. 三种 RankingMode 都能生成解释
# ============================================================

class TestAllModesGenerateExplanations(unittest.TestCase):
    def test_three_modes(self):
        for mode in RankingMode:
            with self.subTest(mode=mode.value):
                query = QueryRequest(
                    research_direction="太赫兹", ranking_mode=mode.value,
                )
                ranked, exps = RankingService.rank_with_explanation(_fresh(), query)
                self.assertEqual(len(ranked), 3)
                self.assertEqual(len(exps), 3)
                self.assertEqual([r.rank for r in ranked], [1, 2, 3])
                for e in exps:
                    self.assertIsInstance(e.total_score, float)
                    self.assertIsInstance(e.reasons, list)

    def test_explanations_follow_rank_order(self):
        """解释列表与排序后的结果列表按位置一一对应。"""
        query = QueryRequest(
            research_direction="太赫兹", ranking_mode=RankingMode.CITATION.value,
        )
        ranked, exps = RankingService.rank_with_explanation(_fresh(), query)
        # 第一名是高引用论文，其解释应含引用原因
        self.assertEqual(ranked[0].citation_count, 150)
        self.assertIn("引用量较高（被引150次）", exps[0].reasons)


# ============================================================
# 6. 旧调用方式不受影响
# ============================================================

class TestOldCallCompat(unittest.TestCase):
    def test_rank_returns_list_not_tuple(self):
        query = QueryRequest(research_direction="太赫兹")
        out = RankingService.rank(_fresh(), query)
        self.assertIsInstance(out, list)
        self.assertEqual(len(out), 3)

    def test_rank_behavior_unchanged(self):
        """旧断言：标题匹配优先于年份。"""
        query = QueryRequest(research_direction="太赫兹")
        matched = _result(title="太赫兹成像方法", year="2015")
        unmatched_new = _result(title="量子计算方法", year="2026")
        ranked = RankingService.rank([unmatched_new, matched], query)
        self.assertIs(ranked[0], matched)

    def test_old_query_like_object(self):
        """缺 ranking_mode/year_range 属性的旧 Query 对象可走解释路径。"""
        old_query = SimpleNamespace(research_direction="太赫兹")
        ranked, exps = RankingService.rank_with_explanation(_fresh(), old_query)
        self.assertEqual(len(ranked), 3)
        self.assertEqual(len(exps), 3)

    def test_year_filter_applies_to_explanation_path(self):
        query = QueryRequest(research_direction="太赫兹", year_range=5)
        spec = [
            dict(title="太赫兹A", year=str(_CURRENT_YEAR), database="CNKI"),
            dict(title="太赫兹B", year="2001", database="CNKI"),
        ]
        ranked, exps = RankingService.rank_with_explanation(_fresh(spec), query)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(len(exps), 1)
        self.assertEqual(ranked[0].title, "太赫兹A")

    def test_ranks_renumbered_in_explanation_path(self):
        query = QueryRequest(research_direction="太赫兹")
        items = [_result(rank=10, title="太赫兹A"), _result(rank=5, title="太赫兹B")]
        ranked, _exps = RankingService.rank_with_explanation(items, query)
        self.assertEqual([r.rank for r in ranked], [1, 2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
