#!/usr/bin/env python3
"""正式测试：BrowserWorker 生命周期。

- v0.13 Phase 8.5 生命周期修复：成功打开详情后，在同一 slot 调用栈内
  等待用户关闭浏览器，finally 释放会话（杜绝跨 slot shutdown 的 SIGSEGV）。
- v0.13 授权生命周期修复：check_auth login_page 分支不再跨 slot 保留
  会话（旧实现由下一次任务跨栈 shutdown 旧 Playwright 对象，用户已关闭
  Edge 时触发 EPIPE 闪退）。改为同栈等待登录 → 导出授权状态 → finally
  同栈释放；_new_session / shutdown 对残留旧会话按存活状态防御处理。
"""
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.ui.worker import BrowserWorker


class TestOpenDetailLifecycle(unittest.TestCase):
    def _make_worker(self, session):
        worker = BrowserWorker()

        def fake_new_session():
            worker._session = session
            worker._service = mock.Mock()
            return session, worker._service

        worker._new_session = fake_new_session
        return worker

    def test_success_emits_detail_opened_then_releases_same_stack(self):
        session = mock.Mock()
        worker = self._make_worker(session)
        opened = []
        worker.detail_opened.connect(lambda u: opened.append(u))
        worker.open_detail("https://kns.cnki.net/x")
        self.assertEqual(opened, ["https://kns.cnki.net/x"])
        # 生命周期修复：同栈等待用户关闭后，finally 释放（shutdown 恰好一次）
        session.wait_until_closed.assert_called_once()
        session.shutdown.assert_called_once()
        self.assertIsNone(worker._session)

    def test_failure_emits_error_and_cleans_up(self):
        session = mock.Mock()
        session.open_detail.side_effect = RuntimeError("goto failed")
        worker = self._make_worker(session)
        errors = []
        worker.error.connect(lambda m: errors.append(m))
        worker.open_detail("https://bad")
        self.assertTrue(errors, "异常时应发出错误信号")
        self.assertIn("打开全文页面失败", errors[0])
        session.shutdown.assert_called_once()
        self.assertIsNone(worker._session)
        self.assertIsNone(worker._service)


class TestCheckAuthLifecycle(unittest.TestCase):
    """check_auth 生命周期（v0.13 修复后）：所有分支同一 slot 栈内释放。

    login_page 不再跨 slot 保留会话：同栈等待用户登录（_wait_for_login），
    登录成功/用户关闭浏览器/超时/退出请求均走 finally 释放。
    """

    def _make_check_auth_worker(self, state):
        worker = BrowserWorker()
        session = mock.Mock()
        service = mock.Mock()
        service.check_tju_auth.return_value = state
        worker._session = session
        worker._service = service

        def fake_new_session():
            return session, service

        worker._new_session = fake_new_session
        return worker, session

    def test_login_page_waits_then_releases_same_stack(self):
        """login_page：同栈等待用户关闭浏览器后 finally 释放（修复跨 slot 保存）。"""
        worker, session = self._make_check_auth_worker("login_page")
        # 用户已关闭受控 Edge → 等待立即结束（未检测到登录成功）
        session.is_browser_open.return_value = False
        states = []
        worker.auth_result.connect(lambda s: states.append(s))
        worker.check_auth()
        self.assertEqual(states, ["login_page"])
        session.shutdown.assert_called_once()  # 同栈 finally 释放
        self.assertIsNone(worker._session)
        self.assertIsNone(worker._service)

    def test_login_success_exports_and_emits_logged_in(self):
        """login_page → 等待期间检测到登录成功 → 导出授权状态并补发 logged_in。"""
        worker, session = self._make_check_auth_worker("login_page")
        session.is_browser_open.return_value = True
        # 同栈重探测：首次仍 login_page，随后登录成功
        session.check_tju_auth.side_effect = ["login_page", "logged_in"]
        states = []
        worker.auth_result.connect(lambda s: states.append(s))
        with mock.patch("tju_info_retrieval.ui.worker.time.sleep"):
            worker.check_auth()
        self.assertEqual(states, ["login_page", "logged_in"])
        session.export_storage_state.assert_called_once()
        session.shutdown.assert_called_once()
        self.assertIsNone(worker._session)

    def test_stop_request_ends_wait_and_releases(self):
        """应用退出请求（request_stop）→ 等待立即结束并同栈释放。"""
        worker, session = self._make_check_auth_worker("login_page")
        session.is_browser_open.return_value = True
        session.check_tju_auth.return_value = "login_page"
        worker.request_stop()
        states = []
        worker.auth_result.connect(lambda s: states.append(s))
        worker.check_auth()
        self.assertEqual(states, ["login_page"])  # 仅初始判定，无 logged_in 补发
        session.shutdown.assert_called_once()
        self.assertIsNone(worker._session)

    def test_logged_in_releases_session(self):
        worker, session = self._make_check_auth_worker("logged_in")
        states = []
        worker.auth_result.connect(lambda s: states.append(s))
        worker.check_auth()
        self.assertEqual(states, ["logged_in"])
        session.export_storage_state.assert_called_once()
        session.shutdown.assert_called_once()
        self.assertIsNone(worker._session)

    def test_unknown_releases_session(self):
        # unknown 保留安全策略（释放），现场日志由 session.check_tju_auth 负责
        worker, session = self._make_check_auth_worker("unknown")
        states = []
        worker.auth_result.connect(lambda s: states.append(s))
        worker.check_auth()
        self.assertEqual(states, ["unknown"])
        session.shutdown.assert_called_once()
        self.assertIsNone(worker._session)

    def test_error_releases_session(self):
        worker, session = self._make_check_auth_worker("logged_in")
        worker._service.check_tju_auth.side_effect = RuntimeError("check failed")
        errors = []
        worker.error.connect(lambda m: errors.append(m))
        worker.check_auth()
        self.assertTrue(errors, "异常时应发出错误信号")
        self.assertIn("检查授权失败", errors[0])
        session.shutdown.assert_called_once()
        self.assertIsNone(worker._session)


