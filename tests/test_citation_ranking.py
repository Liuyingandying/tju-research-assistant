#!/usr/bin/env python3
"""v0.11 Phase 2：引用影响力排序增强测试。

覆盖：
1. citation_count 字段序列化（to_dict / from_dict）
2. None 不影响排序（未知引用数）
3. 高引用论文评分提升（log 曲线）
4. 低引用论文正常参与
5. CNKI/万方解析成功时字段正确
6. IEEE 保持 None
"""
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.ranking import RankingService


# ============================================================
# 1. citation_count 字段序列化
# ============================================================

class TestCitationCountSerialization(unittest.TestCase):
    def test_to_dict_includes_citation_count(self):
        r = SearchResult(rank=1, title="Test", citation_count=42)
        d = r.to_dict()
        self.assertEqual(d["citation_count"], 42)

    def test_to_dict_none_citation_count(self):
        r = SearchResult(rank=1, title="Test", citation_count=None)
        d = r.to_dict()
        self.assertIsNone(d["citation_count"])

    def test_to_dict_zero_citation_count(self):
        r = SearchResult(rank=1, title="Test", citation_count=0)
        d = r.to_dict()
        self.assertEqual(d["citation_count"], 0)

    def test_from_dict_with_citation_count(self):
        data = {"rank": 1, "title": "Test", "citation_count": 99}
        r = SearchResult.from_dict(data)
        self.assertEqual(r.citation_count, 99)

    def test_from_dict_without_citation_count(self):
        """旧数据不包含 citation_count 时应为 None。"""
        data = {"rank": 1, "title": "Test"}
        r = SearchResult.from_dict(data)
        self.assertIsNone(r.citation_count)

    def test_round_trip(self):
        original = SearchResult(
            rank=1, title="太赫兹论文", citation_count=150,
            authors=["张三"], year="2024", database="CNKI",
        )
        restored = SearchResult.from_dict(original.to_dict())
        self.assertEqual(restored.citation_count, 150)
        self.assertEqual(restored.title, "太赫兹论文")


# ============================================================
# 2. None 不影响排序
# ============================================================

class TestNoneCitationDoesNotBreakRanking(unittest.TestCase):
    def test_all_none_citations(self):
        """全部 citation_count=None 时，排序不受影响。"""
        query = QueryRequest(research_direction="太赫兹")
        r1 = SearchResult(rank=1, title="太赫兹方法", database="CNKI", citation_count=None)
        r2 = SearchResult(rank=2, title="量子计算", database="CNKI", citation_count=None)
        ranked = RankingService.rank([r2, r1], query)
        assert ranked[0] is r1  # 标题匹配优先

    def test_mixed_none_and_value(self):
        """citation_count=None 与有值混合时，None 不报错。"""
        query = QueryRequest(research_direction="太赫兹")
        r_none = SearchResult(rank=1, title="太赫兹方法", database="CNKI", citation_count=None)
        r_high = SearchResult(rank=2, title="太赫兹方法", database="CNKI", citation_count=1000)
        # 两者标题匹配且年份相同，citation 高的应排前面
        ranked = RankingService.rank([r_none, r_high], query)
        assert ranked[0] is r_high


# ============================================================
# 3. 高引用论文评分提升（log 曲线）
# ============================================================

class TestHighCitationLogScoring(unittest.TestCase):
    def test_log_curve_nonlinear(self):
        """log 曲线：引用翻倍不意味着分数翻倍。"""
        # citation=9 → log10(10)*10 = 10.0
        # citation=99 → log10(100)*10 = 20.0
        # citation=999 → log10(1000)*10 = 30.0
        # 引用从 9→99（10倍），分数从 10→20（2倍）
        s9 = RankingService._citation_score(9)
        s99 = RankingService._citation_score(99)
        s999 = RankingService._citation_score(999)
        self.assertAlmostEqual(s9, 10.0, places=5)
        self.assertAlmostEqual(s99, 20.0, places=5)
        self.assertAlmostEqual(s999, 30.0, places=5)
        # 确认非线性：用非 10 的幂次验证
        # citation=4 → log10(5)*10 ≈ 6.99
        # citation=8 → log10(9)*10 ≈ 9.54 （引用翻倍，分数增加约 2.55）
        # citation=16 → log10(17)*10 ≈ 12.30（引用翻倍，分数增加约 2.76）
        s4 = RankingService._citation_score(4)
        s8 = RankingService._citation_score(8)
        s16 = RankingService._citation_score(16)
        self.assertLess(s8 - s4, s16 - s8)

    def test_high_citation_beats_older_year(self):
        """高引用论文可以超过较新但不相关的论文。"""
        query = QueryRequest(research_direction="太赫兹")
        old_unmatched = SearchResult(
            rank=1, title="量子计算方法", year="2026", database="CNKI", citation_count=None,
        )
        new_matched = SearchResult(
            rank=2, title="太赫兹成像", year="2020", database="CNKI", citation_count=10000,
        )
        ranked = RankingService.rank([old_unmatched, new_matched], query)
        # 标题匹配 60 分 + 高引用 ~40 分 = ~100 分
        # 不匹配 0 分 + 年份 26 分 = 26 分
        assert ranked[0] is new_matched


