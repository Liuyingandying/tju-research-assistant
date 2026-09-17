"""详情页专用线程 DetailWorker（v0.13 详情页多窗口架构）。

每个详情页一个独立 QThread，线程栈内完成完整生命周期：
  创建 DetailBrowserSession → 打开详情 → detail_opened
  → 等待用户关闭窗口 → finally 同栈释放

与搜索 worker（BrowserWorker）完全隔离：不共享 BrowserSession /
Playwright context，搜索生命周期不受影响。

v0.13 B′ 万方按需解析：title 模式（url 为空、title 给定）时在同栈内
执行"门户 → 标题定位 → 点击 → adopt 详情页"，之后进入相同的
等待/释放流程；生命周期约束不变。
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QThread, Signal

from tju_info_retrieval.browser.detail_session import DetailBrowserSession
from tju_info_retrieval.browser.wanfang_resolver import resolve_detail_page

logger = logging.getLogger(__name__)


class DetailWorker(QThread):
    """单篇论文详情页的独立浏览器线程。"""

    detail_opened = Signal(str)
    status = Signal(str)
    error = Signal(str)

    # 详情页阅读等待上限（秒）：防止长期未关闭导致会话泄漏
    READ_TIMEOUT_S = 1800.0

    def __init__(
        self,
        url: str,
        storage_state_path=None,
        parent=None,
        title: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._url = url
        self._storage_state_path = storage_state_path
        self._session: DetailBrowserSession | None = None
        self._close_requested = False  # shutdown_all 的优雅关闭请求
        self._title = title  # B′：url 为空且 title 给定 → 按需解析模式

    def request_close(self) -> None:
        """请求提前关闭窗口（DetailManager.shutdown_all 调用，线程安全）。"""
        self._close_requested = True

    def run(self) -> None:
        """线程主函数：同线程栈内完成会话创建 → 打开 → 等待 → 释放。

        等待循环每秒自检一次（窗口关闭 / shutdown_all 请求 / 阅读超时），
        保证 shutdown_all 能在秒级内优雅结束线程。
        """
        try:
            self.status.emit("正在启动浏览器...")
            session = DetailBrowserSession(self._storage_state_path)
            self._session = session
            session.start()
            if self._title and not self._url:
                # B′ 万方按需解析：门户 → 标题定位 → 点击 → adopt 详情页，
                # Playwright 对象全程在本 run() 栈内创建与销毁
                resolve_detail_page(session, self._title, status=self.status.emit)
                self.detail_opened.emit(self._title)
            else:
                session.open_detail(self._url)
                self.detail_opened.emit(self._url)
            self.status.emit("详情页已打开；关闭浏览器窗口后可继续其它操作")
            deadline = time.monotonic() + self.READ_TIMEOUT_S
            while time.monotonic() < deadline:
                if self._close_requested or not session.is_open():
                    break
                time.sleep(1.0)
        except Exception as exc:  # noqa: BLE001 - 失败不影响其它详情线程
            logger.exception("详情页打开失败 url=%r title=%r", self._url, self._title)
            self.error.emit(f"打开详情页失败：{exc}")
        finally:
            try:
                if self._session is not None:
                    self._session.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._session = None