class TestNewSessionDefense(unittest.TestCase):
    """_new_session / shutdown 对残留旧会话的防御（v0.13 生命周期修复）。"""

    def test_dead_leftover_dropped_without_shutdown(self):
        """死会话（浏览器已被用户关闭）：仅丢弃引用，不跨栈 shutdown。"""
        worker = BrowserWorker()
        dead = mock.Mock()
        dead.is_alive.return_value = False
        worker._session = dead
        worker._service = mock.Mock()
        with mock.patch("tju_info_retrieval.ui.worker.BrowserSession") as cls_mock, \
                mock.patch("tju_info_retrieval.ui.worker.time.sleep") as sleep_mock:
            session, _service = worker._new_session()
        dead.shutdown.assert_not_called()  # 绝不触碰死 Playwright 对象
        sleep_mock.assert_not_called()  # Edge 已退出，无需等待锁释放
        self.assertIsNot(session, dead)
        self.assertIs(worker._session, session)

    def test_live_leftover_released_before_new_session(self):
        """活会话残留：先 shutdown 并等待旧 Edge 退出，再创建新会话。"""
        worker = BrowserWorker()
        live = mock.Mock()
        live.is_alive.return_value = True
        worker._session = live
        with mock.patch("tju_info_retrieval.ui.worker.BrowserSession") as cls_mock, \
                mock.patch("tju_info_retrieval.ui.worker.time.sleep") as sleep_mock:
            session, _service = worker._new_session()
        live.shutdown.assert_called_once()
        sleep_mock.assert_any_call(5.0)
        self.assertIsNot(session, live)

    def test_shutdown_slot_drops_dead_leftover(self):
        """退出兜底：死亡残留会话仅丢弃引用，不跨栈 shutdown。"""
        worker = BrowserWorker()
        dead = mock.Mock()
        dead.is_alive.return_value = False
        worker._session = dead
        worker._service = mock.Mock()
        worker.shutdown()
        dead.shutdown.assert_not_called()
        self.assertIsNone(worker._session)
        self.assertIsNone(worker._service)

    def test_shutdown_slot_releases_live_session(self):
        worker = BrowserWorker()
        live = mock.Mock()
        live.is_alive.return_value = True
        worker._session = live
        worker.shutdown()
        live.shutdown.assert_called_once()
        self.assertIsNone(worker._session)


