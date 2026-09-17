#!/usr/bin/env python3
"""正式测试：v0.18 Phase 2.8-B APIConfigManager 凭据安全。

- keyring 用 mock（不触碰真实凭据库）；
- 配置/凭据目录用 TEST_USER_DATA_ROOT 隔离；
- 覆盖：keyring 存取、DPAPI fallback（Windows 真实 DPAPI）、
  禁止明文 secret、无安全 backend 时 memory-only、
  配置 JSON 不含 api_key、重启持久化、旧 secret.json 不回读。
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

from tju_info_retrieval import app_paths
from tju_info_retrieval.services import credential_store as cs
from tju_info_retrieval.services.api_config_manager import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    APIConfigManager,
    config_path,
)
from tju_info_retrieval.services.credential_store import (
    CredentialStore,
    credential_file,
)


class _FakeKeyring:
    """内存 keyring（避免触碰真实凭据库）。"""

    def __init__(self):
        self._store = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        self._store.pop((service, username), None)


class _IsolatedCase(unittest.TestCase):
    """公共基座：隔离目录 + keyring mock（子类可再 patch DPAPI）。"""

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

    def _fresh_manager(self):
        """新建 manager + 新 CredentialStore（模拟重启）。"""
        return APIConfigManager(credential_store=CredentialStore())


class TestKeyringBackend(_IsolatedCase):
    """keyring 存取（首选后端）。"""

    def test_save_and_get_via_keyring(self):
        mgr = self._fresh_manager()
        self.assertEqual(mgr.save_api_key("kr-key-123"), "keyring")
        self.assertEqual(mgr.get_api_key(), "kr-key-123")
        # 不落任何明文文件
        self.assertFalse(credential_file().exists())

    def test_clear_key(self):
        mgr = self._fresh_manager()
        mgr.save_api_key("kr-key-123")
        mgr.clear_api_key()
        self.assertEqual(mgr.get_api_key(), "")

    def test_backend_persisted_in_config(self):
        mgr = self._fresh_manager()
        mgr.save_api_key("kr-key-123")
        cfg = mgr.load_config()
        self.assertEqual(cfg.get("credential_backend"), "keyring")

    def test_restart_persistence(self):
        """保存 → 新 manager（模拟重启）→ 凭据仍可读。"""
        m1 = self._fresh_manager()
        m1.set_from_settings("tju", "https://x/v1", "tju-llm", "persist-key")
        self.assertTrue(m1.is_configured())
        m2 = self._fresh_manager()
        self.assertTrue(m2.is_configured())
        self.assertEqual(m2.get_api_key(), "persist-key")
        m2.clear_api_key()


class TestDPAPIFallback(_IsolatedCase):
    """DPAPI fallback（Windows 真实 DPAPI；非 Windows 跳过）。"""

    def setUp(self):
        super().setUp()
        if os.name != "nt":
            self.skipTest("DPAPI 仅 Windows")
        # keyring 不可用 → DPAPI
        kr_off = mock.patch.object(cs, "_keyring_available", return_value=False)
        kr_off.start()
        self.addCleanup(kr_off.stop)

    def test_dpapi_roundtrip_no_plaintext(self):
        mgr = self._fresh_manager()
        self.assertEqual(mgr.save_api_key("dpapi-key-456"), "dpapi")
        self.assertEqual(mgr.get_api_key(), "dpapi-key-456")
        # credential.dat 存在且不含明文
        self.assertTrue(credential_file().exists())
        raw = credential_file().read_bytes()
        self.assertNotIn(b"dpapi-key-456", raw)
        self.assertNotIn(b"dpapi-key-456",
                         credential_file().read_text(encoding="utf-8",
                                                     errors="ignore")
                         .encode("utf-8"))

    def test_dpapi_restart_persistence(self):
        m1 = self._fresh_manager()
        m1.save_api_key("dpapi-key-456")
        m2 = self._fresh_manager()
        self.assertEqual(m2.get_api_key(), "dpapi-key-456")
        m2.clear_api_key()
        self.assertEqual(m2.get_api_key(), "")
        self.assertFalse(credential_file().exists())


class TestMemoryOnly(_IsolatedCase):
    """无安全 backend → memory-only（不落盘）。"""

    def setUp(self):
        super().setUp()
        kr_off = mock.patch.object(cs, "_keyring_available", return_value=False)
        dpapi_off = mock.patch.object(cs, "_dpapi_available", return_value=False)
        kr_off.start()
        dpapi_off.start()
        self.addCleanup(kr_off.stop)
        self.addCleanup(dpapi_off.stop)

    def test_memory_only_no_file(self):
        mgr = self._fresh_manager()
        self.assertEqual(mgr.save_api_key("mem-key-789"), "memory")
        self.assertEqual(mgr.get_api_key(), "mem-key-789")
        self.assertFalse(credential_file().exists())

    def test_memory_only_not_persistent(self):
        mgr = self._fresh_manager()
        mgr.save_api_key("mem-key-789")
        self.assertFalse(mgr.credential_persistent)
        # “重启”（新实例）后不可用
        m2 = self._fresh_manager()
        self.assertEqual(m2.get_api_key(), "")
        self.assertFalse(m2.is_configured())


class TestNoPlaintextSecret(_IsolatedCase):
    """禁止明文 API Key 写入 JSON/txt。"""

    def test_config_json_never_contains_key(self):
        mgr = self._fresh_manager()
        mgr.save_api_key("leak-check-key")
        mgr.save_config({"enabled": True, "provider": "tju",
                         "base_url": "https://x", "model": "m",
                         "api_key": "leak-check-key"})  # 恶意传入必须被剥离
        raw = config_path().read_text(encoding="utf-8")
        self.assertNotIn("leak-check-key", raw)
        self.assertNotIn('"api_key"', raw)
        # JSON 仅允许的非敏感字段
        data = json.loads(raw)
        allowed = {"enabled", "provider", "base_url", "model",
                   "api_key_env", "credential_backend"}
        self.assertTrue(set(data.keys()) <= allowed)

    def test_legacy_secret_json_not_read_back(self):
        """2.8-A 时代的明文 secret.json 不再回读（明文 fallback 移除）。"""
        legacy = config_path().parent / "secret.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"api_key": "old-plaintext-key"}),
                          encoding="utf-8")
        mgr = self._fresh_manager()
        # keyring 无记录 → 不得回读明文文件
        self.assertEqual(mgr.get_api_key(), "")

    def test_credential_file_is_not_json_plaintext(self):
        if os.name != "nt":
            self.skipTest("DPAPI 仅 Windows")
        kr_off = mock.patch.object(cs, "_keyring_available", return_value=False)
        kr_off.start()
        self.addCleanup(kr_off.stop)
        mgr = self._fresh_manager()
        mgr.save_api_key("dpapi-not-plain")
        raw = credential_file().read_text(encoding="utf-8", errors="ignore")
        self.assertNotIn("dpapi-not-plain", raw)
        self.assertNotIn("api_key", raw)


class TestConfigState(_IsolatedCase):
    """配置状态与 provider_config 注入。"""

    def test_is_configured_full_chain(self):
        mgr = self._fresh_manager()
        self.assertFalse(mgr.is_configured())
        mgr.set_from_settings("tju", "https://x/v1", "tju-llm", "cfg-key")
        self.assertTrue(mgr.is_configured())

    def test_provider_config_injects_key_memory_only(self):
        mgr = self._fresh_manager()
        mgr.save_api_key("injected-key")
        pc = mgr.provider_config()
        self.assertEqual(pc["api_key"], "injected-key")
        # JSON 无明文
        self.assertNotIn("injected-key", config_path().read_text(encoding="utf-8"))

    def test_defaults(self):
        cfg = APIConfigManager(credential_store=CredentialStore()).load_config()
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["base_url"], DEFAULT_BASE_URL)
        self.assertEqual(cfg["model"], DEFAULT_MODEL)


if __name__ == "__main__":
    unittest.main(verbosity=2)