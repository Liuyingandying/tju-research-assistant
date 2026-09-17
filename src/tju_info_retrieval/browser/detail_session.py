"""详情页专用浏览器会话 DetailBrowserSession（v0.13 详情页多窗口架构）。

与任务会话（BrowserSession）完全隔离：
- 独立 Playwright 实例、独立 Edge 进程、独立 context、独立 page；
- 不共享 BrowserSession / context（多窗口各自生灭，无跨线程 Playwright 对象）；
- 授权通过 storage_state 文件注入（由任务会话在 search/check_auth 成功后导出），
  避开 persistent context 的 profile SingletonLock（两个进程不能共用同一
  user-data-dir，这是多窗口无法直接复用 BrowserSession 的硬约束）。

生命周期（在 DetailWorker(QThread).run 的同一线程栈内完成）：
  start() → open_detail(url) → wait_until_closed() → shutdown()
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from poc_browser import DEFAULT_URL, find_edge_executable

logger = logging.getLogger(__name__)


class DetailBrowserSession:
    """单个详情页的独立浏览器会话（一个 Edge 窗口）。"""

    # 窗口外框相对 viewport 的边距（Playwright 会调整 OS 窗口匹配视口，
    # Phase 8.5 实测：viewport 1904x992 → 窗口 outer 1920x1080 铺满屏幕）
    SCREEN_MARGIN_W = 16
    SCREEN_MARGIN_H = 88

    @staticmethod
    def _screen_size() -> tuple[int, int]:
        """主显示器分辨率（Windows；失败回退 1280×720）。"""
        try:
            import ctypes

            user32 = ctypes.windll.user32
            try:
                user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:  # noqa: BLE001
            return 1280, 720

    def __init__(self, storage_state_path=None) -> None:
        self._storage_state_path = (
            str(Path(storage_state_path)) if storage_state_path else None
        )
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def start(self) -> None:
        if self._browser is not None:
            return
        edge_path = find_edge_executable()
        if edge_path is None:
            raise RuntimeError(
                "未检测到 Microsoft Edge。\n"
                "本系统需要 Microsoft Edge 完成天津大学数据库授权，请安装或恢复 Edge 后重试。"
            )
        screen_w, screen_h = self._screen_size()
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=False,
            executable_path=edge_path,
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=msEdgeFirstRunExperience",
                "--hide-crash-restore-bubble",
                "--start-maximized",
                f"--window-size={screen_w},{screen_h}",
            ],
        )
        context_kwargs: dict = {
            # v0.13：视口 = 屏幕 - 窗口边距。注意 Playwright 1.62 会忽略
            # viewport=None（实测三种启动方式均回退默认 1280×720），
            # 显式大视口才会让窗口铺满屏幕（窗口随视口自适应）。
            "viewport": {
                "width": screen_w - self.SCREEN_MARGIN_W,
                "height": screen_h - self.SCREEN_MARGIN_H,
            },
        }
        if (
            self._storage_state_path
            and Path(self._storage_state_path).exists()
        ):
            context_kwargs["storage_state"] = self._storage_state_path
        context = self._browser.new_context(**context_kwargs)
        self._context = context
        self._page = context.new_page()

    def open_detail(self, url: str) -> None:
        """在独立窗口中打开详情页。"""
        self._page.goto(url, wait_until="domcontentloaded", timeout=45000)

    def goto(self, url: str, timeout_ms: int = 45000) -> None:
        """导航当前活动页面（万方 resolver 与 PortalNavigator 协议共用）。"""
        if self._page is None:
            raise RuntimeError("DetailBrowserSession 尚未启动，请先调用 start()。")
        self._page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    def open_tju(self) -> None:
        """打开天津大学门户入口（PortalNavigator 协议兼容）。"""
        self.goto(DEFAULT_URL)

    def page(self):
        """当前活动页面（可能经 adopt 切换为新标签）。"""
        return self._page

    @property
    def context(self):
        """浏览器上下文（PortalNavigator 协议兼容）。"""
        if self._context is None:
            raise RuntimeError("DetailBrowserSession 尚未启动，请先调用 start()。")
        return self._context

    def adopt(self, page) -> None:
        """接管新标签页作为活动页面（万方 resolver 点击后的 new_tab 形态）。

        adopt 后 is_open()/wait_until_closed() 的探活对象随之切换；
        旧标签页不主动关闭，由 shutdown() 的 browser.close() 统一回收。
        """
        self._page = page

    def is_open(self) -> bool:
        """窗口是否仍真实存活。

        仅靠 page.is_closed()/browser.is_connected() 不够：二者是本地
        缓存标志，用户从 Edge 窗口点 X 关闭后**不会更新**（Playwright
        的 close/disconnected 事件只在其它 API 调用时派发），导致
        DetailWorker.run 的等待循环永不退出（v0.13 Phase 8.6 实测）。

        因此本地检查通过后必须补一次轻量 RPC 探测确认浏览器真实存活：
        locator.evaluate 带 1000ms 超时（注意：page.evaluate 没有
        timeout 参数；:visible/:text 等 Playwright 扩展也仅 locator
        引擎支持）。浏览器已被用户关闭时该调用立即抛
        "Target page, context or browser has been closed"。
        """
        try:
            if self._page.is_closed():
                return False
            if not self._browser.is_connected():
                return False
            # 轻量 RPC：泵 Playwright 事件队列并确认浏览器真实存活
            self._page.locator("html").evaluate("1", timeout=1000)
            return True
        except Exception:  # noqa: BLE001 - RPC 失败 = 页面/浏览器已关闭
            return False

    def wait_until_closed(
        self,
        timeout_s: float = 1800.0,
        poll_s: float = 1.0,
    ) -> bool:
        """轮询等待用户关闭详情窗口（page 关闭或浏览器退出）。

        只应在创建本会话的线程（DetailWorker.run）调用；
        超时返回 False，由调用方决定是否释放。
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self.is_open():
                return True
            time.sleep(poll_s)
        return False

    def shutdown(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        self._page = None
        self._browser = None
        self._playwright = None
