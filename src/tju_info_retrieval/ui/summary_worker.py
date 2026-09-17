"""后台增强分析 Worker（v0.18 Phase 2.7-B）。

真实 LLM API 可能耗时 5-30 秒，禁止阻塞 Qt 主线程：
- 本 worker 在独立 QThread 中执行 SummaryService.summarize(enhanced)；
- 信号：started / succeeded(EnhancedSummary dict) / failed(str) / finished / cancelled；
- 支持取消（请求侧置标志，线程内检查；requests 超时仍由 provider 控制）。

与 PaperEvidenceWorker 并存，不触碰 SearchWorker 生命周期。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal, Slot

from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.summary_service import MODE_ENHANCED, SummaryService

logger = logging.getLogger(__name__)


class SummaryWorker(QObject):
    """后台增强分析执行（一次一个请求；single-flight 由主窗口保证）。"""

    succeeded = Signal(dict)      # EnhancedSummary.to_dict()
    failed = Signal(str)          # 用户可读错误消息
    finished = Signal()
    cancelled = Signal()

    def __init__(self, service: SummaryService | None = None) -> None:
        super().__init__()
        self._service = service or SummaryService()
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot(object, str, object)
    def run_enhanced(
        self,
        result: SearchResult,
        user_research_direction: str,
        research_profile=None,
    ) -> None:
        """在 worker 线程执行增强分析并发出结果信号。

        v0.18 Phase 2.9-A：research_profile（用户科研画像）作为上下文注入，
        与 Evidence 分离。
        """
        self._cancel_requested = False
        try:
            if self._cancel_requested:
                self.cancelled.emit()
                return
            summary = self._service.summarize(
                MODE_ENHANCED,
                result,
                user_research_direction=user_research_direction,
                research_profile=research_profile,
            )
            if self._cancel_requested:
                self.cancelled.emit()
                return
            self.succeeded.emit(summary.to_dict())
        except Exception as exc:  # noqa: BLE001 - 兜底不崩溃
            # 日志只记异常类型，绝不输出异常字符串（requests 异常可能
            # 携带含 Authorization header 的 PreparedRequest）。
            logger.warning("增强分析异常: %s", type(exc).__name__)
            self.failed.emit("增强分析请求失败")
        finally:
            self.finished.emit()