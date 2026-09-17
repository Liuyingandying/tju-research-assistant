"""后台浏览器 Worker：在独立线程中执行 Playwright 操作，避免阻塞 GUI。

请求信号定义在 MainWindow（发送方在主线程），连接到本 Worker 的槽
（接收方在 worker 线程），Qt 自动使用 QueuedConnection 跨线程投递。
所有 Playwright 调用都发生在 worker 线程内。

任务生命周期：每个任务（check_auth / search / open_detail）创建独立的
BrowserSession，任务结束释放。Playwright sync API 的 greenlet 状态不能在
QThread 的多次槽调用间安全复用（复用会导致 SIGSEGV），因此绝不跨任务复用会话。
导出（export）不涉及浏览器，不需要会话。
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, Signal, Slot

from tju_info_retrieval.browser.session import BrowserSession
from tju_info_retrieval.services.search_service import AuthError, SearchService

logger = logging.getLogger(__name__)


class BrowserWorker(QObject):
    """在 worker 线程中执行浏览器操作；每个任务使用独立 BrowserSession。"""

    status_changed = Signal(str)
    auth_result = Signal(str)
    search_done = Signal(list)
    search_failed = Signal(str)
    error = Signal(str)
    detail_opened = Signal(str)

    # 详情页阅读等待上限（秒）：防止用户长期不关闭导致会话泄漏；
    # 超时后本槽返回并释放会话（浏览器将被关闭），GUI 收到状态提示。
    DETAIL_READ_TIMEOUT_S = 1800.0

    # 登录等待上限（秒）：login_page 后在同一 slot 栈内等待用户完成登录；
    # 超时/用户关闭浏览器后释放会话（v0.13 授权生命周期修复）
    LOGIN_WAIT_TIMEOUT_S = 1800.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._session: BrowserSession | None = None
        self._service: SearchService | None = None
        # 应用退出请求（closeEvent 经 request_stop 置位；bool 标志 GIL 安全）。
        # 置位后登录等待循环秒级返回并同栈释放会话，保证 thread.wait 能等到线程退出
        self._stop_requested = False

    def request_stop(self) -> None:
        """请求等待中的 slot 提前结束（主线程 closeEvent 调用，非信号）。"""
        self._stop_requested = True

    def _new_session(self):
        """创建独立会话；若存在残留旧会话，按存活状态安全处理。

        v0.13 生命周期防御：正常流程下所有会话已在各 slot 的 finally 中
        同栈释放，此处不应看到旧会话。防御性处理分两种情况——
        - 死会话（is_alive()=False，Edge 已被用户关闭/崩溃）：仅丢弃引用，
          绝不对死 Playwright 对象跨栈 shutdown（EPIPE/SIGSEGV 闪退），
          也无需等待（Edge 已退出，profile 锁已释放）。
        - 活会话：尽力 shutdown 并等待旧 Edge 退出，否则 profile 锁会
          阻塞新会话启动。
        """
        if self._session is not None:
            old_session = self._session
            self._session = None
            self._service = None
            if old_session.is_alive():
                try:
                    old_session.shutdown()
                except Exception:
                    pass
                time.sleep(5.0)
            else:
                logger.warning("发现死亡残留会话：跳过跨栈 shutdown，仅丢弃引用")
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                session = BrowserSession()
                session.start()
                self._session = session
                self._service = SearchService(session)
                return session, self._service
            except Exception as exc:
                last_err = exc
                self._session = None
                self._service = None
                if attempt < 2:
                    time.sleep(3.0)
        raise last_err

    def _release(self) -> None:
        """任务结束释放会话（关闭浏览器，Profile 保留）。"""
        try:
            if self._session is not None:
                self._session.shutdown()
        except Exception:
            pass
        self._session = None
        self._service = None

    @Slot()
    def check_auth(self):
        try:
            self.status_changed.emit("正在启动浏览器...")
            session, service = self._new_session()
            self.status_changed.emit("正在打开天津大学电子资源平台...")
            state = service.check_tju_auth()
            self.auth_result.emit(state)
            if state == "login_page":
                # v0.13 生命周期修复：login_page 不再跨 slot 保留会话
                # （旧实现 slot 返回后由下一次任务的 _new_session 跨栈
                # shutdown 旧 Playwright 对象；用户已手动关闭 Edge 时，
                # 对死 PipeTransport 发送触发 EPIPE 闪退）。改为与
                # open_detail 一致：同一 slot 栈内等待登录，finally 同栈释放。
                logger.info("授权检查判定 login_page：同栈等待用户登录")
                self.status_changed.emit(
                    "请在受控浏览器中完成天津大学登录；登录成功后自动继续，"
                    "也可直接关闭浏览器窗口"
                )
                final_state = self._wait_for_login(session)
                if final_state == "logged_in":
                    self._export_auth_state()  # 详情页多窗口授权来源
                    self.auth_result.emit("logged_in")
                    logger.info("用户完成登录：授权状态已导出")
                elif final_state == "unknown":
                    self.status_changed.emit(
                        "登录等待超时/中止，会话已释放；请重新检查授权"
                    )
            elif state == "logged_in":
                self._export_auth_state()  # 详情页多窗口授权来源
        except Exception as exc:
            self.error.emit(f"检查授权失败：{exc}")
        finally:
            # 生命周期约束：Playwright 对象必须在创建它的 slot 调用栈内销毁
            self._release()

    def _wait_for_login(self, session, timeout_s: float = LOGIN_WAIT_TIMEOUT_S) -> str:
        """同一 slot 栈内等待用户登录（v0.13 生命周期修复）。

        轮询探测授权状态（session.check_tju_auth 直接探测当前页，不重新
        导航）；用户关闭受控 Edge 即结束等待。仅应在创建会话的 slot
        调用栈内运行（Playwright 同栈约束）。

        返回最终状态：
        - logged_in: 用户完成登录（调用方导出授权状态并补发 auth_result）
        - login_page: 用户关闭浏览器，未检测到登录成功
        - unknown: 等待超时或应用退出请求
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not self._stop_requested:
            if not session.is_browser_open():
                # 用户已关闭受控 Edge：登录流程结束（登录成功状态会在
                # 关闭前被探测到并提前返回）
                return "login_page"
            try:
                state = session.check_tju_auth(timeout_ms=3000)
            except Exception:  # noqa: BLE001 - 探测期间浏览器意外退出
                return "login_page"
            if state == "logged_in":
                return "logged_in"
            time.sleep(0.5)
        return "unknown"

    @Slot(object)
    def search(self, query):
        try:
            self.status_changed.emit("正在启动浏览器...")
            _, service = self._new_session()
            results = service.search(query, status=self.status_changed.emit)
            self.status_changed.emit(f"检索完成：{len(results)} 条")
            self.search_done.emit([r.to_dict() for r in results])
            # v0.13 详情页多窗口：导出授权状态供详情线程注入（不改搜索逻辑）
            self._export_auth_state()
        except AuthError as exc:
            self.search_failed.emit(str(exc))
        except Exception as exc:
            self.search_failed.emit(str(exc))
        finally:
            self._release()

    def _export_auth_state(self) -> None:
        """授权有效时导出 storage_state（详情页多窗口的授权来源）。"""
        try:
            if self._session is not None:
                self._session.export_storage_state()
        except Exception as exc:  # noqa: BLE001
            logger.warning("导出授权状态失败: %s", exc)

    @Slot(str)
    def open_detail(self, url):
        """打开论文详情页并保持至用户关闭浏览器。

        v0.13 Phase 8.5 生命周期修复：Playwright 对象必须在创建它的
        slot 调用栈内销毁。旧实现在成功后跨 slot 保留会话，下次任务
        在新栈中对旧对象 shutdown（context.close/playwright.stop），
        触发 greenlet 生命周期违规（SIGSEGV）。新实现在同一 slot 内
        等待用户关闭浏览器，再于 finally 中释放。
        """
        try:
            self.status_changed.emit("正在启动浏览器...")
            session, _ = self._new_session()
            session.open_detail(url)
            self.detail_opened.emit(url)
            self.status_changed.emit("详情页已打开；关闭浏览器窗口后可继续其它操作")
            session.wait_until_closed(
                timeout_s=self.DETAIL_READ_TIMEOUT_S, poll_s=1.0
            )
        except Exception as exc:
            self.error.emit(f"打开全文页面失败：{exc}")
        finally:
            self._release()

    @Slot()
    def shutdown(self):
        """应用退出兜底清理（closeEvent → shutdown_requested）。

        正常流程下所有会话已在各 slot 的 finally 中同栈释放（进入本槽时
        self._session 应为 None）。防御：若仍存在死亡残留会话（浏览器已
        被用户关闭），仅丢弃引用——跨栈对死 Playwright 对象 shutdown 会
        触发 EPIPE/SIGSEGV；存活会话尽力释放。
        """
        if self._session is not None and not self._session.is_alive():
            logger.warning("退出时发现死亡残留会话：仅丢弃引用，不跨栈 shutdown")
            self._session = None
            self._service = None
            return
        self._release()
