#!/usr/bin/env python3
"""v0.13 详情页多窗口架构测试（全部 mock，不开真浏览器）。

覆盖：
1. open_detail 启动独立 DetailWorker（独立线程+独立会话）
2. 同一 URL 防重复打开
3. 并发窗口数上限
4. worker finished 后从映射清理
5. shutdown_all 请求优雅关闭
6. DetailWorker.run 完整生命周期（创建→打开→信号→等待→释放）
7. storage_state 缺失时降级（DetailBrowserSession 不带注入）
8. 搜索生命周期不受影响（BrowserWorker.search 契约保持）
"""
import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from PySide6.QtWidgets import QApplication

from tju_info_retrieval.ui.detail_manager import DetailManager
from tju_info_retrieval.ui.detail_worker import DetailWorker

URL1 = "https://kns.cnki.net/detail/1"
URL2 = "https://kns.cnki.net/detail/2"


class TestDetailManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.created_workers: list = []
        patcher = mock.patch(
            "tju_info_retrieval.ui.detail_manager.DetailWorker"
        )
        self.mock_worker_cls = patcher.start()
        self.addCleanup(patcher.stop)

        def make_worker(url, storage_state_path=None):
            w = mock.Mock(spec=DetailWorker)
            w.url = url
            w.isRunning.return_value = True
            self.created_workers.append(w)
            return w

        self.mock_worker_cls.side_effect = make_worker
        self.manager = DetailManager(storage_state_path="state.json")
        statuses = []
        self.manager.status.connect(statuses.append)
        self.statuses = statuses

    def test_open_detail_starts_independent_worker(self):
        self.manager.open_detail(URL1)
        self.mock_worker_cls.assert_called_once_with(URL1, "state.json")
        w = self.created_workers[0]
        w.start.assert_called_once()
        self.assertEqual(self.manager.active_count(), 1)

    def test_duplicate_url_not_reopened(self):
        self.manager.open_detail(URL1)
        self.manager.open_detail(URL1)
        self.assertEqual(self.mock_worker_cls.call_count, 1)
        self.assertIn("已在打开列表中", self.statuses[-1])

    def test_max_concurrent_rejects(self):
        limit = DetailManager.MAX_CONCURRENT_DETAILS
        for i in range(limit):
            self.manager.open_detail(f"https://kns.cnki.net/detail/{i}")
        self.assertEqual(self.manager.active_count(), limit)
        errors = []
        self.manager.error.connect(errors.append)
        self.manager.open_detail("https://kns.cnki.net/detail/6")
        self.assertTrue(errors and "上限" in errors[0])

    def test_finished_worker_cleaned_up(self):
        self.manager.open_detail(URL1)
        w = self.created_workers[0]
        w.isRunning.return_value = False
        # finished 信号 → lambda pop
        w.finished.emit()
        self.assertEqual(self.manager.active_count(), 0)
        # 同 URL 可以重新打开
        self.manager.open_detail(URL1)
        self.assertEqual(self.manager.active_count(), 1)

    def test_shutdown_all_requests_close(self):
        for i in range(3):
            self.manager.open_detail(f"https://kns.cnki.net/detail/{i}")
        self.manager.shutdown_all(timeout_s=1)
        for w in self.created_workers:
            w.request_close.assert_called_once()
            w.wait.assert_called()
        self.assertEqual(self.manager.active_count(), 0)


# ============================================================
# DetailWorker.run 生命周期
# ============================================================

class TestDetailWorkerRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _worker(self, url=URL1, storage_state="state.json"):
        opened, errors, statuses = [], [], []
        w = DetailWorker(url, storage_state_path=storage_state)
        w.detail_opened.connect(lambda u: opened.append(u))
        w.error.connect(lambda m: errors.append(m))
        w.status.connect(lambda m: statuses.append(m))
        return w, opened, errors, statuses

    def test_run_full_lifecycle(self):
        w, opened, errors, _statuses = self._worker()
        with mock.patch(
            "tju_info_retrieval.ui.detail_worker.DetailBrowserSession"
        ) as session_cls:
            session = session_cls.return_value
            session.is_open.side_effect = [True, False]  # 打开→随后关闭
            w.run()
        session_cls.assert_called_once_with("state.json")
        session.start.assert_called_once()
        session.open_detail.assert_called_once_with(URL1)
        self.assertEqual(opened, [URL1])
        session.shutdown.assert_called_once()
        self.assertEqual(errors, [])
        self.assertIsNone(w._session)

    def test_run_without_storage_state_degrades(self):
        """storage_state 缺失 → 不注入（DetailBrowserSession 收到 None）。"""
        w, opened, errors, _statuses = self._worker(storage_state=None)
        with mock.patch(
            "tju_info_retrieval.ui.detail_worker.DetailBrowserSession"
        ) as session_cls:
            session = session_cls.return_value
            session.is_open.side_effect = [True, False]
            w.run()
        session_cls.assert_called_once_with(None)
        self.assertEqual(opened, [URL1])

    def test_run_error_still_releases(self):
        w, _opened, errors, _statuses = self._worker()
        with mock.patch(
            "tju_info_retrieval.ui.detail_worker.DetailBrowserSession"
        ) as session_cls:
            session = session_cls.return_value
            session.start.side_effect = RuntimeError("launch failed")
            w.run()
        self.assertTrue(errors and "打开详情页失败" in errors[0])
        session.shutdown.assert_called_once()
        self.assertIsNone(w._session)

    def test_request_close_breaks_wait(self):
        w, opened, errors, _statuses = self._worker()
        with mock.patch(
            "tju_info_retrieval.ui.detail_worker.DetailBrowserSession"
        ) as session_cls:
            session = session_cls.return_value
            session.is_open.return_value = True  # 一直开着
            w.request_close()  # shutdown_all 请求
            w.run()
        session.shutdown.assert_called_once()
        self.assertEqual(errors, [])


