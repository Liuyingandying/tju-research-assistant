#!/usr/bin/env python3
"""正式测试：演示数据模式（三来源示例 / 跨来源重复 / 加载器 / JSON 工件）。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest

from tju_info_retrieval.demo_data import (
    DEMO_QUERY_INFO,
    DEMO_RESULTS,
    _default_json_path,
    load_demo_results,
)
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.result_merger import ResultMerger
from tju_info_retrieval.services.ranking import RankingService
from tju_info_retrieval.models.query import QueryRequest


class TestDemoData:
    # v0.17 Phase 2.1：演示数据新增 专利/新闻 两类，来源集合随之扩展
    DEMO_SOURCES = {"CNKI", "万方", "IEEE Xplore", "万方专利", "演示新闻"}

    def test_contains_three_sources(self):
        dbs = {r["database"] for r in DEMO_RESULTS}
        assert dbs == self.DEMO_SOURCES

    def test_contains_cross_source_duplicate(self):
        titles = [r["title"] for r in DEMO_RESULTS]
        assert len(titles) != len(set(titles))  # 存在重复标题供去重演示

    def test_all_items_have_required_fields(self):
        for r in DEMO_RESULTS:
            assert r["title"]
            assert r["database"] in self.DEMO_SOURCES
            assert "authors" in r and "year" in r

    def test_load_demo_results_shape(self):
        data = load_demo_results()
        assert "query_info" in data and "results" in data
        assert len(data["results"]) == len(DEMO_RESULTS)
        assert data["query_info"]["sources"] == ["CNKI", "万方", "IEEE Xplore"]

    def test_runtime_json_artifact_exists(self):
        """仓库自带的演示数据工件存在且与内置数据一致。

        v0.18 Phase 2.9-B.1：全局测试隔离会把 `_default_json_path()` 指向临时
        目录，因此这里显式校验**仓库内**的 dev 态工件（只读，不写用户数据）。
        """
        from tju_info_retrieval import app_paths

        artifact = (app_paths.project_root() / "runtime"
                    / "demo_results.json")
        assert artifact.exists(), f"演示数据工件缺失: {artifact}"
        data = json.loads(artifact.read_text(encoding="utf-8"))
        assert len(data["results"]) == len(DEMO_RESULTS)

    def test_demo_merges_and_ranks(self):
        """演示数据应可完成去重（8 → 7：跨来源重复去重，专利/新闻保留）与排序。"""
        merged = ResultMerger.merge([SearchResult.from_dict(r) for r in DEMO_RESULTS])
        assert len(merged) == 7  # 跨来源重复被去除；专利/新闻与论文跨类型不互吞
        ranked = RankingService.rank(merged, QueryRequest(research_direction="太赫兹"))
        assert [r.rank for r in ranked] == [1, 2, 3, 4, 5, 6, 7]
        dbs = [r.database for r in ranked]
        assert set(dbs) == self.DEMO_SOURCES