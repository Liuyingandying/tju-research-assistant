"""Summary Provider 抽象（v0.18 Phase 2.7-A）。

定义未来可接入的 SummaryProvider 协议：
- 当前实现：OfflineProvider（包装 OfflineSummaryEngine，基础模式）；
- MockEnhancedProvider（增强模式占位，禁止调用 API）；
- 未来：OpenAI-compatible LLM Provider（TJU LLM / DeepSeek / Qwen 等）。

本阶段只定义接口 + 两个实现（offline / mock），不接任何真实 API。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from tju_info_retrieval.models.result import SearchResult


@dataclass
class SummaryContext:
    """Provider 所需的上下文（与 SearchResult 分离，便于未来 LLM 输入组装）。

    增强模式输入：
    - title / authors / abstract / content / metadata：来自 SearchResult 与
      Evidence 链（本阶段仅透传已存在字段，不新增取证链）；
    - user_research_direction：用户研究方向（可空）。
    """

    title: str = ""
    authors: str = ""
    abstract: str = ""
    content: str = ""
    metadata: dict = field(default_factory=dict)
    user_research_direction: str = ""
    artifact_type: str = "paper"
    # v0.18 Phase 2.9-A：用户科研画像（用户上下文，不进 Evidence）
    research_profile: object | None = None

    @classmethod
    def from_result(
        cls,
        result: SearchResult,
        user_research_direction: str = "",
        research_profile=None,
    ) -> "SummaryContext":
        metadata = dict(result.artifact_metadata or {})
        return cls(
            title=(result.title or ""),
            authors=", ".join(result.authors or []),
            abstract=(result.abstract or ""),
            content=(getattr(result, "_news_content_head", None) or ""),
            metadata=metadata,
            user_research_direction=user_research_direction or "",
            artifact_type=getattr(result, "artifact_type", "paper") or "paper",
            research_profile=research_profile,
        )


@dataclass
class EvidenceBundle:
    """Provider 输入：现有证据文本块（来源标签 + 文本），与 OfflineSummary
    的 _collect_evidence 同构，供增强 provider 组装 prompt 时复用。"""

    blocks: list[tuple[str, str]] = field(default_factory=list)
    result: SearchResult | None = None

    @classmethod
    def from_result(cls, result: SearchResult) -> "EvidenceBundle":
        from tju_info_retrieval.services.offline_summary import _collect_evidence

        return cls(blocks=_collect_evidence(result), result=result)


@runtime_checkable
class SummaryProvider(Protocol):
    """Summary Provider 协议（未来可接入 OpenAI-compatible API）。

    约定：
    - summarize 必须同步返回结构化结果（离线同步 / 未来 LLM 由调用方
      决定是否后台线程化，协议本身不强制异步）；
    - 输出必须携带 provider 标识，供 UI 展示来源；
    - 失败时抛 ProviderError（或返回可辨识的“不可用”状态），由
      SummaryService 统一降级。
    """

    name: str

    def summarize(
        self,
        evidence: EvidenceBundle,
        context: SummaryContext,
    ) -> object:
        """输入现有证据与上下文，返回结构化整理结果。

        返回类型：offline → StructuredSummary；enhanced → EnhancedSummary。
        """
        ...