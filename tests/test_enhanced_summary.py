#!/usr/bin/env python3
"""正式测试：v0.18 Phase 2.7-A Enhanced Summary 框架。

覆盖：
1. EnhancedSummary roundtrip（to_dict/from_dict/items）；
2. OfflineProvider 回归（输出与 OfflineSummaryEngine 完全一致）；
3. MockEnhancedProvider 输出（provider=mock / ai_generated=True / 无 API）；
4. SummaryService 模式切换（basic / enhanced / 未知模式报错）；
5. 未配置降级（enhanced_configured=False → 固定提示，不崩溃）。
"""
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.models.enhanced_summary import (
    ENHANCED_FIELD_LABELS,
    ENHANCED_FIELDS,
    ENHANCED_NOT_CONFIGURED_TEXT,
    EnhancedSummary,
)
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.models.summary import StructuredSummary
from tju_info_retrieval.services.enhanced_provider import (
    MOCK_PROVIDER_NAME,
    MockEnhancedProvider,
    not_configured_summary,
)
from tju_info_retrieval.services.offline_provider import OfflineProvider
from tju_info_retrieval.services.summary_provider import (
    EvidenceBundle,
    SummaryContext,
)
from tju_info_retrieval.services.summary_service import (
    MODE_BASIC,
    MODE_ENHANCED,
    SummaryService,
)


def _paper_result(abstract: str = "") -> SearchResult:
    """构造带可选摘要的论文结果（不访问网络）。"""
    metadata = {"doi": "10.1000/example"}
    if abstract:
        metadata["abstract"] = abstract
    return SearchResult(
        rank=1,
        title="太赫兹时域光谱茶叶检测方法研究",
        authors=["张三"],
        source="CNKI",
        year=2024,
        detail_url="https://example.invalid/article",
        database="CNKI",
        artifact_type="paper",
        artifact_metadata=metadata,
        abstract=abstract,
    )


PAPER_ABSTRACT = (
    "本文针对茶叶掺假快速检测难题，提出基于太赫兹时域光谱（THz-TDS）"
    "结合机器学习分类的新方法。实验结果表明，该方法对三种茶叶样本的"
    "识别准确率达到 96%，可用于食品安全快速检测与工程应用。"
)


class TestEnhancedSummaryModel(unittest.TestCase):
    """1. EnhancedSummary roundtrip。"""

    def test_roundtrip_preserves_all_fields(self):
        src = EnhancedSummary(
            research_background="背景A",
            technical_route="路线B",
            innovation_points="创新C",
            relation_to_user_direction="关联D",
            reading_recommendation="建议E",
            limitations="局限F",
            provider="mock",
            ai_generated=True,
        )
        restored = EnhancedSummary.from_dict(src.to_dict())
        self.assertEqual(restored, src)

    def test_roundtrip_empty(self):
        restored = EnhancedSummary.from_dict({})
        self.assertEqual(restored, EnhancedSummary())

    def test_items_order_and_labels(self):
        s = EnhancedSummary(
            research_background="a", technical_route="b", innovation_points="c",
            relation_to_user_direction="d", reading_recommendation="e",
            limitations="f",
        )
        keys = [k for k, _label, _v in s.items()]
        self.assertEqual(keys, list(ENHANCED_FIELDS))
        self.assertEqual(ENHANCED_FIELD_LABELS["research_background"], "研究背景")
        self.assertEqual(len(ENHANCED_FIELD_LABELS), 6)

    def test_ai_generated_flag_default_false(self):
        self.assertFalse(EnhancedSummary().ai_generated)

    def test_not_configured_text_constant(self):
        self.assertEqual(ENHANCED_NOT_CONFIGURED_TEXT, "增强分析服务未配置")


class TestOfflineProviderRegression(unittest.TestCase):
    """2. OfflineProvider 输出与 OfflineSummaryEngine 完全一致。"""

    def test_output_matches_engine(self):
        from tju_info_retrieval.services.offline_summary import OfflineSummaryEngine

        result = _paper_result(PAPER_ABSTRACT)
        provider = OfflineProvider()
        via_provider = provider.summarize_result(result)
        via_engine = OfflineSummaryEngine().summarize(result)
        self.assertEqual(via_provider, via_engine)
        self.assertIsInstance(via_provider, StructuredSummary)

    def test_protocol_signature(self):
        """summarize(evidence, context) 协议形态可调用。"""
        result = _paper_result(PAPER_ABSTRACT)
        provider = OfflineProvider()
        out = provider.summarize(
            EvidenceBundle.from_result(result),
            SummaryContext.from_result(result),
        )
        self.assertIsInstance(out, StructuredSummary)
        # 输出必须为证据文本子串或固定“信息不足”文案（no-hallucination
        # 不变量）；同一子句至多被一个字段使用，故不强求四字段全命中。
        self.assertIn(out.core_technology, PAPER_ABSTRACT)
        insufficient = "当前材料未提供足够信息，无法可靠提取。"
        self.assertTrue(
            out.main_results in PAPER_ABSTRACT
            or out.main_results == insufficient)

    def test_evidence_bundle_reuses_collector(self):
        result = _paper_result(PAPER_ABSTRACT)
        bundle = EvidenceBundle.from_result(result)
        self.assertTrue(bundle.blocks)
        labels = {label for label, _text in bundle.blocks}
        self.assertIn("abstract", labels)


