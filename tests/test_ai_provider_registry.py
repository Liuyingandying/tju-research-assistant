#!/usr/bin/env python3
"""正式测试：AI Provider 注册表（v0.18 Phase 2.11-A Phase B）。

纯数据模块测试：provider 列表 / 默认配置 / endpoint 模板 / model 模板 /
旧 id 归一化，无网络、无副作用。
"""
from __future__ import annotations

from tju_info_retrieval.services.ai_provider_registry import (
    PROVIDER_CUSTOM,
    PROVIDER_DEEPSEEK,
    PROVIDER_IDS,
    PROVIDER_TJU,
    default_api_key_env,
    default_base_url,
    default_model,
    endpoint_template,
    is_known_provider,
    normalize_provider,
    provider_entry,
    provider_label,
    provider_options,
)


class TestProviderIds:
    def test_three_builtin_providers(self):
        assert PROVIDER_IDS == ("tju_llm", "deepseek", "custom")
        assert PROVIDER_TJU == "tju_llm"
        assert PROVIDER_DEEPSEEK == "deepseek"
        assert PROVIDER_CUSTOM == "custom"

    def test_known_provider(self):
        assert is_known_provider("tju_llm")
        assert is_known_provider("deepseek")
        assert is_known_provider("custom")
        assert not is_known_provider("unknown-provider")
        assert not is_known_provider("")
        assert not is_known_provider(None)


class TestProviderDefaults:
    def test_tju_defaults(self):
        assert default_base_url("tju_llm") == "https://ai.tju.edu.cn/api/v3"
        assert default_model("tju_llm") == "tju-llm"

    def test_deepseek_defaults(self):
        assert default_base_url("deepseek") == "https://api.deepseek.com"
        assert default_model("deepseek") == "deepseek-chat"
        assert default_api_key_env("deepseek") == "DEEPSEEK_API_KEY"

    def test_custom_defaults_empty(self):
        assert default_base_url("custom") == ""
        assert default_model("custom") == ""

    def test_unknown_provider_returns_empty(self):
        assert default_base_url("nope") == ""
        assert default_model("nope") == ""
        assert default_api_key_env("nope") == ""
        assert provider_entry("nope") == {}


class TestProviderOptions:
    def test_options_shape_and_order(self):
        options = provider_options()
        assert options == [
            ("天津大学 LLM", "tju_llm"),
            ("DeepSeek API", "deepseek"),
            ("自定义 OpenAI Compatible", "custom"),
        ]
        # 下拉框 data 值与 label 一一对应
        for label, value in options:
            assert provider_label(value) == label

    def test_labels(self):
        assert provider_label("tju_llm") == "天津大学 LLM"
        assert provider_label("deepseek") == "DeepSeek API"
        assert provider_label("custom") == "自定义 OpenAI Compatible"


class TestNormalizeProvider:
    def test_new_ids_passthrough(self):
        assert normalize_provider("tju_llm") == "tju_llm"
        assert normalize_provider("deepseek") == "deepseek"
        assert normalize_provider("custom") == "custom"

    def test_legacy_tju_maps_to_tju_llm(self):
        assert normalize_provider("tju") == "tju_llm"

    def test_legacy_openai_compat_maps_to_custom(self):
        assert normalize_provider("openai-compatible") == "custom"
        assert normalize_provider("openai") == "custom"

    def test_unknown_passthrough(self):
        assert normalize_provider("something-else") == "something-else"
        assert normalize_provider("") == ""
        assert normalize_provider(None) == ""


class TestEndpointTemplate:
    def test_uniform_openai_compatible_endpoint(self):
        for pid in PROVIDER_IDS:
            assert endpoint_template(pid) == "{base_url}/chat/completions"

    def test_deepseek_endpoint_expands_to_expected(self):
        template = endpoint_template("deepseek")
        assert template.format(
            base_url="https://api.deepseek.com") == \
            "https://api.deepseek.com/chat/completions"


class TestProviderEntry:
    def test_entry_returns_copy(self):
        entry = provider_entry("deepseek")
        entry["label"] = "被篡改"
        assert provider_entry("deepseek")["label"] == "DeepSeek API"

    def test_entry_contains_required_keys(self):
        entry = provider_entry("deepseek")
        for key in ("id", "label", "default_base_url", "default_model",
                    "api_key_env", "endpoint_template"):
            assert key in entry
