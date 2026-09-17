#!/usr/bin/env python3
"""v0.11 Phase 3：高级筛选 UI（排序模式 + 年份范围）测试。

覆盖：
1. 默认综合排序（RankingMode.COMPREHENSIVE）
2. 最新模式排序（RankingMode.RECENT）
3. 高引用排序（RankingMode.CITATION）
4. GUI 选择传递（combo_rank → QueryRequest）
5. 旧 Query 兼容（缺省字段 / 无属性对象）
6. 年份范围过滤（year_range）
"""
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.models.query import QueryRequest, RankingMode
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.ranking import RankingService

_CURRENT_YEAR = datetime.now().year


def _result(**kw):
    data = dict(rank=99, title="T", authors=["A"], source="S", year=None, database="CNKI")
    data.update(kw)
    return SearchResult(**data)


# ============================================================
# 1. 默认综合排序
# ============================================================

class TestDefaultComprehensiveMode(unittest.TestCase):
    def test_default_ranking_mode_value(self):
        q = QueryRequest(research_direction="太赫兹")
        self.assertEqual(q.ranking_mode, RankingMode.COMPREHENSIVE.value)

    def test_default_matches_legacy_order(self):
        """默认模式排序结果与 Phase 2 行为一致：标题匹配优先于年份。"""
        query = QueryRequest(research_direction="太赫兹")
        matched_old = _result(title="太赫兹成像方法", year="2015")
        unmatched_new = _result(title="量子计算方法", year=str(_CURRENT_YEAR))
        ranked = RankingService.rank([unmatched_new, matched_old], query)
        self.assertIs(ranked[0], matched_old)

    def test_explicit_comprehensive_same_as_default(self):
        query = QueryRequest(research_direction="太赫兹", ranking_mode="COMPREHENSIVE")
        a = _result(title="太赫兹方法A", year="2020")
        b = _result(title="量子计算B", year="2010")
        ranked = RankingService.rank([b, a], query)
        self.assertIs(ranked[0], a)


# ============================================================
# 2. 最新模式排序
# ============================================================

class TestRecentMode(unittest.TestCase):
    def test_recent_mode_ignores_title_match(self):
        """RECENT 模式：较新但不匹配的论文排在较旧但匹配的论文前面。"""
        query = QueryRequest(research_direction="太赫兹", ranking_mode=RankingMode.RECENT.value)
        matched_old = _result(title="太赫兹成像方法", year="2015")
        unmatched_new = _result(title="量子计算方法", year="2026")
        ranked = RankingService.rank([matched_old, unmatched_new], query)
        self.assertIs(ranked[0], unmatched_new)

    def test_recent_mode_year_tiebreak_by_score(self):
        """RECENT 模式：同年论文按综合分排序。"""
        query = QueryRequest(research_direction="太赫兹", ranking_mode=RankingMode.RECENT.value)
        matched = _result(title="太赫兹成像方法", year="2024")
        unmatched = _result(title="量子计算方法", year="2024")
        ranked = RankingService.rank([unmatched, matched], query)
        self.assertIs(ranked[0], matched)

    def test_recent_mode_missing_year_last(self):
        """RECENT 模式：缺少年份的结果排在最后。"""
        query = QueryRequest(research_direction="太赫兹", ranking_mode=RankingMode.RECENT.value)
        no_year = _result(title="太赫兹方法")
        old = _result(title="太赫兹方法B", year="2001")
        ranked = RankingService.rank([no_year, old], query)
        self.assertIs(ranked[0], old)
        self.assertIs(ranked[1], no_year)

    def test_recent_mode_ranks_renumbered(self):
        query = QueryRequest(research_direction="x", ranking_mode=RankingMode.RECENT.value)
        items = [_result(rank=10, title="A", year="2020"), _result(rank=5, title="B", year="2024")]
        ranked = RankingService.rank(items, query)
        self.assertEqual([r.rank for r in ranked], [1, 2])


# ============================================================
# 3. 高引用排序
# ============================================================

class TestCitationMode(unittest.TestCase):
    def test_citation_mode_ignores_year_and_match(self):
        """CITATION 模式：高引用论文优先于标题匹配/较新的论文。"""
        query = QueryRequest(
            research_direction="太赫兹", ranking_mode=RankingMode.CITATION.value,
        )
        matched_no_cite = _result(
            title="太赫兹成像方法", year="2024", citation_count=None,
        )
        no_match_high_cite = _result(
            title="量子计算方法", year="2010", citation_count=5000,
        )
        ranked = RankingService.rank([matched_no_cite, no_match_high_cite], query)
        self.assertIs(ranked[0], no_match_high_cite)

    def test_citation_mode_none_treated_as_zero(self):
        """CITATION 模式：citation_count=None 视为 0，排在有引用论文之后。"""
        query = QueryRequest(
            research_direction="太赫兹", ranking_mode=RankingMode.CITATION.value,
        )
        no_cite = _result(title="太赫兹方法", year="2024", citation_count=None)
        low_cite = _result(title="太赫兹方法B", year="2010", citation_count=1)
        ranked = RankingService.rank([no_cite, low_cite], query)
        self.assertIs(ranked[0], low_cite)

    def test_citation_mode_tiebreak_by_score(self):
        """CITATION 模式：引用相同（含 None==None）时按综合分排序。"""
        query = QueryRequest(
            research_direction="太赫兹", ranking_mode=RankingMode.CITATION.value,
        )
        matched = _result(title="太赫兹成像方法", year="2024", citation_count=None)
        unmatched = _result(title="量子计算方法", year="2010", citation_count=None)
        ranked = RankingService.rank([unmatched, matched], query)
        self.assertIs(ranked[0], matched)