# ============================================================
# DetailBrowserSession.is_open 关闭检测（v0.13 Phase 8.6 回归）
# ============================================================

class TestDetailSessionStartConfig(unittest.TestCase):
    """v0.13 viewport 自适应：launch --start-maximized + context viewport=None。"""

    def _start(self, storage_state="state.json"):
        from tju_info_retrieval.browser import detail_session as ds

        s = ds.DetailBrowserSession(storage_state)
        with mock.patch.object(ds, "find_edge_executable",
                               return_value="C:/Program Files/msedge.exe"), \
             mock.patch.object(ds, "sync_playwright") as mock_sp, \
             mock.patch.object(ds.DetailBrowserSession, "_screen_size",
                               return_value=(1920, 1080)):
            pw = mock_sp.return_value.start.return_value
            s.start()
        return pw

    def test_launch_contains_start_maximized_and_window_size(self):
        pw = self._start("state.json")
        launch_kwargs = pw.chromium.launch.call_args[1]
        self.assertIn("--start-maximized", launch_kwargs["args"])
        self.assertIn("--window-size=1920,1080", launch_kwargs["args"])
        self.assertIs(launch_kwargs["headless"], False)
        self.assertEqual(
            launch_kwargs["executable_path"], "C:/Program Files/msedge.exe"
        )

    def test_context_created_with_screen_adaptive_viewport(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_path = f.name
        self.addCleanup(lambda: Path(state_path).unlink(missing_ok=True))
        pw = self._start(state_path)
        ctx_kwargs = pw.chromium.launch.return_value.new_context.call_args[1]
        # 屏幕 1920×1080 − 边距(16, 88) → 视口 1904×992
        self.assertEqual(
            ctx_kwargs["viewport"], {"width": 1904, "height": 992}
        )
        self.assertEqual(ctx_kwargs["storage_state"], state_path)

    def test_context_without_storage_state_still_sets_viewport(self):
        """storage_state 缺失时降级，但屏幕自适应视口保持。"""
        pw = self._start(None)
        ctx_kwargs = pw.chromium.launch.return_value.new_context.call_args[1]
        self.assertEqual(
            ctx_kwargs["viewport"], {"width": 1904, "height": 992}
        )
        self.assertNotIn("storage_state", ctx_kwargs)


# ============================================================
# DetailBrowserSession.is_open 关闭检测（v0.13 Phase 8.6 回归）
# ============================================================

class TestDetailSessionIsOpen(unittest.TestCase):
    """本地标志正常但浏览器已被用户关闭时，必须靠 RPC 探测感知。"""

    def _session_with_page(self):
        from tju_info_retrieval.browser.detail_session import DetailBrowserSession

        s = DetailBrowserSession()
        page = mock.Mock()
        page.is_closed.return_value = False
        browser = mock.Mock()
        browser.is_connected.return_value = True
        s._page = page
        s._browser = browser
        return s, page

    def test_local_ok_but_rpc_fails_returns_false(self):
        """本地标志正常但 evaluate 抛异常（用户已关闭）→ False。"""
        s, page = self._session_with_page()
        html_loc = mock.Mock()
        html_loc.evaluate.side_effect = Exception(
            "Page.evaluate: Target page, context or browser has been closed"
        )
        page.locator.return_value = html_loc
        self.assertFalse(s.is_open())
        page.locator.assert_called_once_with("html")

    def test_local_ok_rpc_ok_returns_true(self):
        """正常页面 evaluate 成功 → True，且探测带 1000ms 超时。"""
        s, page = self._session_with_page()
        html_loc = mock.Mock()
        html_loc.evaluate.return_value = 1
        page.locator.return_value = html_loc
        self.assertTrue(s.is_open())
        page.locator.assert_called_once_with("html")
        html_loc.evaluate.assert_called_once_with("1", timeout=1000)

    def test_local_page_closed_short_circuits(self):
        """本地已判关闭则直接 False，不再发起 RPC。"""
        s, page = self._session_with_page()
        page.is_closed.return_value = True
        self.assertFalse(s.is_open())
        page.locator.assert_not_called()

    def test_local_disconnected_short_circuits(self):
        s, page = self._session_with_page()
        page.is_closed.return_value = False
        s._browser.is_connected.return_value = False
        self.assertFalse(s.is_open())
        page.locator.assert_not_called()


# ============================================================
# 搜索生命周期不受影响
# ============================================================

class TestSearchLifecycleUnaffected(unittest.TestCase):
    def test_search_still_uses_same_stack_lifecycle(self):
        """搜索契约保持：_new_session → search → finally release。"""
        from tju_info_retrieval.ui.worker import BrowserWorker

        worker = BrowserWorker()
        session = mock.Mock()
        service = mock.Mock()
        service.search.return_value = [mock.Mock()]
        worker._session = session  # 模拟 _new_session 已建立会话
        worker._new_session = lambda: (worker._session, service)
        done = []
        worker.search_done.connect(lambda rows: done.append(rows))
        worker.search(mock.Mock(research_direction="太赫兹"))
        self.assertEqual(len(done), 1)
        session.shutdown.assert_called_once()  # finally 释放
        # 授权状态导出发生在 release 之前（同栈）
        session.export_storage_state.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
