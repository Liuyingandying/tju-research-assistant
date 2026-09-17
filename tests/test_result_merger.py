#!/usr/bin/env python3
"""正式测试：ResultMerger 多源结果融合（去重 + rank 重排）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest

from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.result_merger import ResultMerger


def _make(**kw):
    defaults = dict(rank=1, title="T", authors=["A"], year="2026")
    defaults.update(kw)
    return SearchResult(**defaults)


class TestResultMerger:
    def test_doi_dedup(self):
        r1 = _make(rank=1, title="A", doi="10.1234/a")
        r2 = _make(rank=1, title="A", doi="10.1234/a")  # 同DOI
        results = ResultMerger.merge([r1, r2])
        assert len(results) == 1
        assert results[0].title == "A"

    def test_url_dedup(self):
        r1 = _make(rank=1, title="A", detail_url="https://x.com/a")
        r2 = _make(rank=1, title="A", detail_url="https://x.com/a")  # 同URL
        results = ResultMerger.merge([r1, r2])
        assert len(results) == 1

    def test_title_dedup(self):
        r1 = _make(rank=1, title="Terahertz Tomography", database="CNKI")
        r2 = _make(rank=1, title="Terahertz  Tomography", database="万方")  # 多空格
        results = ResultMerger.merge([r1, r2])
        assert len(results) == 1
        assert results[0].database == "CNKI"  # 保留第一个

    def test_chinese_title_dedup(self):
        r1 = _make(rank=1, title="基于太赫兹的检测方法", database="CNKI")
        r2 = _make(rank=1, title="基于太赫兹  的  检测方法", database="万方")  # 多空格
        results = ResultMerger.merge([r1, r2])
        assert len(results) == 1

    def test_punctuation_insensitive_dedup(self):
        r1 = _make(rank=1, title="Terahertz: A New Frontier", database="CNKI")
        r2 = _make(rank=1, title="Terahertz; A New Frontier", database="IEEE Xplore")
        results = ResultMerger.merge([r1, r2])
        assert len(results) == 1

    def test_different_sources_both_kept(self):
        r1 = _make(rank=1, title="A", database="CNKI")
        r2 = _make(rank=1, title="B", database="万方")  # 不同标题
        results = ResultMerger.merge([r1, r2])
        assert len(results) == 2

    def test_rank_renumbered(self):
        r1 = _make(rank=5, title="A", database="CNKI")
        r2 = _make(rank=3, title="B", database="万方")
        results = ResultMerger.merge([r1, r2])
        assert results[0].rank == 1
        assert results[1].rank == 2

    def test_doi_takes_priority(self):
        # 同DOI但不同URL/标题，应只保留第一个
        r1 = _make(rank=1, title="A", doi="10.1234/a", detail_url="https://x.com/a")
        r2 = _make(rank=1, title="B", doi="10.1234/a", detail_url="https://y.com/b")
        results = ResultMerger.merge([r1, r2])
        assert len(results) == 1

    def test_url_takes_priority_over_title(self):
        # 同URL但不同标题（罕见），应只保留第一个
        r1 = _make(rank=1, title="A", detail_url="https://x.com/a")
        r2 = _make(rank=1, title="B", detail_url="https://x.com/a")
        results = ResultMerger.merge([r1, r2])
        assert len(results) == 1

    def test_empty_input(self):
        assert ResultMerger.merge([]) == []

    def test_normalize_title_edge_cases(self):
        assert ResultMerger._normalize_title("") == ""
        assert ResultMerger._normalize_title("  ") == ""
        assert ResultMerger._normalize_title("Hello World") == "helloworld"