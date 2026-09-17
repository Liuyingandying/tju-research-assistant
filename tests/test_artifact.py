#!/usr/bin/env python3
"""v0.17 Phase 2.1：多类型成果兼容层测试。

覆盖：
- artifact 构造辅助 dataclass roundtrip（PatentMetadata / NewsMetadata）；
- SearchResult.artifact_type / artifact_metadata roundtrip；
- 旧 SearchResult（无 artifact 字段）反序列化等价 paper；
- 类型感知 dedup_key / merger 跨类型同名不去重、同类型同名仍去重；
- 旧论文去重行为兼容（legacy dict 融合）；
- demo 新增专利/新闻后，论文排序与去重结果不变化（回归护栏）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.demo_data import DEMO_RESULTS, load_demo_results
from tju_info_retrieval.models.artifact import (
    ARTIFACT_LABELS,
    ARTIFACT_NEWS,
    ARTIFACT_PAPER,
    ARTIFACT_PATENT,
    NewsMetadata,
    PatentMetadata,
)
from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.result_merger import ResultMerger
from tju_info_retrieval.services.ranking import RankingService


def _paper(title="Paper A", **kw):
    defaults = dict(rank=1, title=title, authors=["张三"], year="2024")
    defaults.update(kw)
    return SearchResult(**defaults)


class TestArtifactMetadataDataclasses(unittest.TestCase):
    """构造/序列化辅助（artifact_metadata 本身是自由 dict，不绑定模型）。"""

    def test_patent_roundtrip(self):
        meta = PatentMetadata(
            inventors=["陈远", "刘明"],
            applicant="天津大学",
            publication_number="CN202610012345A",
            application_number="CN202610056789.0",
            date="2026-05-12",
        )
        restored = PatentMetadata.from_dict(meta.to_dict())
        self.assertEqual(restored, meta)

    def test_patent_none_metadata_returns_none(self):
        self.assertIsNone(PatentMetadata.from_dict(None))
        self.assertIsNone(PatentMetadata.from_dict({}))

    def test_news_roundtrip(self):
        meta = NewsMetadata(
            media="科技日报（演示）",
            publish_time="2026-08-20",
            authors=["周晓"],
            related_person=["王圣麟"],
        )
        restored = NewsMetadata.from_dict(meta.to_dict())
        self.assertEqual(restored, meta)
        # 正式字段名为 authors（审查意见：禁止依赖 byline）
        self.assertNotIn("byline", meta.to_dict())
        self.assertIn("authors", meta.to_dict())

    def test_constants_and_labels(self):
        self.assertEqual(ARTIFACT_PAPER, "paper")
        self.assertEqual(ARTIFACT_PATENT, "patent")
        self.assertEqual(ARTIFACT_NEWS, "news")
        self.assertEqual(ARTIFACT_LABELS[ARTIFACT_PAPER], "论文")
        self.assertEqual(ARTIFACT_LABELS[ARTIFACT_PATENT], "专利")
        self.assertEqual(ARTIFACT_LABELS[ARTIFACT_NEWS], "新闻")


class TestSearchResultArtifact(unittest.TestCase):
    def test_defaults_are_paper_without_metadata(self):
        r = _paper()
        self.assertEqual(r.artifact_type, ARTIFACT_PAPER)
        self.assertIsNone(r.artifact_metadata)

    def test_roundtrip_preserves_artifact_fields(self):
        meta = {"inventors": ["陈远"], "publication_number": "CN2026A"}
        r = _paper(artifact_type=ARTIFACT_PATENT, artifact_metadata=meta)
        restored = SearchResult.from_dict(r.to_dict())
        self.assertEqual(restored.artifact_type, ARTIFACT_PATENT)
        self.assertEqual(restored.artifact_metadata, meta)
        self.assertEqual(restored.title, r.title)

    def test_legacy_dict_deserializes_as_paper(self):
        """旧数据（无 artifact_type 字段）→ 等价 paper。"""
        legacy = {
            "rank": 1,
            "title": "旧论文",
            "authors": ["张三"],
            "source": "期刊A",
            "year": "2020",
            "document_type": "期刊论文",
            "database": "CNKI",
            "detail_url": None,
            "abstract": None,
            "doi": None,
            "keywords": [],
            "venue": None,
            "authors_raw": None,
        }
        r = SearchResult.from_dict(legacy)
        self.assertEqual(r.artifact_type, ARTIFACT_PAPER)
        self.assertIsNone(r.artifact_metadata)
        self.assertEqual(r.title, "旧论文")

    def test_dedup_key_is_type_aware(self):
        paper = _paper(title="同名标题")
        patent = _paper(title="同名标题", artifact_type=ARTIFACT_PATENT)
        news = _paper(title="同名标题", artifact_type=ARTIFACT_NEWS)
        self.assertNotEqual(paper.dedup_title_key(), patent.dedup_title_key())
        self.assertNotEqual(paper.dedup_title_key(), news.dedup_title_key())
        self.assertEqual(patent.dedup_title_key(), patent.dedup_title_key())
        # 同类型同名（含归一化）→ 键相同
        same = _paper(title="同名  标题", database="万方")
        self.assertEqual(paper.dedup_title_key(), same.dedup_title_key())


class TestMergerArtifactAware(unittest.TestCase):
    def test_cross_type_same_title_not_deduped(self):
        paper = _paper(title="太赫兹成像方法研究", database="CNKI")
        news = _paper(title="太赫兹成像方法研究", database="演示新闻",
                      artifact_type=ARTIFACT_NEWS)
        merged = ResultMerger.merge([paper, news])
        self.assertEqual(len(merged), 2, "跨类型同名标题不得互相吞并")

    def test_same_type_same_title_still_deduped(self):
        a = _paper(title="太赫兹成像方法研究", database="CNKI")
        b = _paper(title="太赫兹成像方法研究", database="万方")
        merged = ResultMerger.merge([a, b])
        self.assertEqual(len(merged), 1)

    def test_legacy_paper_dedup_compat(self):
        """旧 dict（无 artifact 字段）融合：论文/论文仍按标题去重。"""
        legacy_a = {
            "rank": 1, "title": "Terahertz Tomography", "authors": ["A"],
            "database": "CNKI", "year": "2024",
        }
        legacy_b = {
            "rank": 1, "title": "Terahertz  Tomography", "authors": ["A"],
            "database": "万方", "year": "2024",
        }
        merged = ResultMerger.merge([
            SearchResult.from_dict(legacy_a),
            SearchResult.from_dict(legacy_b),
        ])
        self.assertEqual(len(merged), 1)


class TestDemoDataMultiType(unittest.TestCase):
    def test_demo_contains_three_artifact_types(self):
        demo = load_demo_results()
        types = {SearchResult.from_dict(r).artifact_type for r in demo["results"]}
        self.assertEqual(types, {ARTIFACT_PAPER, ARTIFACT_PATENT, ARTIFACT_NEWS})

    def test_paper_ordering_unchanged_by_typed_entries(self):
        """新增专利/新闻不改变论文的去重结果与排序顺序（回归护栏）。"""
        papers_only = [r for r in DEMO_RESULTS if r.get("artifact_type") in (None, "paper")]
        all_items = DEMO_RESULTS
        base = RankingService.rank(
            ResultMerger.merge([SearchResult.from_dict(r) for r in papers_only]),
            QueryRequest(research_direction="太赫兹"),
        )
        full = RankingService.rank(
            ResultMerger.merge([SearchResult.from_dict(r) for r in all_items]),
            QueryRequest(research_direction="太赫兹"),
        )
        papers_in_full = [r for r in full if r.artifact_type == ARTIFACT_PAPER]
        self.assertEqual(
            [(r.title, r.database) for r in papers_in_full],
            [(r.title, r.database) for r in base],
            "专利/新闻条目不得改变论文去重点与相对顺序",
        )
        # 三种类型在最终结果中并存
        self.assertEqual(
            {r.artifact_type for r in full}, {ARTIFACT_PAPER, ARTIFACT_PATENT, ARTIFACT_NEWS}
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)