class TestCheckAuthThenNextTask(unittest.TestCase):
    """修复核心场景回归：login_page 会话同栈释放后，后续任务干净启动。

    真实 _new_session 路径（patch BrowserSession/SearchService 类），
    覆盖"检查授权 → 手动关闭 Edge → 再次操作"的崩溃复现链路。
    """

    def _patched_env(self):
        """patch BrowserSession/SearchService/time.sleep，返回 (mocks, enter)。"""
        patches = [
            mock.patch("tju_info_retrieval.ui.worker.BrowserSession"),
            mock.patch("tju_info_retrieval.ui.worker.SearchService"),
            mock.patch("tju_info_retrieval.ui.worker.time.sleep"),
        ]
        return patches

    def test_second_check_auth_after_manual_close_creates_fresh_session(self):
        """复现路径：第一次 login_page（用户关闭 Edge）→ 第二次 check_auth
        干净创建新会话，绝不触碰第一个会话的 Playwright 对象。"""
        patches = self._patched_env()
        with patches[0] as cls_mock, patches[1] as svc_mock, patches[2]:
            s1, s2 = mock.Mock(), mock.Mock()
            cls_mock.side_effect = [s1, s2]
            service = mock.Mock()
            service.check_tju_auth.return_value = "login_page"
            svc_mock.return_value = service
            worker = BrowserWorker()
            # 第一次：login_page，用户已手动关闭 Edge
            s1.is_browser_open.return_value = False
            worker.check_auth()
            s1.shutdown.assert_called_once()  # 同栈释放
            self.assertIsNone(worker._session)
            # 第二次：无残留 → 直接创建新会话；旧会话不再被触碰
            s2.is_browser_open.return_value = False
            worker.check_auth()
            self.assertEqual(cls_mock.call_count, 2)
            self.assertEqual(s1.shutdown.call_count, 1)
            self.assertEqual(s2.shutdown.call_count, 1)
            self.assertIsNone(worker._session)

    def test_check_auth_then_search_uses_fresh_session(self):
        """check_auth（login_page→关窗释放）后检索：新会话创建，检索正常执行。"""
        patches = self._patched_env()
        with patches[0] as cls_mock, patches[1] as svc_mock, patches[2]:
            s1, s2 = mock.Mock(), mock.Mock()
            cls_mock.side_effect = [s1, s2]
            service = mock.Mock()
            service.check_tju_auth.return_value = "login_page"
            service.search.return_value = []
            svc_mock.return_value = service
            worker = BrowserWorker()
            s1.is_browser_open.return_value = False
            worker.check_auth()
            self.assertIsNone(worker._session)
            # 检索：新会话 s2，service.search 正常调用并发出结果
            done = []
            worker.search_done.connect(lambda r: done.append(r))
            worker.search(mock.Mock())
            self.assertEqual(len(done), 1)
            service.search.assert_called_once()
            s2.shutdown.assert_called_once()  # search finally 释放
            self.assertEqual(s1.shutdown.call_count, 1)  # 旧会话不再被触碰
            self.assertIsNone(worker._session)

    def test_check_auth_then_open_detail_uses_fresh_session(self):
        """check_auth（login_page→关窗释放）后打开详情：新会话创建，详情正常。"""
        patches = self._patched_env()
        with patches[0] as cls_mock, patches[1] as svc_mock, patches[2]:
            s1, s2 = mock.Mock(), mock.Mock()
            cls_mock.side_effect = [s1, s2]
            service = mock.Mock()
            service.check_tju_auth.return_value = "login_page"
            svc_mock.return_value = service
            worker = BrowserWorker()
            s1.is_browser_open.return_value = False
            worker.check_auth()
            self.assertIsNone(worker._session)
            # 详情页：新会话 s2，open_detail + 同栈等待 + finally 释放
            opened = []
            worker.detail_opened.connect(lambda u: opened.append(u))
            worker.open_detail("https://kns.cnki.net/x")
            self.assertEqual(opened, ["https://kns.cnki.net/x"])
            s2.open_detail.assert_called_once_with("https://kns.cnki.net/x")
            s2.wait_until_closed.assert_called_once()
            s2.shutdown.assert_called_once()
            self.assertEqual(s1.shutdown.call_count, 1)  # 旧会话不再被触碰
            self.assertIsNone(worker._session)


if __name__ == "__main__":
    unittest.main(verbosity=2)
