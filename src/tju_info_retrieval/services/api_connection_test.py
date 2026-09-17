"""AI 连接测试（v0.18 Phase 2.8-A）。

设置界面“测试连接”的后台执行体：短 prompt 调 OpenAICompatibleProvider，
按类别返回结果（不阻塞 GUI 主线程，由调用方放入 QThread）。

错误分类（禁止统一显示“连接失败”）：
- config      配置错误（未启用/缺 base_url/model）
- credential  Key 错误（401/403 或凭据缺失）
- network     网络错误（连接失败/DNS）
- timeout     超时
- model       模型不存在（400/404）
- format      返回格式错误（非 Chat Completions 结构）
- server      服务器错误（5xx 及其它）
"""
from __future__ import annotations

import time

from tju_info_retrieval.services.api_config_manager import APIConfigManager
from tju_info_retrieval.services.llm_provider import (
    LLMConfigError,
    LLMHTTPError,
    LLMNetworkError,
    LLMParseError,
    LLMTimeoutError,
    OpenAICompatibleProvider,
)

TEST_PROMPT = "Reply with exactly: CONNECTIVITY_OK"
CATEGORY_LABELS = {
    "config": "配置错误",
    "credential": "Key 错误",
    "network": "网络错误",
    "timeout": "连接超时",
    "model": "模型不存在",
    "format": "返回格式错误",
    "server": "服务器错误",
    "ok": "连接成功",
}


def test_connection(
    manager: APIConfigManager | None = None,
    provider_cfg: dict | None = None,
) -> dict:
    """执行一次连接测试，返回结果 dict（永不抛错）。

    返回结构：
    {
      "ok": bool,
      "category": str,          # 见 CATEGORY_LABELS
      "label": str,             # 用户可读分类
      "model": str,             # 成功时返回 model
      "latency_s": float | None,
      "http_status": int | None,
      "detail": str,            # 简短补充（不含 key）
    }
    """
    manager = manager or APIConfigManager()
    cfg = provider_cfg if provider_cfg is not None else manager.provider_config()
    result: dict = {
        "ok": False,
        "category": "config",
        "label": CATEGORY_LABELS["config"],
        "model": str(cfg.get("model") or ""),
        "latency_s": None,
        "http_status": None,
        "detail": "",
    }
    if not cfg.get("enabled"):
        result["detail"] = "AI 增强分析未启用"
        return result
    if not str(cfg.get("base_url") or "").strip():
        result["detail"] = "缺少 API 地址"
        return result
    if not str(cfg.get("model") or "").strip():
        result["detail"] = "缺少模型名称"
        return result
    if not str(cfg.get("api_key") or "").strip():
        result["category"] = "credential"
        result["label"] = CATEGORY_LABELS["credential"]
        result["detail"] = "缺少 API Key"
        return result

    provider = OpenAICompatibleProvider(cfg)
    started = time.perf_counter()
    try:
        text = provider.complete(TEST_PROMPT, timeout_s=30.0)
        result["ok"] = True
        result["category"] = "ok"
        result["label"] = CATEGORY_LABELS["ok"]
        result["latency_s"] = round(time.perf_counter() - started, 2)
        result["http_status"] = 200
        result["detail"] = (text or "").strip()[:80]
    except LLMConfigError as exc:
        result["category"] = "config"
        result["label"] = CATEGORY_LABELS["config"]
        result["detail"] = str(exc)
    except LLMTimeoutError as exc:
        result["category"] = "timeout"
        result["label"] = CATEGORY_LABELS["timeout"]
        result["detail"] = str(exc)
    except LLMNetworkError as exc:
        result["category"] = "network"
        result["label"] = CATEGORY_LABELS["network"]
        result["detail"] = str(exc)
    except LLMHTTPError as exc:
        result["http_status"] = exc.status_code
        if exc.status_code in (401, 403):
            result["category"] = "credential"
            result["label"] = CATEGORY_LABELS["credential"]
        elif exc.status_code in (400, 404):
            result["category"] = "model"
            result["label"] = CATEGORY_LABELS["model"]
        else:
            result["category"] = "server"
            result["label"] = CATEGORY_LABELS["server"]
        result["detail"] = f"HTTP {exc.status_code}"
    except LLMParseError as exc:
        result["category"] = "format"
        result["label"] = CATEGORY_LABELS["format"]
        result["detail"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - 兜底分类
        result["category"] = "server"
        result["label"] = CATEGORY_LABELS["server"]
        result["detail"] = type(exc).__name__
    return result