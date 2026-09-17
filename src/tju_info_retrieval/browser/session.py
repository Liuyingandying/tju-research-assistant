"""正式 BrowserSession：封装已验证的受控 Edge + 持久 Profile。

复用 PoC-0 已验证的启动配置（本机 Edge、runtime/edge_profile、persistent
context、chromium_sandbox=True），不重写浏览器路线。
"""
from __future__ import annotations

import logging
import time
import traceback
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import sync_playwright

from tju_info_retrieval.app_paths import storage_state_path

from poc_browser import (
    DEFAULT_URL,
    EDGE_PROFILE_DIR,
    build_launch_kwargs,
    find_edge_executable,
    is_login_url,
)

logger = logging.getLogger(__name__)

CNKI_HOME_URL = "https://www.cnki.net/"

# 授权状态导出文件（v0.13 详情页多窗口：DetailBrowserSession 启动时注入）
STORAGE_STATE_PATH = storage_state_path()


def _safe_url_for_log(url: str) -> str:
    """日志只保留 scheme/host/path，避免记录认证 query/fragment。"""
    try:
        parts = urlsplit(url or "")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return "<redacted-url>"


class BrowserSession:
    """管理受控 Edge 生命周期与 TJU/CNKI 授权会话（仅本机 Edge + 专用 Profile）。"""

    # 启动后默认打开的入口页面（天津大学电子资源平台），避免停留在 about:blank
    START_URL = DEFAULT_URL

    def __init__(self) -> None:
        self._playwright = None
        self._context = None
        self._page = None

    def start(self) -> None:
        if self._context is not None:
            return
        edge_path = find_edge_executable()
        if edge_path is None:
            logger.error("Microsoft Edge 未检测到")
            raise RuntimeError(
                "未检测到 Microsoft Edge。\n"
                "本系统需要 Microsoft Edge 完成天津大学数据库授权，请安装或恢复 Edge 后重试。"
            )
        logger.info("Microsoft Edge detected path=%s", edge_path)
        EDGE_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                **build_launch_kwargs(edge_path)
            )
        except Exception:
            logger.exception("受控 Edge 启动失败")
            raise
        logger.info("受控 Edge 启动成功 profile=%s", EDGE_PROFILE_DIR)
        self._page = (
            self._context.pages[0] if self._context.pages else self._context.new_page()
        )
        # 启动后默认打开天津大学平台入口；加载失败不致命，后续 goto 仍可导航
        try:
            self._page.goto(self.START_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            logger.warning("启动入口页面加载失败：%s", exc)

    def is_alive(self) -> bool:
        """会话是否已启动且 context 本地引用有效（仅本地检查，无 RPC）。

        注意：_context.pages 是本地缓存，用户关闭 Edge 后不会自动更新
        （Playwright 事件仅在其它 API 调用时派发，Phase 8.6 实测），
        因此本方法可能对"刚被外部关闭"的会话误报 True。
        优点是不触发任何 Playwright 调用，可安全跨栈调用——
        _new_session / shutdown 兜底据此决定"丢弃引用 vs 尽力释放"。
        需要真实存活判定时，在创建会话的栈内改用 is_browser_open()。
        """
        if self._context is None:
            return False
        try:
            self._context.pages
            return True
        except Exception:
            return False

    def is_browser_open(self) -> bool:
        """受控 Edge 窗口是否真实存活（轻量 RPC 探测）。

        与 DetailBrowserSession.is_open 同一问题域：用户从窗口 X 关闭后，
        本地缓存标志不自动更新，必须补一次轻量 RPC 确认；浏览器已关闭时
        该调用立即抛错（捕获后返回 False）。页面重定向瞬间 evaluate 可能
        误报（Execution context destroyed），静置 1 秒后二次确认，连续失败
        才判定浏览器已关闭。仅应在创建本会话的线程/调用栈内调用。
        """
        try:
            if self._context is None or not self._context.pages:
                return False
            self._page.locator("html").evaluate("1", timeout=1000)
            return True
        except Exception:  # noqa: BLE001 - RPC 失败 = 页面/浏览器已关闭
            time.sleep(1.0)
            try:
                self._page.locator("html").evaluate("1", timeout=1000)
                return True
            except Exception:  # noqa: BLE001
                return False

    def open_url(self, url: str, timeout_ms: int = 60000) -> None:
        self._require_started()
        self._page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    def open_tju(self) -> None:
        # 不再固定 sleep：等待跳转稳定交给 check_tju_auth 的条件轮询
        self.open_url(DEFAULT_URL)

    def open_cnki(self) -> None:
        self.open_url(CNKI_HOME_URL, timeout_ms=45000)
        self._page.wait_for_timeout(4000)

    def check_tju_auth(self, timeout_ms: int = 15000) -> str:
        """返回 'logged_in' / 'login_page' / 'unknown'。

        不再依赖固定 sleep：轮询等待以下任一条件出现——
        1) URL 进入登录/SSO 页；
        2) 页面出现“已授权”标志；
        3) 页面出现“登录”入口。
        超时仍未判定 → 记录现场（url/title/pages/body/traceback）并返回 'unknown'。
        """
        self._require_started()
        deadline = time.monotonic() + timeout_ms / 1000.0
        last_url = ""
        last_body = ""
        last_exc: Exception | None = None
        while time.monotonic() < deadline:
            state, last_url, last_body, last_exc = self._probe_auth_state()
            if state is not None:
                logger.info(
                    "授权检查判定 %s url=%r title=%r pages=%d body_head=%r",
                    state,
                    _safe_url_for_log(last_url),
                    self._safe_title(),
                    self._page_count(),
                    last_body[:200],
                )
                if state == "login_page":
                    logger.warning("天津大学 portal auth failure: login required")
                return state
            # 仅作探测间隔让步；判断依据是上面的条件轮询，而非固定 sleep
            time.sleep(0.5)
        self._log_unknown(last_url, last_body, last_exc)
        return "unknown"

    def _probe_auth_state(self) -> tuple[str | None, str, str, Exception | None]:
        """读取当前页面一次，返回 (state|None, url, body, exc)。

        state 为 None 表示本次采样尚无法判定（页面未就绪或读取失败），
        由调用方继续轮询；读取异常保存在 exc 中供超时日志使用。
        """
        url = ""
        body = ""
        exc: Exception | None = None
        try:
            url = self._page.url
            body = self._page.inner_text("body") or ""
        except Exception as e:  # noqa: BLE001 - 记录现场后继续轮询
            exc = e
            try:
                url = self._page.url
            except Exception:
                url = ""
        if exc is not None:
            return None, url, body, exc
        if is_login_url(url):
            return "login_page", url, body, None
        if "已授权" in body:
            return "logged_in", url, body, None
        # TJU 平台顶部出现登录入口且无“已授权” → 视为需要登录
        if "登录" in body[:150]:
            return "login_page", url, body, None
        return None, url, body, None

    def _safe_title(self) -> str:
        try:
            return self._page.title()
        except Exception:
            return "<unavailable>"

    def _page_count(self) -> int:
        try:
            return len(self._context.pages)
        except Exception:
            return -1

    def _log_unknown(self, url: str, body: str, exc: Exception | None) -> None:
        """返回 'unknown' 前记录现场：url / title / pages / body / traceback。"""
        logger.warning(
            "无法确定天津大学授权状态 url=%r title=%r pages=%d body_head=%r exc=%r",
            _safe_url_for_log(url),
            self._safe_title(),
            self._page_count(),
            body[:200],
            exc,
        )
        if exc is not None:
            logger.warning(
                "授权检查最后一次读取异常 traceback:\n%s",
                "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                ),
            )

    def open_detail(self, url: str) -> None:
        """在受控 Edge 中打开详情页（保留授权会话）。"""
        self._require_started()
        self._page.goto(url, wait_until="domcontentloaded", timeout=45000)

    def export_storage_state(self, path=None) -> bool:
        """导出授权状态（cookies/localStorage）到 JSON 文件。

        供详情页多窗口（DetailBrowserSession）启动时注入授权。
        调用时机：授权有效的会话任务（search 成功 / check_auth logged_in），
        在同栈 release 之前。失败返回 False 不影响主流程。
        """
        try:
            target = Path(path) if path else STORAGE_STATE_PATH
            target.parent.mkdir(parents=True, exist_ok=True)
            self._context.storage_state(path=str(target))
            logger.info("授权状态已导出: %s", target)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("授权状态导出失败: %s", exc)
            return False

    def wait_until_closed(
        self,
        timeout_s: float = 1800.0,
        poll_s: float = 1.0,
    ) -> bool:
        """轮询等待用户关闭浏览器（所有页面关闭或 context 失效）。

        供 open_detail 在 worker 线程内"同栈等待用户阅读结束"使用：
        Playwright 对象必须在创建它的 Qt slot 调用栈内销毁——跨 slot
        对旧会话执行 shutdown（context.close/playwright.stop）会触发
        greenlet 生命周期违规（SIGSEGV，v0.13 Phase 8.5 实测）。

        - 只应在 worker 线程调用（轮询 sleep 不阻塞 GUI 主线程）。
        - 支持超时保护：超时返回 False，由调用方决定是否释放。
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if not self._context.pages:
                    return True  # 所有页面已关闭（用户关闭浏览器窗口）
            except Exception:  # noqa: BLE001 - context 已失效视为已关闭
                return True
            time.sleep(poll_s)
        return False

    def page(self):
        self._require_started()
        return self._page

    @property
    def context(self):
        self._require_started()
        return self._context

    def shutdown(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._page = None
        self._playwright = None

    def _require_started(self) -> None:
        if self._context is None or self._page is None:
            raise RuntimeError("BrowserSession 尚未启动，请先调用 start()。")
