#!/usr/bin/env python3
"""正式测试：v0.18 Phase 2.7-B OpenAI-compatible Provider 基础设施。

覆盖（禁止真实 API 调用，全部 mock transport / 临时配置）：
1. 配置读取；
2. 默认关闭（enabled=false → 未配置降级）；
3. 无 key 失败（LLMConfigError）；
4. mock HTTP 响应（成功路径 + HTTP 错误分类）；
5. JSON 解析（合法/非法/缺字段）；
6. timeout（LLMTimeoutError）；
7. cache 命中（第二次不请求 transport）；
8. 基础模式回归（basic 仍返回 StructuredSummary）。
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

import requests

from tju_info_retrieval.models.enhanced_summary import EnhancedSummary
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.models.summary import StructuredSummary
from tju_info_retrieval.services.enhanced_cache import EnhancedSummaryCache
from tju_info_retrieval.services.enhanced_provider import LLMEnhancedProvider
from tju_info_retrieval.services.llm_provider import (
    EXAMPLE_PATH,
    LLMConfigError,
    LLMHTTPError,
    LLMNetworkError,
    LLMParseError,
    LLMTimeoutError,
    OpenAICompatibleProvider,
    load_provider_config,
)
from tju_info_retrieval.services.prompts.enhanced_summary_prompt import (
    build_enhanced_summary_prompt,
    parse_enhanced_json,
)
from tju_info_retrieval.services.summary_service import (
    MODE_BASIC,
    MODE_ENHANCED,
    SummaryService,
)
from tju_info_retrieval.services.summary_provider import (
    EvidenceBundle,
    SummaryContext,
)

VALID_CONFIG = {
    "enabled": True,
    "provider": "openai-compatible",
    "base_url": "https://api.example.invalid/v1",
    "api_key": "test-key-not-real",
    "model": "test-model",
}

FIELDS = {
    "research_background": "背景",
    "technical_route": "路线",
    "innovation_points": "创新",
    "relation_to_user_direction": "关联",
    "reading_recommendation": "建议",
    "limitations": "局限",
}


class _FakeResponse:
    def __init__(self, ok=True, status_code=200, text=""):
        self.ok = ok
        self.status_code = status_code
        self._text = text

    @property
    def text(self):
        return self._text

    def json(self):
        return json.loads(self._text)


def _ok_transport(*args, **kwargs):
    """返回合法 Chat Completions 响应的 transport。"""
    body = {
        "choices": [{"message": {"role": "assistant",
                                  "content": json.dumps(FIELDS, ensure_ascii=False)}}]
    }
    return _FakeResponse(ok=True, status_code=200, text=json.dumps(body))


def _paper_result(abstract="") -> SearchResult:
    return SearchResult(
        rank=1,
        title="太赫兹时域光谱茶叶检测方法研究",
        authors=["张三"],
        source="CNKI",
        year=2024,
        detail_url="https://example.invalid/article",
        database="CNKI",
        artifact_type="paper",
        artifact_metadata={"doi": "10.1000/example"},
        abstract=abstract,
    )


PAPER_ABSTRACT = (
    "本文针对茶叶掺假快速检测难题，提出基于太赫兹时域光谱（THz-TDS）"
    "结合机器学习分类的新方法。实验结果表明，该方法对三种茶叶样本的"
    "识别准确率达到 96%，可用于食品安全快速检测与工程应用。"
)


class TestConfigLoading(unittest.TestCase):
    """1. 配置读取。"""

    def test_loads_valid_config(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "provider.json"
            path.write_text(json.dumps(VALID_CONFIG), encoding="utf-8")
            cfg = load_provider_config(path)
            self.assertTrue(cfg["enabled"])
            self.assertEqual(cfg["model"], "test-model")

    def test_missing_config_defaults_disabled(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            cfg = load_provider_config(path)
            self.assertFalse(cfg["enabled"])
            self.assertEqual(cfg["base_url"], "")

    def test_corrupt_config_disabled(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            cfg = load_provider_config(path)
            self.assertFalse(cfg["enabled"])


class TestSecretManagement(unittest.TestCase):
    """v0.18 Phase 2.7-C：api_key_env Secret 管理。"""

    ENV_CONFIG = {
        "enabled": True,
        "provider": "openai-compatible",
        "base_url": "https://api.example.invalid/v1",
        "model": "test-model",
        "api_key_env": "TJU_TEST_LLM_KEY",
    }

    def setUp(self):
        self._saved = os.environ.get("TJU_TEST_LLM_KEY")
        os.environ.pop("TJU_TEST_LLM_KEY", None)

    def tearDown(self):
        os.environ.pop("TJU_TEST_LLM_KEY", None)
        if self._saved is not None:
            os.environ["TJU_TEST_LLM_KEY"] = self._saved

    def test_api_key_env_reads_environment(self):
        os.environ["TJU_TEST_LLM_KEY"] = "env-key-placeholder"
        provider = OpenAICompatibleProvider(dict(self.ENV_CONFIG))
        self.assertTrue(provider.api_key_configured)
        provider.validate()  # 不抛（凭据来自环境变量）

    def test_api_key_env_missing_degrades(self):
        # 环境变量未设置 → 缺少 API 凭据（不崩溃）
        provider = OpenAICompatibleProvider(dict(self.ENV_CONFIG))
        self.assertFalse(provider.api_key_configured)
        with self.assertRaises(LLMConfigError) as ctx:
            provider.validate()
        self.assertIn("缺少 API 凭据", str(ctx.exception))

    def test_legacy_api_key_field_backward_compatible(self):
        # 旧配置 api_key 字段仍可回退读取（向后兼容）
        cfg = dict(self.ENV_CONFIG)
        cfg["api_key"] = "legacy-key-placeholder"
        provider = OpenAICompatibleProvider(cfg)
        self.assertTrue(provider.api_key_configured)

    def test_env_preferred_over_legacy_field(self):
        # api_key_env 存在时优先环境变量，忽略 JSON 明文
        cfg = dict(self.ENV_CONFIG)
        cfg["api_key"] = "legacy-key-placeholder"
        os.environ["TJU_TEST_LLM_KEY"] = "env-key-placeholder"
        provider = OpenAICompatibleProvider(cfg)
        self.assertTrue(provider.api_key_configured)
        provider.validate()

    def test_example_has_no_real_credentials(self):
        example = EXAMPLE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("sk-", example)
        self.assertNotIn("api_key\": \"", example)
        self.assertIn("api_key_env", example)
        self.assertIn("YOUR_", example)

    def test_configured_summary_service_uses_env(self):
        os.environ["TJU_TEST_LLM_KEY"] = "env-key-placeholder"
        service = SummaryService(config=dict(self.ENV_CONFIG))
        self.assertTrue(service.enhanced_configured)
        # 未设置环境变量时 SummaryService 不启用增强（未配置降级）
        os.environ.pop("TJU_TEST_LLM_KEY", None)
        service2 = SummaryService(config=dict(self.ENV_CONFIG))
        self.assertTrue(service2.enhanced_configured)  # 门面按 enabled 判定；
        # 实际调用时缺凭据 → 降级“未配置”文案（由 LLMEnhancedProvider 保证）
        result = _paper_result(PAPER_ABSTRACT)
        out = service2.summarize(MODE_ENHANCED, result)
        self.assertFalse(out.ai_generated)
        self.assertEqual(out.research_background, "增强分析服务未配置")


class TestDefaultDisabled(unittest.TestCase):
    """2. 默认关闭。"""

    def test_service_enhanced_unconfigured_when_config_disabled(self):
        # 2.8-C：与生产 runtime 配置解耦——显式 disabled 验证未配置路径
        service = SummaryService(config={"enabled": False})
        self.assertFalse(service.enhanced_configured)

    def test_enhanced_mode_returns_not_configured(self):
        # 2.8-C：生产 runtime 配置可能已启用——显式 disabled 验证降级
        service = SummaryService(config={"enabled": False})
        result = _paper_result(PAPER_ABSTRACT)
        out = service.summarize(MODE_ENHANCED, result)
        self.assertIsInstance(out, EnhancedSummary)
        self.assertFalse(out.ai_generated)
        self.assertEqual(out.research_background, "增强分析服务未配置")

    def test_provider_validate_raises_when_disabled(self):
        provider = OpenAICompatibleProvider({"enabled": False})
        with self.assertRaises(LLMConfigError):
            provider.validate()


class TestMissingKey(unittest.TestCase):
    """3. 无 key 失败（配置错误分类）。"""

    def test_missing_api_key_raises_config_error(self):
        cfg = dict(VALID_CONFIG)
        cfg["api_key"] = ""
        provider = OpenAICompatibleProvider(cfg)
        with self.assertRaises(LLMConfigError):
            provider.validate()

    def test_missing_base_url_raises_config_error(self):
        cfg = dict(VALID_CONFIG)
        cfg["base_url"] = ""
        provider = OpenAICompatibleProvider(cfg)
        with self.assertRaises(LLMConfigError):
            provider.validate()

    def test_missing_model_raises_config_error(self):
        cfg = dict(VALID_CONFIG)
        cfg["model"] = ""
        provider = OpenAICompatibleProvider(cfg)
        with self.assertRaises(LLMConfigError):
            provider.validate()


class TestMockHTTP(unittest.TestCase):
    """4. mock HTTP 响应。"""

    def test_success_complete_returns_content(self):
        provider = OpenAICompatibleProvider(
            VALID_CONFIG, transport=_ok_transport)
        out = provider.complete("hello")
        self.assertEqual(json.loads(out), FIELDS)

    def test_http_error_raises_http_error(self):
        def bad_transport(*args, **kwargs):
            return _FakeResponse(ok=False, status_code=500, text="server down")

        provider = OpenAICompatibleProvider(
            VALID_CONFIG, transport=bad_transport)
        with self.assertRaises(LLMHTTPError) as ctx:
            provider.complete("hello")
        self.assertEqual(ctx.exception.status_code, 500)

    def test_network_error_raises_network_error(self):
        def net_transport(*args, **kwargs):
            raise requests.ConnectionError("refused")

        provider = OpenAICompatibleProvider(
            VALID_CONFIG, transport=net_transport)
        with self.assertRaises(LLMNetworkError):
            provider.complete("hello")

    def test_request_payload_shape(self):
        captured = {}

        def record_transport(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _ok_transport()

        provider = OpenAICompatibleProvider(
            VALID_CONFIG, transport=record_transport)
        provider.complete("hi")
        self.assertEqual(captured["url"], "https://api.example.invalid/v1/chat/completions")
        self.assertTrue(captured["headers"]["Authorization"].startswith("Bearer "))
        self.assertEqual(captured["json"]["model"], "test-model")
        self.assertFalse(captured["json"]["stream"])


class TestJSONParsing(unittest.TestCase):
    """5. JSON 解析。"""

    def test_parse_valid_json(self):
        text = json.dumps(FIELDS, ensure_ascii=False)
        result = parse_enhanced_json(text)
        self.assertEqual(result["research_background"], "背景")

    def test_parse_fenced_json(self):
        text = "```json\n" + json.dumps(FIELDS, ensure_ascii=False) + "\n```"
        result = parse_enhanced_json(text)
        self.assertEqual(result["innovation_points"], "创新")

    def test_parse_missing_field_becomes_insufficient(self):
        result = parse_enhanced_json('{"research_background": "x"}')
        self.assertEqual(result["research_background"], "x")
        self.assertEqual(result["technical_route"], "根据当前摘要无法判断")

    def test_parse_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_enhanced_json("not json at all")

    def test_parse_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_enhanced_json("")

    def test_prompt_contains_fields_and_direction(self):
        result = _paper_result(PAPER_ABSTRACT)
        context = SummaryContext.from_result(result, "太赫兹")
        prompt = build_enhanced_summary_prompt(context)
        self.assertIn("research_background", prompt)
        self.assertIn("太赫兹时域光谱茶叶检测方法研究", prompt)
        self.assertIn("太赫兹", prompt)


class TestTimeout(unittest.TestCase):
    """6. timeout 分类。"""

    def test_timeout_raises_timeout_error(self):
        def slow_transport(*args, **kwargs):
            raise requests.Timeout("took too long")

        provider = OpenAICompatibleProvider(
            VALID_CONFIG, transport=slow_transport, timeout_s=0.1)
        with self.assertRaises(LLMTimeoutError):
            provider.complete("hello")


class TestCache(unittest.TestCase):
    """7. 缓存命中：第二次不请求 transport。"""

    def test_second_call_hits_cache(self):
        calls = {"n": 0}

        def counting_transport(*args, **kwargs):
            calls["n"] += 1
            return _ok_transport()

        with TemporaryDirectory() as tmp:
            cache = EnhancedSummaryCache(Path(tmp))
            provider = LLMEnhancedProvider(
                llm_client=OpenAICompatibleProvider(
                    VALID_CONFIG, transport=counting_transport),
                cache=cache,
            )
            result = _paper_result(PAPER_ABSTRACT)
            evidence = EvidenceBundle.from_result(result)
            context = SummaryContext.from_result(result, "太赫兹")
            first = provider.summarize(evidence, context)
            self.assertEqual(calls["n"], 1)
            second = provider.summarize(evidence, context)
            self.assertEqual(calls["n"], 1)  # 命中缓存，不再请求
            self.assertEqual(first.to_dict(), second.to_dict())

    def test_cache_key_differs_by_model(self):
        cache = EnhancedSummaryCache()
        k1 = cache.key_for("t", "a", "p", "m1")
        k2 = cache.key_for("t", "a", "p", "m2")
        self.assertNotEqual(k1, k2)

    def test_cache_persists_to_disk(self):
        with TemporaryDirectory() as tmp:
            dir_path = Path(tmp)
            cache = EnhancedSummaryCache(dir_path)
            summary = EnhancedSummary(
                **FIELDS, provider="test", ai_generated=True)
            key = cache.key_for("t", "a", "p", "m")
            cache.put(key, summary)
            # 新实例（清内存）→ 磁盘命中
            cache2 = EnhancedSummaryCache(dir_path)
            hit = cache2.get(key)
            self.assertIsNotNone(hit)
            self.assertEqual(hit.provider, "test")


class TestLLMEnhancedProvider(unittest.TestCase):
    """LLMEnhancedProvider 端到端（mock transport）。"""

    def test_success_returns_ai_summary(self):
        with TemporaryDirectory() as tmp:
            provider = LLMEnhancedProvider(
                llm_client=OpenAICompatibleProvider(
                    VALID_CONFIG, transport=_ok_transport),
                cache=EnhancedSummaryCache(Path(tmp)),
            )
            result = _paper_result(PAPER_ABSTRACT)
            out = provider.summarize(
                EvidenceBundle.from_result(result),
                SummaryContext.from_result(result, "太赫兹"),
            )
        self.assertTrue(out.ai_generated)
        self.assertEqual(out.provider, "openai-compatible")
        self.assertEqual(out.research_background, "背景")

    def test_failure_degrades_not_crash(self):
        def bad_transport(*args, **kwargs):
            raise requests.ConnectionError("boom")

        with TemporaryDirectory() as tmp:
            provider = LLMEnhancedProvider(
                llm_client=OpenAICompatibleProvider(
                    VALID_CONFIG, transport=bad_transport),
                cache=EnhancedSummaryCache(Path(tmp)),
            )
            result = _paper_result(PAPER_ABSTRACT)
            out = provider.summarize(
                EvidenceBundle.from_result(result),
                SummaryContext.from_result(result),
            )
        self.assertFalse(out.ai_generated)
        self.assertEqual(out.research_background, "增强分析请求失败")

    def test_parse_failure_degrades(self):
        def prose_transport(*args, **kwargs):
            body = {"choices": [{"message": {"role": "assistant",
                                             "content": "not json"}}]}
            return _FakeResponse(ok=True, status_code=200,
                                 text=json.dumps(body))

        with TemporaryDirectory() as tmp:
            provider = LLMEnhancedProvider(
                llm_client=OpenAICompatibleProvider(
                    VALID_CONFIG, transport=prose_transport),
                cache=EnhancedSummaryCache(Path(tmp)),
            )
            result = _paper_result(PAPER_ABSTRACT)
            out = provider.summarize(
                EvidenceBundle.from_result(result),
                SummaryContext.from_result(result),
            )
        self.assertEqual(out.research_background, "增强分析结果解析失败")

    def test_unconfigured_degrades(self):
        with TemporaryDirectory() as tmp:
            provider = LLMEnhancedProvider(
                llm_client=OpenAICompatibleProvider({"enabled": False}),
                cache=EnhancedSummaryCache(Path(tmp)),
            )
            result = _paper_result(PAPER_ABSTRACT)
            out = provider.summarize(
                EvidenceBundle.from_result(result),
                SummaryContext.from_result(result),
            )
        self.assertEqual(out.research_background, "增强分析服务未配置")


class TestBasicModeRegression(unittest.TestCase):
    """8. 基础模式回归（与 v0.17 一致）。"""

    def test_basic_mode_returns_structured_summary(self):
        service = SummaryService()
        result = _paper_result(PAPER_ABSTRACT)
        out = service.summarize(MODE_BASIC, result)
        self.assertIsInstance(out, StructuredSummary)
        self.assertNotIsInstance(out, EnhancedSummary)

    def test_basic_output_is_evidence_substring(self):
        service = SummaryService()
        result = _paper_result(PAPER_ABSTRACT)
        out = service.summarize(MODE_BASIC, result)
        self.assertIn(out.core_technology, PAPER_ABSTRACT)

    def test_unknown_mode_raises(self):
        service = SummaryService()
        with self.assertRaises(ValueError):
            service.summarize("banana", _paper_result())


if __name__ == "__main__":
    unittest.main(verbosity=2)