"""智能体大赛专属 Provider（agent2026）测试。

覆盖 v0.18 比赛接入要求：
1. agent2026 注册表条目与专属 endpoint 路由；
2. base_url 不得回落到普通 /api/v3 网关；
3. 最终 chat completions URL 正确；
4. model 固定 tju-llm；
5. API Key 不进入日志；
6. 普通 Provider（tju_llm/deepseek/custom）回归不受影响。
"""
import json
import logging
import unittest

from tju_info_retrieval.services import ai_provider_registry as reg
from tju_info_retrieval.services.llm_provider import OpenAICompatibleProvider

COMPETITION_BASE = (
    "https://ai.tju.edu.cn/api/agent2026/gitlab-102-agent2026-qwen-agent"
)
COMPETITION_CHAT = COMPETITION_BASE + "/chat/completions"


def _agent2026_config(api_key="test-key-not-real"):
    return {
        "enabled": True,
        "provider": reg.PROVIDER_AGENT2026,
        "base_url": reg.default_base_url(reg.PROVIDER_AGENT2026),
        "api_key": api_key,
        "model": reg.default_model(reg.PROVIDER_AGENT2026),
    }


class _FakeResponse:
    def __init__(self, ok=True, status_code=200, text=""):
        self.ok = ok
        self.status_code = status_code
        self._text = text

    def json(self):
        return json.loads(self._text)


def _ok_transport(*args, **kwargs):
    body = {"choices": [{"message": {"role": "assistant", "content": "{}"}}]}
    return _FakeResponse(ok=True, status_code=200, text=json.dumps(body))


class TestAgent2026Registry(unittest.TestCase):
    """注册表条目与路由属性。"""

    def test_agent2026_is_known_provider(self):
        self.assertTrue(reg.is_known_provider("agent2026"))

    def test_default_base_url_is_competition_endpoint(self):
        self.assertEqual(reg.default_base_url("agent2026"), COMPETITION_BASE)

    def test_base_url_does_not_fall_back_to_api_v3(self):
        self.assertNotIn(
            "/api/v3", reg.default_base_url("agent2026"),
            "比赛 Provider 的 base_url 不得回落到普通 /api/v3 网关")

    def test_default_model_is_tju_llm(self):
        self.assertEqual(reg.default_model("agent2026"), "tju-llm")

    def test_options_contain_agent2026_after_tju(self):
        options = reg.provider_options()
        ids = [pid for _, pid in options]
        self.assertIn("agent2026", ids)
        self.assertLess(ids.index("tju_llm"), ids.index("agent2026"))

    def test_label_mentions_competition(self):
        self.assertIn("智能体大赛", reg.provider_label("agent2026"))


class TestAgent2026Routing(unittest.TestCase):
    """实际请求路由：URL / 模型 / 认证头。"""

    def test_chat_completions_url_routing(self):
        captured = {}

        def record_transport(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _ok_transport()

        provider = OpenAICompatibleProvider(
            _agent2026_config(), transport=record_transport)
        provider.complete("你好")

        self.assertEqual(captured.get("url"), COMPETITION_CHAT,
                         "最终请求必须到达比赛专属 /chat/completions")
        self.assertNotIn("/api/v3", captured.get("url", ""))
        self.assertEqual(captured["json"]["model"], "tju-llm")
        self.assertTrue(
            str(captured["headers"].get("Authorization", "")).startswith(
                "Bearer "))

    def test_api_key_never_appears_in_logs(self):
        api_key = "test-key-not-real-abcdef123456"
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = _Capture()
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            provider = OpenAICompatibleProvider(
                _agent2026_config(api_key=api_key),
                transport=_ok_transport)
            provider.complete("你好")
        finally:
            root.removeHandler(handler)

        leaked = [r for r in records if api_key in r]
        self.assertEqual(
            leaked, [],
            "API Key 不得出现在任何日志记录中")

    def test_api_key_not_in_error_message(self):
        def auth_fail_transport(*args, **kwargs):
            return _FakeResponse(ok=False, status_code=401,
                                 text="unauthorized")

        provider = OpenAICompatibleProvider(
            _agent2026_config(api_key="test-key-not-real-abcdef123456"),
            transport=auth_fail_transport)
        with self.assertRaises(Exception) as ctx:
            provider.complete("你好")
        self.assertNotIn("test-key-not-real-abcdef123456", str(ctx.exception))


class TestNormalProviderRegression(unittest.TestCase):
    """普通 Provider 回归：本次接入不得影响既有行为。"""

    def test_tju_llm_still_uses_api_v3(self):
        self.assertEqual(
            reg.default_base_url("tju_llm"),
            "https://ai.tju.edu.cn/api/v3")
        self.assertEqual(reg.default_model("tju_llm"), "tju-llm")

    def test_deepseek_unchanged(self):
        self.assertEqual(
            reg.default_base_url("deepseek"), "https://api.deepseek.com")
        self.assertEqual(reg.default_model("deepseek"), "deepseek-chat")

    def test_custom_provider_still_empty_by_default(self):
        self.assertEqual(reg.default_base_url("custom"), "")
        self.assertEqual(reg.default_api_key_env("tju_llm"),
                         "TJU_INFO_LLM_API_KEY")

    def test_legacy_mapping_unchanged(self):
        self.assertEqual(reg.normalize_provider("tju"), "tju_llm")
        self.assertEqual(
            reg.normalize_provider("openai-compatible"), "custom")

    def test_normal_tju_request_routing_unchanged(self):
        captured = {}

        def record_transport(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            return _ok_transport()

        cfg = {
            "enabled": True,
            "provider": "tju_llm",
            "base_url": reg.default_base_url("tju_llm"),
            "api_key": "test-key-not-real",
            "model": "tju-llm",
        }
        OpenAICompatibleProvider(cfg, transport=record_transport).complete(
            "hi")
        self.assertEqual(
            captured["url"], "https://ai.tju.edu.cn/api/v3/chat/completions")


if __name__ == "__main__":
    unittest.main()
