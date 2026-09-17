#!/usr/bin/env python3
"""正式测试：多 Provider 配置能力（v0.18 Phase 2.11-A Phase C/D/E）。

覆盖：

1. Provider 切换：tju_llm / deepseek / custom 的默认值与 config 写入；
2. 配置保存：DeepSeek 配置示例 → load_config / provider_config 回读正确；
3. Key 隔离：切换 provider 后 API Key 仍经 CredentialStore（不落明文 JSON）；
4. SettingsDialog：下拉框三项、选择后自动填充 base_url / model；
5. 无侵入：SummaryService / ReportService 无需修改即可消费 provider_config。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tju_info_retrieval.services import credential_store as cs
from tju_info_retrieval.services.ai_provider_registry import (
    PROVIDER_CUSTOM,
    PROVIDER_DEEPSEEK,
    PROVIDER_TJU,
    default_api_key_env,
    default_base_url,
    default_model,
)
from tju_info_retrieval.services.api_config_manager import (
    APIConfigManager,
    config_path,
)
from tju_info_retrieval.services.credential_store import CredentialStore


class _FakeKeyring:
    def __init__(self):
        self._store = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        self._store.pop((service, username), None)


class _IsolatedCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["TEST_USER_DATA_ROOT"] = self._tmp.name
        self._keyring = _FakeKeyring()
        self._patches = [
            mock.patch.object(cs, "_keyring_available", return_value=True),
            mock.patch.object(cs, "_get_keyring", return_value=self._keyring),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        os.environ.pop("TEST_USER_DATA_ROOT", None)

    def _mgr(self):
        return APIConfigManager(credential_store=CredentialStore())


# ============================================================
# 1. Provider 切换
# ============================================================

class TestProviderSwitch(_IsolatedCase):
    def test_switch_to_deepseek_sets_defaults(self):
        mgr = self._mgr()
        mgr.set_from_settings(PROVIDER_DEEPSEEK, "", "", "sk-deepseek-1")
        cfg = mgr.load_config()
        assert cfg["provider"] == PROVIDER_DEEPSEEK
        assert cfg["base_url"] == "https://api.deepseek.com"
        assert cfg["model"] == "deepseek-chat"
        assert cfg["api_key_env"] == "DEEPSEEK_API_KEY"

    def test_switch_to_tju_sets_defaults(self):
        mgr = self._mgr()
        mgr.set_from_settings(PROVIDER_TJU, "", "", "sk-tju-1")
        cfg = mgr.load_config()
        assert cfg["provider"] == PROVIDER_TJU
        assert cfg["base_url"] == "https://ai.tju.edu.cn/api/v3"
        assert cfg["model"] == "tju-llm"

    def test_custom_requires_manual_url_and_model(self):
        mgr = self._mgr()
        mgr.set_from_settings(PROVIDER_CUSTOM, "https://my.api/v1",
                              "my-model", "sk-custom-1")
        cfg = mgr.load_config()
        assert cfg["provider"] == PROVIDER_CUSTOM
        assert cfg["base_url"] == "https://my.api/v1"
        assert cfg["model"] == "my-model"

    def test_legacy_tju_id_normalized_on_load(self):
        mgr = self._mgr()
        # 旧版写入 provider="tju" → load 时应归一为 tju_llm
        mgr.save_config({"enabled": True, "provider": "tju",
                         "base_url": "https://x/v1", "model": "tju-llm",
                         "api_key_env": "TJU_INFO_LLM_API_KEY"})
        assert mgr.load_config()["provider"] == PROVIDER_TJU

    def test_legacy_openai_compat_normalized_to_custom(self):
        mgr = self._mgr()
        mgr.save_config({"enabled": True, "provider": "openai-compatible",
                         "base_url": "https://x/v1", "model": "m",
                         "api_key_env": ""})
        assert mgr.load_config()["provider"] == PROVIDER_CUSTOM


# ============================================================
# 2. 配置保存（DeepSeek 示例）
# ============================================================

class TestConfigSave(_IsolatedCase):
    def test_deepseek_config_example_roundtrip(self):
        """任务书示例：provider=deepseek, base_url=https://api.deepseek.com,
        model=deepseek-chat。"""
        mgr = self._mgr()
        mgr.save_config({
            "enabled": True,
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key_env": "DEEPSEEK_API_KEY",
        })
        mgr.save_api_key("sk-deepseek-example")
        cfg = mgr.load_config()
        assert cfg["provider"] == "deepseek"
        assert cfg["base_url"] == "https://api.deepseek.com"
        assert cfg["model"] == "deepseek-chat"

        pc = mgr.provider_config()
        assert pc["provider"] == "deepseek"
        assert pc["model"] == "deepseek-chat"
        assert pc["api_key"] == "sk-deepseek-example"

    def test_provider_config_shape_stable(self):
        """provider_config 结构不变（SummaryService/ReportService 零改动依赖）。"""
        mgr = self._mgr()
        mgr.set_from_settings(PROVIDER_DEEPSEEK, "", "", "sk-x")
        pc = mgr.provider_config()
        for key in ("enabled", "provider", "base_url", "model", "api_key_env"):
            assert key in pc
        assert pc["enabled"] is True


# ============================================================
# 3. Key 隔离
# ============================================================

class TestKeyIsolation(_IsolatedCase):
    def test_switch_provider_keeps_key_out_of_config_json(self):
        mgr = self._mgr()
        mgr.set_from_settings(PROVIDER_DEEPSEEK, "", "", "secret-deepseek-key")
        raw = config_path().read_text(encoding="utf-8")
        assert "secret-deepseek-key" not in raw
        assert '"api_key"' not in raw
        data = json.loads(raw)
        assert set(data.keys()) <= {
            "enabled", "provider", "base_url", "model", "api_key_env",
            "credential_backend"}

    def test_key_stored_via_credential_store_not_config(self):
        mgr = self._mgr()
        mgr.set_from_settings(PROVIDER_TJU, "", "", "kr-key-multi")
        assert mgr.get_api_key() == "kr-key-multi"
        # key 在 mock keyring，不在 config 文件
        assert "kr-key-multi" not in config_path().read_text(encoding="utf-8")

    def test_key_survives_provider_switch(self):
        """切换 provider（仅改配置）不应清空已保存的 key。"""
        mgr = self._mgr()
        mgr.set_from_settings(PROVIDER_TJU, "", "", "shared-key-1")
        assert mgr.get_api_key() == "shared-key-1"
        # 切到 deepseek（保留 key）
        mgr.set_from_settings(PROVIDER_DEEPSEEK, "", "", None)
        assert mgr.get_api_key() == "shared-key-1"

    def test_api_key_env_is_provider_specific(self):
        mgr = self._mgr()
        mgr.set_from_settings(PROVIDER_DEEPSEEK, "", "", "sk-x")
        assert mgr.load_config()["api_key_env"] == "DEEPSEEK_API_KEY"
        mgr.set_from_settings(PROVIDER_TJU, "", "", "sk-y")
        assert mgr.load_config()["api_key_env"] == "TJU_INFO_LLM_API_KEY"


# ============================================================
# 4. SettingsDialog 自动填充
# ============================================================

class TestSettingsDialogProvider(_IsolatedCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self):
        from tju_info_retrieval.ui.settings_dialog import SettingsDialog
        return SettingsDialog(manager=self._mgr())

    def test_three_provider_options(self):
        dlg = self._dialog()
        labels = [dlg.combo_service.itemText(i)
                  for i in range(dlg.combo_service.count())]
        assert labels == ["天津大学 LLM", "DeepSeek API",
                          "自定义 OpenAI Compatible"]
        ids = [dlg.combo_service.itemData(i)
               for i in range(dlg.combo_service.count())]
        assert ids == ["tju_llm", "deepseek", "custom"]
        dlg.deleteLater()

    def test_select_deepseek_autofills(self):
        dlg = self._dialog()
        idx = dlg.combo_service.findData("deepseek")
        dlg.combo_service.setCurrentIndex(idx)     # 触发 _on_service_changed
        assert dlg.input_base_url.text() == "https://api.deepseek.com"
        assert dlg.input_model.text() == "deepseek-chat"
        dlg.deleteLater()

    def test_select_tju_autofills(self):
        dlg = self._dialog()
        dlg.combo_service.setCurrentIndex(
            dlg.combo_service.findData("tju_llm"))
        assert dlg.input_base_url.text() == "https://ai.tju.edu.cn/api/v3"
        assert dlg.input_model.text() == "tju-llm"
        dlg.deleteLater()

    def test_select_custom_clears_for_manual_entry(self):
        dlg = self._dialog()
        dlg.combo_service.setCurrentIndex(
            dlg.combo_service.findData("custom"))
        assert dlg.input_base_url.text() == ""
        assert dlg.input_model.text() == ""
        dlg.deleteLater()

    def test_save_deepseek_from_dialog(self):
        dlg = self._dialog()
        dlg.combo_service.setCurrentIndex(
            dlg.combo_service.findData("deepseek"))
        dlg.input_api_key.setText("dlg-deepseek-key")
        dlg._on_save()
        mgr = self._mgr()
        assert mgr.load_config()["provider"] == "deepseek"
        assert mgr.load_config()["base_url"] == "https://api.deepseek.com"
        assert mgr.load_config()["model"] == "deepseek-chat"
        assert mgr.get_api_key() == "dlg-deepseek-key"
        dlg.deleteLater()


# ============================================================
# 5. 无侵入：SummaryService / ReportService 无需修改
# ============================================================

class TestNoServiceChange(_IsolatedCase):
    def test_summary_service_consumes_deepseek_config(self):
        """SummaryService 接受 provider_config() 结果，无需任何改动。"""
        from tju_info_retrieval.services.summary_service import SummaryService

        mgr = self._mgr()
        mgr.set_from_settings(PROVIDER_DEEPSEEK, "", "", "sk-x")
        service = SummaryService(config=mgr.provider_config())
        assert service.enhanced_configured is True

    def test_openai_compatible_provider_accepts_deepseek_config(self):
        """唯一调用链 OpenAICompatibleProvider 直接消费 DeepSeek 配置。"""
        from tju_info_retrieval.services.llm_provider import (
            OpenAICompatibleProvider,
        )

        mgr = self._mgr()
        mgr.set_from_settings(PROVIDER_DEEPSEEK, "", "", "sk-x")
        client = OpenAICompatibleProvider(mgr.provider_config())
        assert client.enabled is True
        assert client.model == "deepseek-chat"
        assert client.provider_name == "deepseek"


if __name__ == "__main__":
    unittest.main(verbosity=2)
