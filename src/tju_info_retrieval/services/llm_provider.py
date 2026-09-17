"""OpenAI-compatible LLM Provider（v0.18 Phase 2.7-B / 2.7-C）。

通过配置切换目标（TJU LLM / DeepSeek / Qwen / OpenAI 等 OpenAI-compatible
Chat Completions 端点），不绑定任何官方 SDK，仅用 requests。

Secret 管理（v0.18 Phase 2.7-C）：
- 真实 API key 只从环境变量读取：配置中 `api_key_env` 指定环境变量名；
- 禁止在 summary_provider.json 中写入真实 key；
- 旧配置 `api_key` 字段仅作向后兼容回退（不鼓励，example 不含）；
- 凭据缺失 → LLMConfigError（"缺少 API 凭据"），绝不崩溃/回退 basic/打印 key。

日志安全：本模块无日志输出；Authorization header 只在 requests 调用内
构造，任何异常信息均不含 key（详见 enhanced_provider 分类文案）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

# 配置路径（相对项目根；运行时产物，已在 .gitignore）
CONFIG_PATH = Path("runtime") / "summary_provider.json"
EXAMPLE_PATH = Path("runtime") / "summary_provider.example.json"

DEFAULT_TIMEOUT_S = 30.0
CONFIG_FIELDS = ("enabled", "provider", "base_url", "api_key", "model",
                 "api_key_env")


class LLMConfigError(RuntimeError):
    """配置缺失/非法（未启用、缺少 base_url/api_key/model）。"""


class LLMNetworkError(RuntimeError):
    """网络失败（连接/请求异常）。"""


class LLMTimeoutError(RuntimeError):
    """请求超时。"""


class LLMHTTPError(RuntimeError):
    """API 返回非 2xx。"""

    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"LLM API HTTP {status_code}: {detail}")


class LLMParseError(RuntimeError):
    """响应体不符合 Chat Completions 结构或内容不可用。"""


def load_provider_config(path: Path | str | None = None) -> dict:
    """读取 OpenAI-compatible Provider 配置（缺省路径不存在 → 全空默认）。

    返回 dict 至少包含 CONFIG_FIELDS；enabled 默认 False。
    """
    p = Path(path) if path else CONFIG_PATH
    config = {"enabled": False, "provider": "openai-compatible",
              "base_url": "", "api_key": "", "model": "", "api_key_env": ""}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in CONFIG_FIELDS:
                    if key in data:
                        config[key] = data[key]
        except (OSError, ValueError) as exc:
            # 配置损坏 → 按未启用处理（不崩溃，UI 显示未配置）
            config["enabled"] = False
            config["_load_error"] = str(exc)
    return config


class OpenAICompatibleProvider:
    """OpenAI-compatible Chat Completions 客户端（requests 实现）。"""

    def __init__(
        self,
        config: dict | None = None,
        transport=None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        cfg = config if config is not None else load_provider_config()
        self._cfg = dict(cfg)
        self._transport = transport or requests.post
        self._timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.get("enabled"))

    @property
    def model(self) -> str:
        return str(self._cfg.get("model") or "")

    @property
    def provider_name(self) -> str:
        return str(self._cfg.get("provider") or "openai-compatible")

    def _resolve_api_key(self) -> str:
        """解析 API key：api_key_env → 环境变量（首选）；回退旧 api_key 字段。

        不打印、不记录解析结果；缺失返回空串由 validate 报错。
        """
        env_name = str(self._cfg.get("api_key_env") or "").strip()
        if env_name:
            value = os.environ.get(env_name, "").strip()
            if value:
                return value
        # 向后兼容：旧 JSON api_key 字段（example 不再鼓励填写真实 key）
        return str(self._cfg.get("api_key") or "").strip()

    @property
    def api_key_configured(self) -> bool:
        """凭据是否可用（不暴露值本身）。"""
        return bool(self._resolve_api_key())

    @property
    def api_key_env_name(self) -> str:
        return str(self._cfg.get("api_key_env") or "").strip()

    def validate(self) -> None:
        """未启用或缺少必需项 → LLMConfigError。"""
        if not self.enabled:
            raise LLMConfigError("增强分析服务未配置")
        base_url = str(self._cfg.get("base_url") or "").rstrip("/")
        api_key = self._resolve_api_key()
        model = str(self._cfg.get("model") or "")
        if not base_url:
            raise LLMConfigError("增强分析服务未配置：缺少 base_url")
        if not api_key:
            raise LLMConfigError("增强分析服务未配置：缺少 API 凭据")
        if not model:
            raise LLMConfigError("增强分析服务未配置：缺少 model")
        self._base_url = base_url
        self._api_key = api_key
        self._model = model

    def complete(
        self,
        prompt: str,
        timeout_s: float | None = None,
        system_prompt: str = "",
    ) -> str:
        """一次 Chat Completions 调用，返回 assistant 文本。

        失败按分类抛 LLMConfigError / LLMNetworkError / LLMTimeoutError /
        LLMHTTPError / LLMParseError。
        """
        self.validate()
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt or ""},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        try:
            response = self._transport(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise LLMTimeoutError("增强分析超时") from exc
        except requests.RequestException as exc:
            raise LLMNetworkError("增强分析请求失败") from exc
        if not response.ok:
            detail = ""
            try:
                detail = response.text[:200]
            except Exception:  # noqa: BLE001 - 仅取文本片段
                pass
            raise LLMHTTPError(response.status_code, detail)
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMParseError(f"增强分析结果解析失败: {exc}") from exc
        text = (content or "").strip()
        if not text:
            raise LLMParseError("增强分析结果解析失败：空内容")
        return text