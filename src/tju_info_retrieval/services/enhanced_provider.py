"""Enhanced Summary Provider（v0.18 Phase 2.7-A / 2.7-B）。

- MockEnhancedProvider（2.7-A）：确定性占位输出，provider="mock"；
- LLMEnhancedProvider（2.7-B）：通过 OpenAICompatibleProvider 调用
  可配置的 OpenAI-compatible 端点（TJU LLM / DeepSeek / Qwen / OpenAI），
  输出 EnhancedSummary；任何失败返回固定降级结果，绝不崩溃；
- 增强模式默认关闭（配置 enabled=false 时不创建 LLM Provider）。

设计要点：
- 输出必须标记 provider 与 ai_generated=True（AI 生成内容，不伪装 Evidence）；
- 失败降级：not_configured / 网络 / 超时 / 解析 分类提示。
"""
from __future__ import annotations

from tju_info_retrieval.models.enhanced_summary import (
    ENHANCED_NOT_CONFIGURED_TEXT,
    EnhancedSummary,
)
from tju_info_retrieval.services.prompts.enhanced_summary_prompt import (
    ENHANCED_PROMPT_VERSION,
    profile_version_of,
)
from tju_info_retrieval.services.summary_provider import (
    EvidenceBundle,
    SummaryContext,
)

MOCK_PROVIDER_NAME = "mock"


class MockEnhancedProvider:
    """增强模式占位 Provider（无 API；确定性模板输出）。"""

    name = MOCK_PROVIDER_NAME

    def summarize(
        self,
        evidence: EvidenceBundle,
        context: SummaryContext,
    ) -> EnhancedSummary:
        title = (context.title or "").strip()
        direction = (context.user_research_direction or "").strip()
        abstract = (context.abstract or "").strip()

        # 模板输出：只回显证据中真实存在的文本片段，不产生新事实。
        # 标题/方向为空时给出占位说明，保证任何输入下均可展示。
        background = (
            f"该成果围绕“{title}”展开。"
            if title
            else "该成果的研究背景需结合摘要与正文进一步分析。"
        )
        route = (
            f"技术路线可结合摘要线索：{_excerpt(abstract, 60)}"
            if abstract
            else "技术路线需在获取摘要后进一步分析。"
        )
        if direction:
            relation = (
                f"与研究方向“{direction}”存在主题关联，"
                "建议结合全文核对具体切入点。"
            )
        else:
            relation = "未提供研究方向，暂不评估与用户方向的关系。"
        innovations = (
            "创新点需结合全文与方法部分进一步分析（当前为 Mock 占位输出）。"
        )
        reading = (
            "建议优先阅读摘要与结论部分，再按需获取全文。"
        )
        limitations = (
            "局限性需结合实验数据与对比分析评估（当前为 Mock 占位输出）。"
        )

        return EnhancedSummary(
            research_background=background,
            technical_route=route,
            innovation_points=innovations,
            relation_to_user_direction=relation,
            reading_recommendation=reading,
            limitations=limitations,
            provider=MOCK_PROVIDER_NAME,
            ai_generated=True,
        )


def _excerpt(text: str, limit: int) -> str:
    """取证据文本片段（截断加省略号，保持子串性质）。"""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def not_configured_summary() -> EnhancedSummary:
    """未配置增强 Provider 时的降级占位（固定文案，不崩溃）。"""
    return EnhancedSummary(
        research_background=ENHANCED_NOT_CONFIGURED_TEXT,
        technical_route=ENHANCED_NOT_CONFIGURED_TEXT,
        innovation_points=ENHANCED_NOT_CONFIGURED_TEXT,
        relation_to_user_direction=ENHANCED_NOT_CONFIGURED_TEXT,
        reading_recommendation=ENHANCED_NOT_CONFIGURED_TEXT,
        limitations=ENHANCED_NOT_CONFIGURED_TEXT,
        provider="",
        ai_generated=False,
    )


