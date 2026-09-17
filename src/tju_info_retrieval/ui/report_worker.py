"""后台调研报告生成 Worker（v0.18 Phase 2.9-C-E-D）。

LLM 报告生成可能耗时数秒到数十秒，禁止阻塞 Qt 主线程：

- 本 worker 在独立 QThread 中执行 `ReportService.generate_report()`；
- 信号：progress(int, int, str) / succeeded(dict=ReportGenerationResult.to_dict)
  / failed(str) / finished；
- `ReportService.generate_report` 设计上不抛异常（任何失败都降级为基础报告），
  `failed` 仅是兜底（如 Service 未注入）。

与 `SummaryWorker` / `PaperEvidenceWorker` 并存，互不触碰生命周期；
single-flight 由主窗口在提交侧保证。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)


class ReportWorker(QObject):
    """后台调研报告生成（一次一个请求）。"""

    progress = Signal(int, int, str)   # 已完成, 总数, 阶段文案
    succeeded = Signal(dict)           # ReportGenerationResult.to_dict()
    failed = Signal(str)               # 兜底错误消息（正常路径不触发）
    finished = Signal()

    def __init__(self, service=None) -> None:
        super().__init__()
        self._service = service

    def set_service(self, service) -> None:
        """注入 ReportService（主线程调用；经队列信号到达 worker 线程前完成）。"""
        self._service = service

    @Slot(list, str)
    def run_report(self, paper_ids: list, title: str = "") -> None:
        """在 worker 线程执行报告生成并发出结果信号。"""
        total = max(len(list(paper_ids or [])), 1)
        try:
            if self._service is None:
                raise RuntimeError("ReportService 未注入")
            self.progress.emit(0, total, "读取论文库并组装报告输入…")
            result = self._service.generate_report(
                list(paper_ids or []), title or None)
            self.progress.emit(total, total, "完成")
            self.succeeded.emit(result.to_dict())
        except Exception as exc:  # noqa: BLE001 - 兜底不崩溃
            # 日志只记异常类型，不打印异常字符串（可能含认证信息）
            logger.warning("调研报告生成异常: %s", type(exc).__name__)
            self.failed.emit("调研报告生成失败")
        finally:
            self.finished.emit()
