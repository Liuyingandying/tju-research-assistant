"""AI Provider 注册表（v0.18 Phase 2.11-A Phase B）。

多 Provider 配置的**单一事实来源**：只提供 provider 列表 / 默认配置 /
endpoint 模板 / model 模板等纯数据，不持有任何状态、不做任何网络调用。

内置三种 provider：

| id        | label                      | default_base_url                  | default_model  |
|-----------|----------------------------|-----------------------------------|----------------|
| tju_llm   | 天津大学 LLM                | https://ai.tju.edu.cn/api/v3      | tju-llm        |
| deepseek  | DeepSeek API               | https://api.deepseek.com           | deepseek-chat  |
| custom    | 自定义 OpenAI Compatible   | （空，由用户填写）                 | （空，由用户填写） |

设计约束：

- **不新增第二套 AI 调用链**：本模块只产出 `base_url` / `model` 等配置值，
  实际调用仍经 `OpenAICompatibleProvider`（OpenAI-compatible Chat Completions）；
- **向后兼容**：`normalize_provider` 把 2.8-A 时代的旧 provider id
  （`tju` / `openai-compatible`）平滑映射到新 id。
"""

from __future__ import annotations

# Provider id 常量
PROVIDER_TJU = "tju_llm"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_CUSTOM = "custom"

# 展示顺序（设置页下拉框顺序）
PROVIDER_IDS: tuple[str, ...] = (
    PROVIDER_TJU,
    PROVIDER_DEEPSEEK,
    PROVIDER_CUSTOM,
)

# endpoint 模板：统一 OpenAI-compatible Chat Completions
_ENDPOINT_TEMPLATE = "{base_url}/chat/completions"

# 旧 id → 新 id（2.8-A 平滑迁移）
_LEGACY_PROVIDER_MAP: dict[str, str] = {
    "tju": PROVIDER_TJU,
    "openai-compatible": PROVIDER_CUSTOM,   # 通用 OpenAI-compatible 归入 custom
    "openai": PROVIDER_CUSTOM,
}

_PROVIDERS: dict[str, dict] = {
    PROVIDER_TJU: {
        "id": PROVIDER_TJU,
        "label": "天津大学 LLM",
        "default_base_url": "https://ai.tju.edu.cn/api/v3",
        "default_model": "tju-llm",
        "api_key_env": "TJU_INFO_LLM_API_KEY",
        "endpoint_template": _ENDPOINT_TEMPLATE,
    },
    PROVIDER_DEEPSEEK: {
        "id": PROVIDER_DEEPSEEK,
        "label": "DeepSeek API",
        "default_base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "endpoint_template": _ENDPOINT_TEMPLATE,
    },
    PROVIDER_CUSTOM: {
        "id": PROVIDER_CUSTOM,
        "label": "自定义 OpenAI Compatible",
        "default_base_url": "",
        "default_model": "",
        "api_key_env": "TJU_INFO_LLM_API_KEY",
        "endpoint_template": _ENDPOINT_TEMPLATE,
    },
}


def is_known_provider(provider_id) -> bool:
    """是否为注册表内置的 provider id。"""
    return str(provider_id or "") in _PROVIDERS


def normalize_provider(provider_id) -> str:
    """归一 provider id：旧 id 平滑映射；未知 id 原样返回（不回退到 custom）。

    示例：
        "tju" → "tju_llm"
        "openai-compatible" → "custom"
        "deepseek" → "deepseek"
        "something-else" → "something-else"
    """
    value = str(provider_id or "").strip()
    return _LEGACY_PROVIDER_MAP.get(value, value)


def provider_entry(provider_id) -> dict:
    """返回 provider 完整元数据（副本）；未知 id → 空 dict。"""
    pid = normalize_provider(provider_id)
    entry = _PROVIDERS.get(pid)
    return dict(entry) if entry else {}


def provider_label(provider_id) -> str:
    """provider 中文显示名；未知 id → 原 id 字符串。"""
    entry = _PROVIDERS.get(normalize_provider(provider_id))
    return str(entry["label"]) if entry else str(provider_id or "")


def default_base_url(provider_id) -> str:
    entry = _PROVIDERS.get(normalize_provider(provider_id))
    return str(entry["default_base_url"]) if entry else ""


def default_model(provider_id) -> str:
    entry = _PROVIDERS.get(normalize_provider(provider_id))
    return str(entry["default_model"]) if entry else ""


def default_api_key_env(provider_id) -> str:
    entry = _PROVIDERS.get(normalize_provider(provider_id))
    return str(entry["api_key_env"]) if entry else ""


def endpoint_template(provider_id) -> str:
    """endpoint 模板（当前统一为 OpenAI-compatible Chat Completions）。"""
    entry = _PROVIDERS.get(normalize_provider(provider_id))
    return str(entry["endpoint_template"]) if entry else _ENDPOINT_TEMPLATE


def provider_options() -> list[tuple[str, str]]:
    """设置页下拉选项 [(label, id), ...]，按 PROVIDER_IDS 顺序。"""
    return [(_PROVIDERS[pid]["label"], pid) for pid in PROVIDER_IDS]


__all__ = [
    "PROVIDER_TJU",
    "PROVIDER_DEEPSEEK",
    "PROVIDER_CUSTOM",
    "PROVIDER_IDS",
    "is_known_provider",
    "normalize_provider",
    "provider_entry",
    "provider_label",
    "default_base_url",
    "default_model",
    "default_api_key_env",
    "endpoint_template",
    "provider_options",
]
