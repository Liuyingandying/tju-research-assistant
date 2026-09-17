"""详情页多窗口管理器 DetailManager（v0.13 详情页多窗口架构）。

接管 MainWindow 的详情打开请求（原 BrowserWorker.open_detail 路径）：
- 每次打开创建独立 DetailWorker(QThread) + 独立 DetailBrowserSession
  （独立 Edge 进程 / context / page，互不共享，同线程栈内生灭）；
- 同一 URL 防重复打开；并发窗口数有上限；
- shutdown_all 在应用退出时优雅关闭全部详情线程。
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, Signal, Slot

from tju_info_retrieval.ui.detail_worker import DetailWorker

logger = logging.getLogger(__name__)


class DetailManager(QObject):
    """多详情页线程管理器。"""

    detail_opened = Signal(str)
    status = Signal(str)
    error = Signal(str)

    MAX_CONCURRENT_DETAILS = 5

    def __init__(self, storage_state_path=None, parent=None) -> None:
        super().__init__(parent)
        self._storage_state_path = storage_state_path
        self._workers: dict[str, DetailWorker] = {}

    def active_count(self) -> int:
        return sum(1 for w in self._workers.values() if w.isRunning())

    @Slot(str)
    def open_detail(self, url: str) -> None:
        """打开一个详情页窗口（独立线程 + 独立会话）。"""
        if not (url or "").strip():
            return
        existing = self._workers.get(url)
        if existing is not None and existing.isRunning():
            self.status.emit("该论文详情页已在打开列表中")
            return
        if self.active_count() >= self.MAX_CONCURRENT_DETAILS:
            self.error.emit(
                f"详情页窗口已达上限（{self.MAX_CONCURRENT_DETAILS}），"
                "请先关闭部分窗口"
            )
            return
        worker = DetailWorker(url, self._storage_state_path)
        worker.detail_opened.connect(self.detail_opened)
        worker.status.connect(self.status)
        worker.error.connect(self.error)
        worker.finished.connect(lambda u=url: self._workers.pop(u, None))
        self._workers[url] = worker
        worker.start()
        self.status.emit("正在打开详情页...")

    @Slot(str)
    def open_detail_by_title(self, title: str) -> None:
        """万方按需解析（B′）：按标题定位并打开详情页窗口。

        与 open_detail 相同的防重/并发约束；防重键使用标题前缀
        以区分于 URL 键。DetailWorker 以 title 模式运行（url=None）。
        """
        clean = (title or "").strip()
        if not clean:
            return
        key = f"wanfang-title:{clean}"
        existing = self._workers.get(key)
        if existing is not None and existing.isRunning():
            self.status.emit("该论文详情页已在打开列表中")
            return
        if self.active_count() >= self.MAX_CONCURRENT_DETAILS:
            self.error.emit(
                f"详情页窗口已达上限（{self.MAX_CONCURRENT_DETAILS}），"
                "请先关闭部分窗口"
            )
            return
        worker = DetailWorker(None, self._storage_state_path, title=clean)
        worker.detail_opened.connect(self.detail_opened)
        worker.status.connect(self.status)
        worker.error.connect(self.error)
        worker.finished.connect(lambda k=key: self._workers.pop(k, None))
        self._workers[key] = worker
        worker.start()
        self.status.emit("正在按标题定位万方全文页...")

    def shutdown_all(self, timeout_s: float = 10.0) -> None:
        """应用退出：请求全部详情线程关闭并等待结束。"""
        workers = list(self._workers.values())
        if not workers:
            return
        logger.info("关闭全部详情页线程: %d", len(workers))
        for w in workers:
            w.request_close()
        deadline = time.monotonic() + timeout_s
        for w in workers:
            remaining_ms = int(max(0.0, deadline - time.monotonic()) * 1000)
            w.wait(remaining_ms if remaining_ms > 0 else 0)
        self._workers.clear()
