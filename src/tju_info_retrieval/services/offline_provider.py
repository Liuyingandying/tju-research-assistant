"""Offline Summary Provider（v0.18 Phase 2.7-A）。

包装既有 OfflineSummaryEngine：
- 无 API / 无网络 / 证据严格约束 / 不产生新事实；
- 输出与 v0.17 完全一致（StructuredSummary 四字段 + source_basis）；
- 供 SummaryService 在 basic 模式下调用，保证基础模式零行为变化。
"""
from __future__ import annotations

from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.models.summary import StructuredSummary
from tju_info_retrieval.services.offline_summary import OfflineSummaryEngine
from tju_info_retrieval.services.summary_provider import (
    EvidenceBundle,
    SummaryContext,
)


class OfflineProvider:
    """基础模式 Provider：直接委托 OfflineSummaryEngine。

    接口与未来 EnhancedProvider 对齐（summarize(evidence, context)），
    但忽略 context（离线整理不依赖用户方向），证据仍来自 result 本身。
    """

    name = "offline"

    def __init__(self, engine: OfflineSummaryEngine | None = None) -> None:
        self._engine = engine or OfflineSummaryEngine()

    def summarize(
        self,
        evidence: EvidenceBundle,
        context: SummaryContext,
    ) -> StructuredSummary:
        result = evidence.result
        if result is None:
            raise ValueError("OfflineProvider 需要 SearchResult 证据")
        return self._engine.summarize(result)

    def summarize_result(self, result: SearchResult) -> StructuredSummary:
        """便捷入口：直接按 SearchResult 整理（保持既有调用形态）。"""
        return self._engine.summarize(result)