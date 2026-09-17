#!/usr/bin/env python3
"""正式测试：RankingService（标题匹配 / 年份 / 数据库权重 / 字段保留）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.ranking import RankingService


def _result(**kw):
    data = dict(rank=99, title="T", authors=["A"], source="S", year="2020", database="CNKI")
    data.update(kw)
    return SearchResult(**data)


class TestRankingService:
    def test_title_match_ranks_first(self):
        query = QueryRequest(research_direction="太赫兹")
        matched = _result(title="太赫兹成像方法", year="2015")
        unmatched = _result(title="量子计算方法", year="2026")
        ranked = RankingService.rank([unmatched, matched], query)
        assert ranked[0] is matched  # 标题匹配优先于年份

    def test_newer_year_ranks_first(self):
        query = QueryRequest(research_direction="太赫兹")
        old = _result(title="太赫兹方法A", year="2015")
        new = _result(title="太赫兹方法B", year="2026")
        ranked = RankingService.rank([old, new], query)
        assert ranked[0] is new

    def test_empty_results(self):
        query = QueryRequest(research_direction="太赫兹")
        assert RankingService.rank([], query) == []

    def test_fields_preserved(self):
        query = QueryRequest(research_direction="太赫兹")
        r = _result(
            title="太赫兹论文", authors=["张三"], year="2024", database="万方",
            doi="10.1234/x", abstract="摘要", keywords=["太赫兹"], venue="期刊A",
        )
        ranked = RankingService.rank([r], query)
        out = ranked[0]
        assert out.title == "太赫兹论文"
        assert out.authors == ["张三"]
        assert out.year == "2024"
        assert out.database == "万方"
        assert out.doi == "10.1234/x"
        assert out.abstract == "摘要"
        assert out.keywords == ["太赫兹"]
        assert out.venue == "期刊A"
        assert out.rank == 1

    def test_database_weight_is_light_tiebreaker(self):
        query = QueryRequest(research_direction="太赫兹")
        ieee = _result(title="太赫兹论文", year="2024", database="IEEE Xplore")
        cnki = _result(title="太赫兹论文B", year="2024", database="CNKI")
        wanfang = _result(title="太赫兹论文C", year="2024", database="万方")
        ranked = RankingService.rank([wanfang, cnki, ieee], query)
        assert [r.database for r in ranked] == ["IEEE Xplore", "CNKI", "万方"]

    def test_rank_regenerated(self):
        query = QueryRequest(research_direction="x")
        items = [_result(rank=10, title="A"), _result(rank=5, title="B"), _result(rank=2, title="C")]
        ranked = RankingService.rank(items, query)
        assert [r.rank for r in ranked] == [1, 2, 3]

    def test_multiword_query_scores_each_term(self):
        query = QueryRequest(research_direction="terahertz imaging")
        both = _result(title="Terahertz Imaging Method", year="2020")
        one = _result(title="Terahertz Method", year="2026")
        ranked = RankingService.rank([one, both], query)
        assert ranked[0] is both
