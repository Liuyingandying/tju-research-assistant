#!/usr/bin/env python3
"""v0.17 Phase 2.5-B：扩展方向 Spec 与 applicability resolver 测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.services.expansion_direction import (
    APPLICATION,
    GENERAL,
    EN_TERMS,
    REVIEW,
    THEORY,
    ZH_TERMS,
    direction_applies_to_source,
    en_terms,
    resolve_expansion_behavior,
    zh_terms,
)


class TestDirectionSpec(unittest.TestCase):
    def test_general_no_terms(self):
        assert zh_terms(GENERAL) == ()
        assert en_terms(GENERAL) == ()

    def test_review_zh_is_stable_short_group(self):
        """CNKI 综述必须是已验证短组，绝不含 研究现状/展望/回顾。"""
        assert zh_terms(REVIEW) == ("综述", "进展")
        assert not any(t in ("研究现状", "展望", "回顾") for t in zh_terms(REVIEW))

    def test_zh_terms_known(self):
        assert "应用" in zh_terms(APPLICATION)
        assert "模型" in zh_terms(THEORY)

    def test_en_terms_verified(self):
        assert set(en_terms(REVIEW)) == {"review", "survey", "overview"}
        assert "application" in en_terms(APPLICATION)
        assert "theoretical" in en_terms(THEORY)

    def test_no_domain_terms(self):
        """禁止领域词膨胀。"""
        allowed = set(ZH_TERMS[APPLICATION]) | set(ZH_TERMS[THEORY]) \
            | set(ZH_TERMS[REVIEW])
        assert not allowed & {"成像", "通信", "检测", "雷达", "生物医学"}

    def test_source_support(self):
        assert direction_applies_to_source("CNKI", REVIEW)
        assert direction_applies_to_source("IEEE Xplore", THEORY)
        assert not direction_applies_to_source("万方", REVIEW)
        assert not direction_applies_to_source("CNKI专利", REVIEW)
        assert not direction_applies_to_source("万方专利", REVIEW)
        assert not direction_applies_to_source("天津大学新闻网", THEORY)


class TestResolveExpansionBehavior(unittest.TestCase):
    def test_general_no_hint(self):
        applied, fallback, hint = resolve_expansion_behavior(
            ["paper"], ["CNKI"], GENERAL, has_topic=True)
        assert applied == [] and fallback == [] and hint is None

    def test_pure_person_no_expansion_no_hint(self):
        applied, fallback, hint = resolve_expansion_behavior(
            ["paper"], ["CNKI", "IEEE Xplore"], REVIEW, has_topic=False)
        assert applied == [] and fallback == [] and hint is None

    def test_paper_review_cnki_ieee_applied(self):
        applied, fallback, hint = resolve_expansion_behavior(
            ["paper"], ["CNKI", "IEEE Xplore"], REVIEW, has_topic=True)
        assert applied == ["CNKI", "IEEE Xplore"]
        assert fallback == []
        assert hint is None

    def test_paper_patent_review_one_hint(self):
        applied, fallback, hint = resolve_expansion_behavior(
            ["paper", "patent"], ["CNKI", "CNKI专利"], REVIEW, has_topic=True)
        assert applied == ["CNKI"]
        assert fallback == ["CNKI专利"]
        assert hint is not None and "专利结果将按综合主题模式检索" in hint

    def test_paper_news_theory_one_hint(self):
        applied, fallback, hint = resolve_expansion_behavior(
            ["paper", "news"], ["IEEE Xplore", "天津大学新闻网"], THEORY,
            has_topic=True)
        assert applied == ["IEEE Xplore"]
        assert fallback == ["天津大学新闻网"]
        assert hint is not None and "新闻结果将按综合主题模式检索" in hint

    def test_wanfang_paper_review_fallback_hint(self):
        applied, fallback, hint = resolve_expansion_behavior(
            ["paper"], ["万方"], REVIEW, has_topic=True)
        assert applied == []
        assert fallback == ["万方"]
        assert hint is not None and "万方论文" in hint

    def test_cnki_wanfang_review_partial(self):
        applied, fallback, hint = resolve_expansion_behavior(
            ["paper"], ["CNKI", "万方"], REVIEW, has_topic=True)
        assert applied == ["CNKI"]
        assert fallback == ["万方"]
        assert hint is not None and "万方论文" in hint

    def test_patent_only_review_no_block(self):
        applied, fallback, hint = resolve_expansion_behavior(
            ["patent"], ["CNKI专利"], REVIEW, has_topic=True)
        assert applied == []
        assert fallback == ["CNKI专利"]
        assert hint is not None

    def test_paper_patent_news_application_one_combined_hint(self):
        applied, fallback, hint = resolve_expansion_behavior(
            ["paper", "patent", "news"],
            ["CNKI", "CNKI专利", "天津大学新闻网"], APPLICATION, has_topic=True)
        assert applied == ["CNKI"]
        assert len(fallback) == 2
        assert hint.count("专利") == 1 and hint.count("新闻") == 1


if __name__ == "__main__":
    unittest.main(verbosity=2)