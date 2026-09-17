#!/usr/bin/env python3
"""正式测试：SearchResult / QueryRequest。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult, normalize_url


class TestSearchResult(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        r = SearchResult(
            rank=1,
            title="标题A",
            authors=["张三", "李四"],
            source="期刊A",
            year="2026",
            detail_url="https://kns.cnki.net/a1",
            document_type="期刊",
            database="CNKI",
            abstract="摘要内容",
            doi="10.1234/test",
            keywords=["关键词1", "关键词2"],
            venue="期刊A",
            authors_raw="张三；李四",
        )
        d = r.to_dict()
        self.assertEqual(d["title"], "标题A")
        self.assertEqual(d["abstract"], "摘要内容")
        self.assertEqual(d["keywords"], ["关键词1", "关键词2"])
        restored = SearchResult.from_dict(d)
        self.assertEqual(restored, r)

    def test_missing_fields_defaults(self):
        r = SearchResult(rank=1, title="T")
        d = r.to_dict()
        self.assertEqual(d["authors"], [])
        self.assertIsNone(d["source"])
        self.assertIsNone(d["year"])
        self.assertIsNone(d["detail_url"])
        self.assertIsNone(d["document_type"])
        # v0.4 扩展字段默认安全
        self.assertIsNone(d["abstract"])
        self.assertIsNone(d["doi"])
        self.assertEqual(d["keywords"], [])
        self.assertIsNone(d["venue"])
        self.assertIsNone(d["authors_raw"])

    def test_from_dict_missing_fields(self):
        r = SearchResult.from_dict({"rank": 1, "title": "T"})
        self.assertEqual(r.authors, [])
        self.assertIsNone(r.source)
        self.assertEqual(r.database, "CNKI")
        # 旧 dict 兼容（无新字段）
        self.assertIsNone(r.abstract)
        self.assertIsNone(r.doi)
        self.assertEqual(r.keywords, [])
        self.assertIsNone(r.venue)
        self.assertIsNone(r.authors_raw)

    def test_from_dict_old_dict_compatible(self):
        # 模拟旧版本数据（无新字段）
        old = {"rank": 1, "title": "T", "authors": [], "source": "S", "year": "2026",
               "detail_url": "https://x", "document_type": "期刊", "database": "CNKI"}
        r = SearchResult.from_dict(old)
        self.assertEqual(r.title, "T")
        self.assertIsNone(r.abstract)
        self.assertEqual(r.keywords, [])
        # v0.17 Phase 2.1：旧数据无 artifact 字段 → 等价 paper，无 typed 元数据
        self.assertEqual(r.artifact_type, "paper")
        self.assertIsNone(r.artifact_metadata)

    def test_to_dict_roundtrip_artifact_fields(self):
        """v0.17 Phase 2.1：多类型字段可往返（patent 示例）。"""
        r = SearchResult(
            rank=1,
            title="一种太赫兹装置",
            artifact_type="patent",
            artifact_metadata={
                "inventors": ["陈远"],
                "publication_number": "CN2026A",
            },
        )
        restored = SearchResult.from_dict(r.to_dict())
        self.assertEqual(restored.artifact_type, "patent")
        self.assertEqual(restored.artifact_metadata["inventors"], ["陈远"])
        self.assertEqual(restored.artifact_metadata["publication_number"], "CN2026A")

    def test_normalize_url(self):
        self.assertEqual(normalize_url("  https://x.com  "), "https://x.com")
        self.assertEqual(normalize_url("//x.com/a"), "https://x.com/a")
        self.assertIsNone(normalize_url(""))
        self.assertIsNone(normalize_url(None))


class TestQueryRequest(unittest.TestCase):
    def test_default_affiliation_filter_mode_is_soft(self):
        """v0.13：默认单位筛选模式必须为 soft（智能匹配），防止误删。"""
        req = QueryRequest(research_direction="太赫兹")
        self.assertEqual(req.affiliation_filter_mode, "soft")

    def test_valid(self):
        q = QueryRequest(research_direction="太赫兹", result_count=10)
        q.validate()

    def test_empty_direction_raises(self):
        q = QueryRequest(research_direction="  ")
        with self.assertRaises(ValueError):
            q.validate()

    def test_count_not_allowed_raises(self):
        q = QueryRequest(research_direction="太赫兹", result_count=201)
        with self.assertRaises(ValueError):
            q.validate()

    def test_candidate_count_not_allowed_raises(self):
        q = QueryRequest(research_direction="太赫兹", result_count=10, candidate_count=201)
        with self.assertRaises(ValueError):
            q.validate()

    def test_candidate_count_less_than_result_raises(self):
        q = QueryRequest(research_direction="太赫兹", result_count=50, candidate_count=20)
        with self.assertRaises(ValueError):
            q.validate()

    def test_candidate_count_default_to_result_count(self):
        q = QueryRequest(research_direction="太赫兹", result_count=20)
        q.validate()
        self.assertEqual(q.effective_candidate_count, 20)

    def test_candidate_count_explicit(self):
        q = QueryRequest(research_direction="太赫兹", result_count=20, candidate_count=100)
        q.validate()
        self.assertEqual(q.effective_candidate_count, 100)

    def test_no_database_raises(self):
        q = QueryRequest(research_direction="太赫兹", sources=[])
        with self.assertRaises(ValueError):
            q.validate()

    def test_default_sources_is_cnki(self):
        q = QueryRequest(research_direction="太赫兹")
        self.assertEqual(q.sources, ["CNKI"])


class TestAuthorOrDirectionValidation(unittest.TestCase):
    """v0.17 Phase 1：研究方向 OR 人员姓名（至少一项）。

    - 两者均空 → 校验失败；
    - 仅人员单位（author_affiliation）不能作为主检索条件；
    - 纯人员姓名（research_direction=""）为合法作者检索请求。
    """

    def test_topic_only_valid(self):
        QueryRequest(research_direction="太赫兹").validate()

    def test_person_only_valid(self):
        q = QueryRequest(research_direction="", author_name="张三")
        q.validate()

    def test_topic_and_person_valid(self):
        QueryRequest(research_direction="太赫兹", author_name="张三").validate()

    def test_both_empty_raises_with_message(self):
        q = QueryRequest(research_direction="  ", author_name="")
        with self.assertRaises(ValueError) as ctx:
            q.validate()
        self.assertEqual(str(ctx.exception), "请输入研究方向或人员姓名，至少填写一项")

    def test_affiliation_only_raises(self):
        """人员单位不能替代研究方向/人员姓名。"""
        q = QueryRequest(
            research_direction="", author_name="", author_affiliation="天津大学",
        )
        with self.assertRaises(ValueError):
            q.validate()

    def test_person_with_affiliation_valid(self):
        QueryRequest(
            research_direction="", author_name="张三", author_affiliation="天津大学",
        ).validate()


if __name__ == "__main__":
    unittest.main(verbosity=2)
