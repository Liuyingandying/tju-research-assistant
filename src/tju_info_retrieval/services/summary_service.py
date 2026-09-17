"""SummaryService 门面（v0.18 Phase 2.7-A / 2.7-B）。

按模式路由到 Provider：
- basic   → OfflineProvider（包装 OfflineSummaryEngine，输出与 v0.17 完全一致）；
- enhanced → LLMEnhancedProvider（OpenAI-compatible，默认关闭）或注入的
  MockEnhancedProvider（测试/演示）；未配置返回 not_configured 降级占位。

设计约束：
- 不修改 SearchService / Adapter / Evidence 链 / OfflineSummaryEngine；
- 增强模式默认关闭（配置 enabled=false），未配置绝不崩溃；
- 失败时（除占位降级外）回退 basic 语义由调用方决定，本门面只负责路由。
"""
from __future__ import annotations

from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.enhanced_provider import (
    LLMEnhancedProvider,
    MockEnhancedProvider,
    not_configured_summary,
)
from tju_info_retrieval.services.llm_provider import (
    OpenAICompatibleProvider,
    load_provider_config,
)
from tju_info_retrieval.services.offline_provider import OfflineProvider
from tju_info_retrieval.services.summary_provider import (
    EvidenceBundle,
    SummaryContext,
    SummaryProvider,
)

MODE_BASIC = "basic"
MODE_ENHANCED = "enhanced"
MODES = (MODE_BASIC, MODE_ENHANCED)


class SummaryService:
    """汇总整理门面：根据模式选择 Provider 并组装输入。"""

    def __init__(
        self,
        offline_provider: OfflineProvider | None = None,
        enhanced_provider: SummaryProvider | None = None,
        enhanced_configured: bool | None = None,
        config: dict | None = None,
    ) -> None:
        self._offline = offline_provider or OfflineProvider()
        if enhanced_provider is not None:
            # 显式注入（测试用 Mock / 演示）优先
            self._enhanced = enhanced_provider
            self._enhanced_configured = (
                True if enhanced_configured is None else enhanced_configured
            )
            return
        # 生产路径：从配置读取；enabled=false（默认）→ 未配置降级
        cfg = config if config is not None else load_provider_config()
        self._llm_client = OpenAICompatibleProvider(cfg)
        if self._llm_client.enabled:
            self._enhanced = LLMEnhancedProvider(llm_client=self._llm_client)
            self._enhanced_configured = True
        else:
            self._enhanced = LLMEnhancedProvider(llm_client=self._llm_client)
            self._enhanced_configured = False

    @property
    def enhanced_configured(self) -> bool:
        return self._enhanced_configured

    def set_enhanced_configured(self, configured: bool) -> None:
        """运行期切换增强是否可用（UI 模式切换/配置变化时调用）。"""
        self._enhanced_configured = configured

    def summarize(
        self,
        mode: str,
        result: SearchResult,
        user_research_direction: str = "",
        research_profile=None,
    ) -> object:
        """按模式整理单条结果。

        返回：
        - basic → StructuredSummary（与 v0.17 完全一致）；
        - enhanced → EnhancedSummary（Mock 或降级占位）。
        """
        if mode not in MODES:
            raise ValueError(
                f"未知整理模式 {mode!r}；仅支持 {MODES}"
            )
        if mode == MODE_BASIC:
            return self._offline.summarize_result(result)

        # enhanced 分支
        if not self._enhanced_configured:
            return not_configured_summary()
        evidence = EvidenceBundle.from_result(result)
        context = SummaryContext.from_result(
            result, user_research_direction=user_research_direction,
            research_profile=research_profile)
        return self._enhanced.summarize(evidence, context)

    def summarize_with_evidence(
        self,
        mode: str,
        evidence: EvidenceBundle,
        context: SummaryContext,
    ) -> object:
        """按模式整理（显式传入证据/上下文；供 future LLM 组装复用）。

        basic 分支仍以 evidence.result 为准（与 summarize 行为一致）。
        """
        if mode not in MODES:
            raise ValueError(f"未知整理模式 {mode!r}；仅支持 {MODES}")
        if mode == MODE_BASIC:
            result = evidence.result
            if result is None:
                raise ValueError("basic 模式需要 SearchResult 证据")
            return self._offline.summarize_result(result)
        if not self._enhanced_configured:
            return not_configured_summary()
        return self._enhanced.summarize(evidence, context)