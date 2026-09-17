#!/usr/bin/env python3
"""离线结构化整理引擎测试（v0.17 Phase 2.4-B-OFFLINE）。

覆盖：分句/子句切分、Evidence 收集（news/patent/paper insufficient）、
四字段抽取、no-hallucination（输出 ⊆ evidence）、同句不占满四字段、
cache、空证据、artifact 词类加成。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.models.summary import StructuredSummary
from tju_info_retrieval.services.offline_summary import (
    INSUFFICIENT_TEXT,
    OfflineSummaryEngine,
    split_into_clauses,
    summarize_offline,
)

# 真实新闻样本（Phase 2.3-B 真机验收首条摘要）
NEWS_ABSTRACT = (
    "近日，天津大学研究团队开发了一种新型太赫兹光声系统，该系统克服水干扰，"
    "无须抽血或标记，实现了对活体小鼠钠水平的实时测量，并通过人体实验初步"
    "验证了走向临床应用的潜力与可行性。"
)
# 真实专利样本（Phase 2.2-C og:description 摘要节选）
PATENT_ABSTRACT = (
    "蓝山咖啡豆因种植环境苛刻、产量稀少而具有较高市场价值，但其外观与普通"
    "阿拉比卡咖啡豆较为相似，市场中存在以阿拉比卡咖啡豆掺假蓝山咖啡豆的现象。"
    "为实现咖啡豆掺假比例的快速定量检测，采用太赫兹时域光谱（THz-TDS）技术"
    "结合特征筛选与机器学习方法，对阿拉比卡与蓝山咖啡豆二组分混合体系进行"
    "定量分析。首先制备6种比例梯度的咖啡豆粉末压片样品并采集太赫兹光谱数据，"
    "在提取吸收系数光谱的基础上构建多种定量模型进行比较。结果表明，线性模型"
    "在描述咖啡豆组分比例与太赫兹光谱之间关系时存在一定局限，而基于树集成的"
    "非线性模型随机森林（RF）、梯度提升决策树（GBDT）能够更好地刻画其复杂"
    "映射关系。"
)


def _news(abstract=NEWS_ABSTRACT, content_head=None, title="健康报：太赫兹和声波结合使无针血钠检测成为可能"):
    r = SearchResult(rank=1, title=title, database="天津大学新闻网",
                     artifact_type="news", abstract=abstract)
    if content_head:
        r._news_content_head = content_head
    return r


def _patent(abstract=PATENT_ABSTRACT, title="一种基于太赫兹时域光谱的咖啡豆二组分混合物定量检测方法"):
    return SearchResult(rank=1, title=title, database="万方专利",
                        artifact_type="patent", abstract=abstract)


def _paper(title="一种太赫兹检测方法", abstract=None):
    return SearchResult(rank=1, title=title, database="CNKI",
                        artifact_type="paper", abstract=abstract)


class TestSentenceSplitting:
    def test_sentence_split_chinese_and_english(self):
        clauses = split_into_clauses([("summary", "第一句。第二句! Third. 第四句；")])
        texts = [c for _, c in clauses]
        assert any("第一句" in t for t in texts)
        assert any("Second" in t or "Third" in t for t in texts)

    def test_long_sentence_split_into_clauses(self):
        long = "，".join([
            "前半部分之一内容样本", "前半部分之二内容样本",
            "前半部分之三内容样本", "前半部分之四内容样本",
            "前半部分之五内容样本"]) + "。"
        clauses = split_into_clauses([("summary", long)])
        assert len(clauses) > 1, "长句应按逗号切子句"

    def test_dedupe_and_min_length(self):
        clauses = split_into_clauses([("summary", "相同的句子。相同的句子。短。")])
        texts = [c for _, c in clauses]
        assert texts.count("相同的句子") == 1
        assert all(len(c) >= 3 for c in texts)


class TestEvidenceCollection:
    def test_news_uses_summary_and_detail_content(self):
        r = _news(content_head="详情正文片段：田震教授团队……")
        blocks = None
        from tju_info_retrieval.services import offline_summary as m
        blocks = m._collect_evidence(r)
        labels = [l for l, _ in blocks]
        assert "summary" in labels and "detail_content" in labels

    def test_patent_uses_abstract(self):
        from tju_info_retrieval.services import offline_summary as m
        blocks = m._collect_evidence(_patent())
        assert [l for l, _ in blocks] == ["abstract"]

    def test_paper_no_abstract_insufficient_evidence(self):
        from tju_info_retrieval.services import offline_summary as m
        assert m._collect_evidence(_paper()) == []


class TestOfflineExtraction(unittest.TestCase):
    def setUp(self):
        self.engine = OfflineSummaryEngine(cache={})

    def test_news_four_fields_from_real_sample(self):
        s = self.engine.summarize(_news())
        assert INSUFFICIENT_TEXT not in s.research_content
        assert "太赫兹光声系统" in s.research_content
        assert "系统" in s.core_technology or "技术" in s.core_technology
        assert "实时测量" in s.main_results or "实现" in s.main_results
        assert "临床" in s.application_value or "应用" in s.application_value

    def test_patent_four_fields_from_real_sample(self):
        s = self.engine.summarize(_patent())
        assert "定量检测" in s.research_content or "咖啡豆" in s.research_content
        assert s.core_technology != INSUFFICIENT_TEXT
        assert s.main_results != INSUFFICIENT_TEXT

    def test_no_hallucination_output_subset_of_evidence(self):
        """输出字段文本必须 ⊆ 证据文本（或为固定“信息不足”文案）。"""
        r = _news()
        s = self.engine.summarize(r)
        evidence = NEWS_ABSTRACT + (getattr(r, "_news_content_head", "") or "")
        for value in (s.research_content, s.core_technology,
                      s.main_results, s.application_value):
            assert value == INSUFFICIENT_TEXT or value in evidence

    def test_same_clause_not_reused_across_fields(self):
        s = self.engine.summarize(_news())
        values = [s.research_content, s.core_technology,
                  s.main_results, s.application_value]
        non_insufficient = [v for v in values if v != INSUFFICIENT_TEXT]
        assert len(non_insufficient) == len(set(non_insufficient)), \
            "同一子句不得无意义占满多个字段"

    def test_paper_title_only_all_insufficient(self):
        """仅标题（无摘要）→ 不得凭标题推导技术结论，全字段信息不足。"""
        s = self.engine.summarize(_paper())
        for value in (s.research_content, s.core_technology,
                      s.main_results, s.application_value):
            assert value == INSUFFICIENT_TEXT

    def test_paper_insufficient_source_basis_none(self):
        s = self.engine.summarize(_paper())
        assert set(s.source_basis.values()) == {"none"}

    def test_news_partially_insufficient_allowed(self):
        """证据只覆盖部分字段时，其余字段诚实“信息不足”（如 Fellow 新闻）。"""
        r = _news(abstract="田震教授凭借在太赫兹光子学等方面的研究入选 Optica Fellow。",
                  title="天津大学精仪学院田震教授当选美国光学学会会士")
        s = self.engine.summarize(r)
        assert s.research_content != INSUFFICIENT_TEXT or \
            s.main_results != INSUFFICIENT_TEXT
        # 该新闻未描述应用价值 → 允许不足
        assert s.application_value == INSUFFICIENT_TEXT

    def test_empty_evidence_all_insufficient(self):
        r = _news(abstract=None, title="无证据标题")
        s = self.engine.summarize(r)
        for value in (s.research_content, s.core_technology,
                      s.main_results, s.application_value):
            assert value == INSUFFICIENT_TEXT


class TestCache:
    def test_repeated_call_hits_cache(self):
        cache = {}
        engine = OfflineSummaryEngine(cache=cache)
        r = _news()
        s1 = engine.summarize(r)
        # 修改原对象不影响第二次结果（缓存返回）
        r.abstract = "被篡改的摘要"
        s2 = engine.summarize(r)
        assert s1 is s2
        assert len(cache) == 1

    def test_different_results_different_entries(self):
        cache = {}
        engine = OfflineSummaryEngine(cache=cache)
        engine.summarize(_news(title="A", abstract="第一种成果的摘要内容。"))
        engine.summarize(_news(title="B", abstract="第二种成果的摘要内容。"))
        assert len(cache) == 2

    def test_module_level_entry_uses_global_cache(self):
        r = _news()
        s1 = summarize_offline(r)
        s2 = summarize_offline(r)
        assert s1 is s2


if __name__ == "__main__":
    unittest.main(verbosity=2)