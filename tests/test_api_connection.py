#!/usr/bin/env python3
"""正式测试：v0.18 Phase 2.8-A API 连接测试分类。

用 mock transport 覆盖：成功 / 401(Key错误) / timeout / 500(服务器) /
bad json(格式) / 未配置 / 缺 key。禁止真实网络调用。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import requests

from tju_info_retrieval.services.api_connection_test import (
    CATEGORY_LABELS,
    test_connection,
)

VALID_CFG = {
    "enabled": True,
    "provider": "openai-compatible",
    "base_url": "https://api.example.invalid/v1",
    "model": "test-model",
    "api_key": "test-key-not-real",
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
    body = {"choices": [{"message": {"role": "assistant",
                                     "content": "CONNECTIVITY_OK"}}]}
    return _FakeResponse(ok=True, status_code=200, text=json.dumps(body))


class TestConnectionTest(unittest.TestCase):
    def test_success(self):
        with mock.patch("tju_info_retrieval.services.llm_provider.requests.post",
                        side_effect=_ok_transport):
            r = test_connection(provider_cfg=dict(VALID_CFG))
        self.assertTrue(r["ok"])
        self.assertEqual(r["category"], "ok")
        self.assertEqual(r["model"], "test-model")
        self.assertEqual(r["http_status"], 200)
        self.assertIsNotNone(r["latency_s"])

    def test_401_credential(self):
        def bad(*a, **k):
            return _FakeResponse(ok=False, status_code=401, text="bad key")

        with mock.patch("tju_info_retrieval.services.llm_provider.requests.post",
                        side_effect=bad):
            r = test_connection(provider_cfg=dict(VALID_CFG))
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "credential")
        self.assertEqual(r["label"], CATEGORY_LABELS["credential"])

    def test_timeout(self):
        def slow(*a, **k):
            raise requests.Timeout("took too long")

        with mock.patch("tju_info_retrieval.services.llm_provider.requests.post",
                        side_effect=slow):
            r = test_connection(provider_cfg=dict(VALID_CFG))
        self.assertEqual(r["category"], "timeout")
        self.assertEqual(r["label"], CATEGORY_LABELS["timeout"])

    def test_500_server(self):
        def bad(*a, **k):
            return _FakeResponse(ok=False, status_code=500, text="boom")

        with mock.patch("tju_info_retrieval.services.llm_provider.requests.post",
                        side_effect=bad):
            r = test_connection(provider_cfg=dict(VALID_CFG))
        self.assertEqual(r["category"], "server")
        self.assertEqual(r["http_status"], 500)

    def test_bad_json_format(self):
        def bad(*a, **k):
            return _FakeResponse(ok=True, status_code=200,
                                 text='{"choices": [{"message": {}}]}')

        with mock.patch("tju_info_retrieval.services.llm_provider.requests.post",
                        side_effect=bad):
            r = test_connection(provider_cfg=dict(VALID_CFG))
        self.assertEqual(r["category"], "format")

    def test_404_model(self):
        def bad(*a, **k):
            return _FakeResponse(ok=False, status_code=404, text="no model")

        with mock.patch("tju_info_retrieval.services.llm_provider.requests.post",
                        side_effect=bad):
            r = test_connection(provider_cfg=dict(VALID_CFG))
        self.assertEqual(r["category"], "model")

    def test_disabled_config(self):
        cfg = dict(VALID_CFG)
        cfg["enabled"] = False
        r = test_connection(provider_cfg=cfg)
        self.assertEqual(r["category"], "config")

    def test_missing_key(self):
        cfg = dict(VALID_CFG)
        cfg["api_key"] = ""
        r = test_connection(provider_cfg=cfg)
        self.assertEqual(r["category"], "credential")

    def test_no_network_called_when_misconfigured(self):
        cfg = dict(VALID_CFG)
        cfg["enabled"] = False
        with mock.patch("tju_info_retrieval.services.llm_provider.requests.post") as m:
            test_connection(provider_cfg=cfg)
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSummaryServiceRefresh(unittest.TestCase):
    """v0.18 Phase 2.8-B：保存配置后 SummaryService 刷新（增强 Provider 更新）。"""

    def test_service_enhanced_enabled_after_save(self):
        import tempfile
        from tju_info_retrieval.services.api_config_manager import APIConfigManager
        from tju_info_retrieval.services import credential_store as cs_mod
        from tju_info_retrieval.services.credential_store import CredentialStore
        from tju_info_retrieval.services.summary_service import (
            MODE_ENHANCED, SummaryService)

        tmp = tempfile.TemporaryDirectory()
        os.environ["TEST_USER_DATA_ROOT"] = tmp.name
        kr = mock.patch.object(cs_mod, "_keyring_available", return_value=True)
        kg = mock.patch.object(
            cs_mod, "_get_keyring",
            return_value=type("KR", (), {
                "get_password": lambda s, a, b: "refresh-key",
                "set_password": lambda s, a, b, c: None,
                "delete_password": lambda s, a, b: None,
            })())
        kr.start(); kg.start()
        try:
            mgr = APIConfigManager(credential_store=CredentialStore())
            mgr.set_from_settings("tju", "https://x/v1", "tju-llm",
                                  "refresh-key")
            # 保存后构建的 service 应已启用增强
            service = SummaryService(config=mgr.provider_config())
            self.assertTrue(service.enhanced_configured)
            from tju_info_retrieval.models.result import SearchResult
            result = SearchResult(
                rank=1, title="刷新测试论文", authors=["张三"], source="CNKI",
                year=2024, detail_url="https://x", database="CNKI",
                artifact_type="paper", artifact_metadata={},
                abstract="本文提出一种基于太赫兹光谱的检测方法。")
            # LLM 分支验证：mock 网络失败 → 应是“请求失败”降级而非“未配置”
            with mock.patch(
                    "tju_info_retrieval.services.llm_provider.requests.post",
                    side_effect=lambda *a, **k: (_ for _ in ()).throw(
                        __import__("requests").ConnectionError("blocked"))):
                out = service.summarize(MODE_ENHANCED, result)
            self.assertNotEqual(out.research_background, "增强分析服务未配置")
            self.assertIn("请求失败", out.research_background)
        finally:
            kr.stop(); kg.stop()
            os.environ.pop("TEST_USER_DATA_ROOT", None)
            tmp.cleanup()