# ============================================================
# 4. GUI 选择传递
# ============================================================

class TestGuiSelectionPassed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        self.window = MainWindow()
        self.captured = []

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def _search_and_capture(self):
        self.window.search_requested.connect(self.captured.append)
        self.window.input_direction.setText("太赫兹")
        self.window._on_search()

    def test_combos_exist_with_options(self):
        labels = [self.window.combo_rank.itemText(i) for i in range(self.window.combo_rank.count())]
        self.assertEqual(labels, ["综合推荐", "最新发表", "高引用"])

    def test_default_gui_selection(self):
        self._search_and_capture()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].ranking_mode, RankingMode.COMPREHENSIVE.value)
        self.assertEqual(self.captured[0].year_range, 0)

    def test_recent_mode_passed(self):
        self.window.combo_rank.setCurrentIndex(1)
        self._search_and_capture()
        self.assertEqual(self.captured[0].ranking_mode, RankingMode.RECENT.value)

    def test_citation_mode_passed(self):
        self.window.combo_rank.setCurrentIndex(2)
        self._search_and_capture()
        self.assertEqual(self.captured[0].ranking_mode, RankingMode.CITATION.value)


# ============================================================
# 5. 旧 Query 兼容
# ============================================================

class TestOldQueryCompat(unittest.TestCase):
    def test_old_style_kwargs_validate(self):
        """旧调用（无 ranking_mode/year_range 参数）构造并校验通过。"""
        q = QueryRequest(research_direction="太赫兹", result_count=20)
        q.validate()
        self.assertEqual(q.ranking_mode, "COMPREHENSIVE")
        self.assertEqual(q.year_range, 0)

    def test_query_like_object_without_new_attrs(self):
        """RankingService 对缺少新属性的 Query 对象使用默认值（getattr 兜底）。"""
        old_query = SimpleNamespace(research_direction="太赫兹")
        matched = _result(title="太赫兹成像方法", year="2015")
        unmatched_new = _result(title="量子计算方法", year="2026")
        ranked = RankingService.rank([unmatched_new, matched], old_query)
        self.assertIs(ranked[0], matched)

    def test_invalid_ranking_mode_raises(self):
        q = QueryRequest(research_direction="太赫兹", ranking_mode="POPULARITY")
        with self.assertRaises(ValueError):
            q.validate()

    def test_invalid_year_range_raises(self):
        q = QueryRequest(research_direction="太赫兹", year_range=3)
        with self.assertRaises(ValueError):
            q.validate()

    def test_ranking_mode_enum_values(self):
        self.assertEqual(RankingMode.COMPREHENSIVE.value, "COMPREHENSIVE")
        self.assertEqual(RankingMode.RECENT.value, "RECENT")
        self.assertEqual(RankingMode.CITATION.value, "CITATION")
        self.assertEqual(RankingMode.COMPREHENSIVE, "COMPREHENSIVE")  # str 枚举可直接比较


# ============================================================
# 6. 年份范围过滤
# ============================================================

class TestYearRangeFilter(unittest.TestCase):
    def test_no_filter_keeps_all(self):
        query = QueryRequest(research_direction="太赫兹", year_range=0)
        items = [
            _result(title="太赫兹A", year="2001"),
            _result(title="太赫兹B"),
        ]
        ranked = RankingService.rank(items, query)
        self.assertEqual(len(ranked), 2)

    def test_recent_5_years_filter(self):
        query = QueryRequest(research_direction="太赫兹", year_range=5)
        recent = _result(title="太赫兹A", year=str(_CURRENT_YEAR))
        old = _result(title="太赫兹B", year=str(_CURRENT_YEAR - 10))
        unknown = _result(title="太赫兹C")
        ranked = RankingService.rank([old, recent, unknown], query)
        self.assertEqual(len(ranked), 1)
        self.assertIs(ranked[0], recent)

    def test_recent_10_years_filter(self):
        query = QueryRequest(research_direction="太赫兹", year_range=10)
        within = _result(title="太赫兹A", year=str(_CURRENT_YEAR - 8))
        too_old = _result(title="太赫兹B", year=str(_CURRENT_YEAR - 15))
        ranked = RankingService.rank([too_old, within], query)
        self.assertEqual(len(ranked), 1)
        self.assertIs(ranked[0], within)

    def test_filter_boundary_year_kept(self):
        """恰好等于阈值年份的结果保留。"""
        query = QueryRequest(research_direction="太赫兹", year_range=5)
        boundary = _result(title="太赫兹A", year=str(_CURRENT_YEAR - 5))
        ranked = RankingService.rank([boundary], query)
        self.assertEqual(len(ranked), 1)

    def test_filter_keeps_newer_first_order(self):
        query = QueryRequest(research_direction="x", year_range=10)
        a = _result(title="A", year=str(_CURRENT_YEAR - 2))
        b = _result(title="B", year=str(_CURRENT_YEAR - 1))
        ranked = RankingService.rank([a, b], query)
        self.assertEqual([r.title for r in ranked], ["B", "A"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
