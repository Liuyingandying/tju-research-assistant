"""AI 配置管理（v0.18 Phase 2.8-A / 2.8-B 安全收尾）。

统一管理 OpenAI-compatible Provider 配置与 API Key，供普通用户一键配置：
- 配置 JSON（不含 key）：
    开发环境   → runtime/summary_provider.json（向后兼容）
    frozen EXE → %LOCALAPPDATA%\\TJU_Info_Retrieval\\config\\summary_provider.json
- API Key 存储（v0.18 2.8-B 安全收尾，禁止明文落盘）：
    1) keyring / Windows Credential Manager（首选）；
    2) Windows DPAPI 加密本地文件 credential.dat（fallback，无明文）；
    3) 二者均不可用 → 仅进程内存临时使用（memory-only），
       UI 提示“当前系统无法安全保存 API Key，本次仅临时使用”。
- 配置 JSON 只允许 enabled/provider/base_url/model/credential_backend 等
  非敏感字段，严禁 api_key/token/Authorization。
- 与既有 LLM Provider 关系：本模块只负责“配置 + 凭据”，把解析后的 dict
  交给 OpenAICompatibleProvider（Provider 协议零改动）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from tju_info_retrieval import app_paths
from tju_info_retrieval.services.ai_provider_registry import (
    PROVIDER_TJU,
    default_api_key_env,
    default_base_url,
    default_model,
    normalize_provider,
    provider_options,
)
from tju_info_retrieval.services.credential_store import (
    BACKEND_DPAPI,
    BACKEND_KEYRING,
    BACKEND_MEMORY,
    CredentialStore,
)

# keyring 服务名/用户名（兼容 2.8-A 既有存储）
KEYRING_SERVICE = "TJU_Info_Retrieval_AI"
KEYRING_USERNAME = "default"
CONFIG_FILENAME = "summary_provider.json"

# 默认 provider（未显式选择时）→ 天津大学 LLM
DEFAULT_BASE_URL = "https://ai.tju.edu.cn/api/v3"
DEFAULT_MODEL = "tju-llm"
DEFAULT_API_KEY_ENV = "TJU_INFO_LLM_API_KEY"

# 服务类型（设置界面 ComboBox 选项，data 值；由 ai_provider_registry 提供）
# v0.18 Phase 2.11-A：provider id 归一为 tju_llm / deepseek / custom。
SERVICE_TJU = PROVIDER_TJU
SERVICE_CUSTOM = "custom"
# 向后兼容别名：旧 "openai-compatible" 已归一到 custom
SERVICE_OPENAI_COMPAT = SERVICE_CUSTOM
SERVICE_OPTIONS = tuple(provider_options())


def config_dir() -> Path:
    """配置目录：frozen 或测试隔离 → LocalAppData/config；开发态 → runtime/。"""
    if app_paths.is_frozen() or bool(
            os.environ.get(app_paths.TEST_USER_DATA_ROOT_ENV, "").strip()):
        return app_paths.user_data_root() / "config"
    return app_paths.project_root() / "runtime"


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


class APIConfigManager:
    """AI 配置与凭据管理（读取/保存/删除/测试准备）。

    凭据经 CredentialStore：keyring → DPAPI credential.dat → memory-only，
    任何路径均不落明文。
    """

    def __init__(self, credential_store: CredentialStore | None = None) -> None:
        self._credential = credential_store or CredentialStore()

    @property
    def credential_backend(self) -> str:
        """当前凭据后端（keyring/dpapi/memory）。"""
        return self._credential.backend

    @property
    def credential_persistent(self) -> bool:
        return self._credential.is_persistent

    # ---------- 配置（不含 key） ----------

    def load_config(self) -> dict:
        """读取配置 JSON；缺失/损坏返回默认（未启用）。

        provider 字段经 normalize_provider 归一（旧 id 平滑迁移到新 id）。
        """
        default = {
            "enabled": False,
            "provider": PROVIDER_TJU,
            "base_url": DEFAULT_BASE_URL,
            "model": DEFAULT_MODEL,
            "api_key_env": DEFAULT_API_KEY_ENV,
            "credential_backend": BACKEND_MEMORY,
        }
        path = config_path()
        if not path.is_file():
            return default
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return default
            merged = dict(default)
            for key in ("enabled", "provider", "base_url", "model",
                        "api_key_env", "credential_backend"):
                if key in data:
                    merged[key] = data[key]
            # v0.18 Phase 2.11-A：旧 provider id 归一
            merged["provider"] = normalize_provider(merged["provider"]) \
                or PROVIDER_TJU
            return merged
        except (OSError, ValueError):
            return default

    def save_config(self, config: dict) -> Path:
        """保存配置 JSON（只含非 key 字段；api_key 被剥离，绝不写明文）。

        附加 credential_backend（凭据存储方式，非凭据本身）。
        """
        path = config_dir()
        path.mkdir(parents=True, exist_ok=True)
        safe = {k: v for k, v in dict(config).items()
                if k != "api_key" and k != "_load_error"
                and k != "token" and "authorization" not in k.lower()}
        safe.setdefault("credential_backend", self._credential.backend)
        target = path / CONFIG_FILENAME
        target.write_text(
            json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def delete_config(self) -> None:
        """删除配置 JSON（凭据由 clear_api_key 单独处理）。"""
        path = config_path()
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass

    # ---------- API Key（经 CredentialStore，禁止明文落盘） ----------

    def get_api_key(self) -> str:
        """读取 API Key（keyring → DPAPI credential.dat → memory-only）。"""
        return self._credential.get()

    def save_api_key(self, api_key: str) -> str:
        """保存 API Key，返回实际使用的后端标识（keyring/dpapi/memory）。

        memory-only 时不落盘，仅进程内有效。
        """
        api_key = (api_key or "").strip()
        if not api_key:
            return self._credential.backend
        backend = self._credential.save(api_key)
        # 持久化 backend 到配置（供设置界面展示与重启诊断）
        cfg = self.load_config()
        cfg["credential_backend"] = backend
        self.save_config(cfg)
        return backend

    def clear_api_key(self) -> None:
        """清除 API Key（keyring + DPAPI 双清，memory 置空）。"""
        self._credential.clear()

    # ---------- 状态与 Provider 输入 ----------

    def is_configured(self) -> bool:
        """已配置：enabled 且 base_url/model 非空且凭据可用。"""
        cfg = self.load_config()
        if not cfg.get("enabled"):
            return False
        if not str(cfg.get("base_url") or "").strip():
            return False
        if not str(cfg.get("model") or "").strip():
            return False
        return bool(self.get_api_key())

    def provider_config(self) -> dict:
        """生成 OpenAICompatibleProvider 可接受的配置 dict（含解析后的 key）。

        凭据来自 get_api_key()（keyring → DPAPI credential.dat →
        memory-only，均不落明文），JSON 只承载 enabled/provider/base_url/
        model/api_key_env/credential_backend；api_key 字段仅在返回 dict
        中注入，绝不落盘。
        """
        cfg = self.load_config()
        provider_cfg = {
            "enabled": bool(cfg.get("enabled")),
            "provider": str(cfg.get("provider") or PROVIDER_TJU),
            "base_url": str(cfg.get("base_url") or DEFAULT_BASE_URL),
            "model": str(cfg.get("model") or DEFAULT_MODEL),
            "api_key_env": str(cfg.get("api_key_env") or DEFAULT_API_KEY_ENV),
        }
        key = self.get_api_key()
        if key:
            provider_cfg["api_key"] = key  # 仅内存注入
        return provider_cfg

    def set_from_settings(
        self,
        service_type: str,
        base_url: str,
        model: str,
        api_key: str,
        enabled: bool = True,
    ) -> None:
        """设置界面保存入口：写配置 + 写凭据。

        v0.18 Phase 2.11-A：provider id 归一，base_url / model 缺失时
        回退到该 provider 的默认值（DeepSeek 等无需手填即可用）。
        """
        provider = normalize_provider(service_type) or PROVIDER_TJU
        base = (base_url or default_base_url(provider)).strip()
        model_ = (model or default_model(provider)).strip()
        self.save_config({
            "enabled": enabled,
            "provider": provider,
            "base_url": base,
            "model": model_,
            "api_key_env": default_api_key_env(provider),
        })
        if api_key and api_key.strip():
            self.save_api_key(api_key)
        elif api_key is not None and not api_key.strip():
            # 显式空串：视为“仅更新配置，保留既有 key”
            pass