class TestMockEnhancedProvider(unittest.TestCase):
    """3. MockEnhancedProvider 输出。"""

    def test_provider_name_and_flag(self):
        result = _paper_result(PAPER_ABSTRACT)
        provider = MockEnhancedProvider()
        out = provider.summarize(
            EvidenceBundle.from_result(result),
            SummaryContext.from_result(result, user_research_direction="太赫兹"),
        )
        self.assertEqual(out.provider, MOCK_PROVIDER_NAME)
        self.assertTrue(out.ai_generated)
        self.assertIsInstance(out, EnhancedSummary)

    def test_outputs_reference_title_and_direction(self):
        result = _paper_result(PAPER_ABSTRACT)
        provider = MockEnhancedProvider()
        out = provider.summarize(
            EvidenceBundle.from_result(result),
            SummaryContext.from_result(result, user_research_direction="太赫兹"),
        )
        self.assertIn("太赫兹时域光谱茶叶检测方法研究", out.research_background)
        self.assertIn("太赫兹", out.relation_to_user_direction)

    def test_no_direction_degrades_gracefully(self):
        result = _paper_result("")
        provider = MockEnhancedProvider()
        out = provider.summarize(
            EvidenceBundle.from_result(result),
            SummaryContext.from_result(result),
        )
        self.assertIn("未提供研究方向", out.relation_to_user_direction)
        # 空摘要也不崩溃
        self.assertTrue(out.technical_route)

    def test_not_configured_placeholder(self):
        out = not_configured_summary()
        self.assertFalse(out.ai_generated)
        self.assertEqual(out.provider, "")
        for key in ENHANCED_FIELDS:
            self.assertEqual(getattr(out, key), ENHANCED_NOT_CONFIGURED_TEXT)


class TestSummaryServiceRouting(unittest.TestCase):
    """4. SummaryService 模式切换。

    2.7-B 语义：增强默认关闭；显式注入 Mock（enhanced_provider）即
    视为已配置，用于验证增强能力路由。
    """

    def setUp(self):
        self.service = SummaryService(
            enhanced_provider=MockEnhancedProvider())
        self.result = _paper_result(PAPER_ABSTRACT)

    def test_basic_mode_returns_structured_summary(self):
        out = self.service.summarize(MODE_BASIC, self.result)
        self.assertIsInstance(out, StructuredSummary)
        self.assertNotIsInstance(out, EnhancedSummary)

    def test_enhanced_mode_returns_enhanced_summary(self):
        out = self.service.summarize(MODE_ENHANCED, self.result)
        self.assertIsInstance(out, EnhancedSummary)
        self.assertEqual(out.provider, MOCK_PROVIDER_NAME)

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            self.service.summarize("banana", self.result)

    def test_enhanced_passes_user_direction(self):
        out = self.service.summarize(
            MODE_ENHANCED, self.result, user_research_direction="太赫兹")
        self.assertIn("太赫兹", out.relation_to_user_direction)

    def test_basic_ignores_direction(self):
        """basic 输出与方向无关（离线确定性，行为与 v0.17 一致）。"""
        a = self.service.summarize(MODE_BASIC, self.result, "太赫兹")
        b = self.service.summarize(MODE_BASIC, self.result, "")
        self.assertEqual(a, b)

    def test_enhanced_default_disabled_without_injection(self):
        """未配置 → 增强返回未配置降级（与生产配置解耦显式指定）。"""
        service = SummaryService(config={"enabled": False})
        out = service.summarize(MODE_ENHANCED, self.result)
        self.assertIsInstance(out, EnhancedSummary)
        self.assertFalse(out.ai_generated)
        self.assertEqual(out.provider, "")


class TestUnconfiguredDegradation(unittest.TestCase):
    """5. 未配置增强 Provider 时降级提示，不崩溃。"""

    def test_unconfigured_enhanced_returns_placeholder(self):
        # 2.8-C：与生产 runtime 配置解耦（用户可能已启用真实 API）
        service = SummaryService(config={"enabled": False})
        result = _paper_result(PAPER_ABSTRACT)
        out = service.summarize(MODE_ENHANCED, result)
        self.assertIsInstance(out, EnhancedSummary)
        self.assertFalse(out.ai_generated)
        self.assertEqual(out.research_background, ENHANCED_NOT_CONFIGURED_TEXT)
        self.assertFalse(service.enhanced_configured)

    def test_unconfigured_basic_still_works(self):
        service = SummaryService(config={"enabled": False})
        result = _paper_result(PAPER_ABSTRACT)
        out = service.summarize(MODE_BASIC, result)
        self.assertIsInstance(out, StructuredSummary)

    def test_toggle_configured_at_runtime(self):
        # 显式注入 Mock 视为已配置；运行期可切换开关
        service = SummaryService(enhanced_provider=MockEnhancedProvider())
        result = _paper_result(PAPER_ABSTRACT)
        service.set_enhanced_configured(False)
        out = service.summarize(MODE_ENHANCED, result)
        self.assertFalse(out.ai_generated)
        service.set_enhanced_configured(True)
        out = service.summarize(MODE_ENHANCED, result)
        self.assertTrue(out.ai_generated)
        self.assertTrue(service.enhanced_configured)


if __name__ == "__main__":
    unittest.main(verbosity=2)