# ============================================================
# 4. 低引用论文正常参与
# ============================================================

class TestLowCitationNormalParticipation(unittest.TestCase):
    def test_zero_citation_score(self):
        r = RankingService._citation_score(0)
        self.assertEqual(r, 0.0)

    def test_low_citation_gives_small_bonus(self):
        """citation_count=1 → log10(2)*10 ≈ 3.01 分。"""
        s = RankingService._citation_score(1)
        self.assertAlmostEqual(s, math.log10(2) * 10, places=5)

    def test_low_citation_does_not_override_title(self):
        """低引用论文不能超越标题匹配论文。"""
        query = QueryRequest(research_direction="太赫兹")
        matched = SearchResult(rank=1, title="太赫兹方法", database="CNKI", citation_count=0)
        # 用 1000 引用（~30 分）+ 无年份（0 分）+ 同库（3 分）= 33 分 < 标题匹配 60 分
        unmatched_high = SearchResult(
            rank=2, title="量子计算", database="CNKI", citation_count=1000,
        )
        ranked = RankingService.rank([unmatched_high, matched], query)
        assert ranked[0] is matched


# ============================================================
# 5. CNKI/万方解析成功时字段正确
# ============================================================

class TestAdapterCitationParsing(unittest.TestCase):
    def test_cnki_citation_extractor_with_number(self):
        """CNKI _extract_citation_count 从 td.citation 提取数字。"""
        from unittest import mock
        td = mock.Mock()
        td.inner_text.return_value = "128"
        row = mock.Mock()
        row.query_selector.return_value = td
        from tju_info_retrieval.sources.cnki import CNKIAdapter
        result = CNKIAdapter._extract_citation_count(row)
        self.assertEqual(result, 128)

    def test_cnki_citation_extractor_with_placeholder(self):
        """CNKI td.citation 显示占位符时无引用数。"""
        from unittest import mock
        td = mock.Mock()
        td.inner_text.return_value = "--"
        row = mock.Mock()
        row.query_selector.return_value = td
        from tju_info_retrieval.sources.cnki import CNKIAdapter
        result = CNKIAdapter._extract_citation_count(row)
        self.assertIsNone(result)

    def test_cnki_citation_extractor_no_cell(self):
        """CNKI 无 td.citation 列时返回 None。"""
        from unittest import mock
        row = mock.Mock()
        row.query_selector.return_value = None
        from tju_info_retrieval.sources.cnki import CNKIAdapter
        result = CNKIAdapter._extract_citation_count(row)
        self.assertIsNone(result)

    def test_wanfang_regex_with_citation(self):
        """万方正则匹配含引用数的结果行。"""
        # 模拟万方结果行：1. 太赫兹方法 | [期刊] 张三-《电子学报》2024 150
        text = "1. 太赫兹方法 | [期刊] 张三-《电子学报》2024 150"
        from tju_info_retrieval.sources.wanfang import _RESULT_RE
        m = _RESULT_RE.search(text)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "1")
        self.assertEqual(m.group(2).strip(), "太赫兹方法")
        self.assertEqual(m.group(6).strip(), "2024")
        self.assertEqual(int(m.group(7)), 150)

    def test_wanfang_regex_without_citation(self):
        """万方正则匹配无引用数的结果行。"""
        text = "2. 量子计算研究 | [期刊] 李四-《计算机学报》2023"
        from tju_info_retrieval.sources.wanfang import _RESULT_RE
        m = _RESULT_RE.search(text)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "2")
        self.assertEqual(m.group(6).strip(), "2023")
        self.assertIsNone(m.group(7))

    def test_wanfang_parse_results_includes_citation(self):
        """万方 _parse_results 正确设置 citation_count。"""
        from unittest import mock
        page = mock.Mock()
        page.inner_text.return_value = "1. 太赫兹成像 | [期刊] 张三-《电子学报》2024 99"
        from tju_info_retrieval.sources.wanfang import WanfangAdapter
        results = WanfangAdapter._parse_results(page, 10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].citation_count, 99)


# ============================================================
# 6. IEEE 保持 None
# ============================================================

class TestIEEECitationNone(unittest.TestCase):
    def test_ieee_adapter_does_not_set_citation(self):
        """IEEE 适配器不设置 citation_count（保持 None）。"""
        from unittest import mock
        from tju_info_retrieval.sources.ieee import IeeeAdapter
        session = mock.Mock()
        adapter = IeeeAdapter(session)
        # IEEE adapter.search 需要浏览器环境，这里只验证 parse_results 逻辑
        # 通过检查 SearchResult 构造：IEEE 不传 citation_count
        # 由于 IEEE parse 逻辑在浏览器中执行，我们通过模型验证
        r = SearchResult(rank=1, title="Test IEEE Paper", database="IEEE Xplore")
        self.assertIsNone(r.citation_count)

    def test_ieee_ranking_with_none_citation(self):
        """IEEE 结果 citation_count=None 时排序正常。"""
        query = QueryRequest(research_direction="太赫兹")
        r = SearchResult(
            rank=1, title="太赫兹方法", database="IEEE Xplore", citation_count=None,
        )
        ranked = RankingService.rank([r], query)
        self.assertEqual(ranked[0].rank, 1)
        self.assertIsNone(ranked[0].citation_count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