# 用户可读的失败分类文案（UI 直接展示，不泄露内部异常）
ERROR_MESSAGES = {
    "not_configured": ENHANCED_NOT_CONFIGURED_TEXT,
    "network": "增强分析请求失败",
    "timeout": "增强分析超时",
    "parse": "增强分析结果解析失败",
    "api": "增强分析服务返回错误",
}


class LLMEnhancedProvider:
    """通过 OpenAI-compatible LLM 生成增强分析（Phase 2.7-B）。

    summarize 永不抛错：任何失败返回固定降级 EnhancedSummary
    （对应 ERROR_MESSAGES 文案），由 SummaryService/UI 直接展示。
    """

    name = "llm"

    def __init__(
        self,
        llm_client=None,
        cache=None,
        prompt_builder=None,
        system_prompt: str | None = None,
    ) -> None:
        # 延迟导入避免启动时依赖 requests/配置
        from tju_info_retrieval.services.enhanced_cache import EnhancedSummaryCache
        from tju_info_retrieval.services.llm_provider import OpenAICompatibleProvider
        from tju_info_retrieval.services.prompts.enhanced_summary_prompt import (
            ENHANCED_SYSTEM_PROMPT,
            build_enhanced_summary_prompt,
            parse_enhanced_json,
        )

        self._client = llm_client or OpenAICompatibleProvider()
        self._cache = cache if cache is not None else EnhancedSummaryCache()
        self._build_prompt = prompt_builder or build_enhanced_summary_prompt
        self._system_prompt = system_prompt or ENHANCED_SYSTEM_PROMPT
        self._parse = parse_enhanced_json
        # v0.18 Phase 2.8-C：Prompt 版本进入缓存 key（实质变化即失效旧缓存）
        self._prompt_version = ENHANCED_PROMPT_VERSION

    def summarize(
        self,
        evidence,
        context,
    ) -> EnhancedSummary:
        """生成增强分析；任何失败返回降级结果（不抛错）。

        v0.18 Phase 2.9-A：缓存 key 纳入 profile_version——用户修改
        科研画像后旧 AI 分析自动失效。
        """
        try:
            self._client.validate()
        except Exception as exc:  # noqa: BLE001 - 统一降级
            return self._degraded("not_configured", exc)

        profile_version = profile_version_of(context)
        key = self._cache.key_for(
            context.title, context.abstract,
            self._client.provider_name, self._client.model,
            prompt_version=ENHANCED_PROMPT_VERSION,
            profile_version=profile_version,
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        try:
            prompt = self._build_prompt(context)
            raw = self._client.complete(prompt, system_prompt=self._system_prompt)
            try:
                fields = self._parse(raw)
            except Exception as exc:  # noqa: BLE001 - 解析失败归类为 parse
                from tju_info_retrieval.services.llm_provider import LLMParseError

                raise LLMParseError(f"增强分析结果解析失败: {exc}") from exc
            summary = EnhancedSummary(
                **fields,
                provider=self._client.provider_name,
                ai_generated=True,
            )
            self._cache.put(key, summary)
            return summary
        except Exception as exc:  # noqa: BLE001 - 分类降级
            category = self._classify(exc)
            return self._degraded(category, exc)

    @staticmethod
    def _classify(exc: Exception) -> str:
        from tju_info_retrieval.services.llm_provider import (
            LLMConfigError,
            LLMHTTPError,
            LLMNetworkError,
            LLMParseError,
            LLMTimeoutError,
        )

        if isinstance(exc, LLMConfigError):
            return "not_configured"
        if isinstance(exc, LLMTimeoutError):
            return "timeout"
        if isinstance(exc, LLMParseError):
            return "parse"
        if isinstance(exc, (LLMNetworkError, LLMHTTPError)):
            return "api" if isinstance(exc, LLMHTTPError) else "network"
        return "api"

    @staticmethod
    def _degraded(category: str, exc: Exception) -> EnhancedSummary:
        message = ERROR_MESSAGES.get(category, ERROR_MESSAGES["api"])
        return EnhancedSummary(
            research_background=message,
            technical_route=message,
            innovation_points=message,
            relation_to_user_direction=message,
            reading_recommendation=message,
            limitations=message,
            provider="",
            ai_generated=False,
        )