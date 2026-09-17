#!/usr/bin/env python3
"""v0.13 Phase 8.5：详情页生命周期修复测试（全部 mock，不开真浏览器）。

覆盖：
1. open_detail 创建 session 并打开详情页
2. detail_opened 信号正常发送
3. finally release 被调用（同栈销毁）
4. 第二次打开详情不会触发旧 session 的跨栈 shutdown
5. wait_until_closed 语义（页面全关/超时/context 失效）
6. 异常路径：open_detail 失败仍释放 + error 信号
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

from tju_info_retrieval.ui.worker import BrowserWorker

URL1 = "https://kns.cnki.net/kcms2/article/abstract?v=a1"
URL2 = "https://kns.cnki.net/kcms2/article/abstract?v=a2"


class TestDetailLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.captured = {"detail_opened": [], "error": [], "status": []}
        self.worker = BrowserWorker()
        self.worker.detail_opened.connect(
            lambda u: self.captured["detail_opened"].append(u)
        )
        self.worker.error.connect(lambda m: self.captured["error"].append(m))
        self.worker.status_changed.connect(
            lambda m: self.captured["status"].append(m)
        )
        # patch worker 模块中的 BrowserSession 类：每次实例化返回新 mock
        self.sessions: list = []
        self._preset_queue: list = []
        patcher = mock.patch("tju_info_retrieval.ui.worker.BrowserSession")
        self._bs_patcher = patcher
        self.mock_bs_cls = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_bs_cls.side_effect = self._make_session

    def _make_session(self):
        if self._preset_queue:
            return self._preset_queue.pop(0)  # 预配置的会话优先生效
        s = mock.Mock()
        s.wait_until_closed.return_value = True  # 默认：模拟用户立即关闭
        self.sessions.append(s)
        return s

    def _preset_session(self):
        """预创建下一个会话 mock（供在触发 open_detail 前配置行为）。"""
        s = mock.Mock()
        s.wait_until_closed.return_value = True
        self.sessions.append(s)
        self._preset_queue.append(s)
        return s

    # ---- 1/2/3. 创建 session、信号、finally 释放 ----

    def test_open_detail_creates_session_and_opens(self):
        self.worker.open_detail(URL1)
        self.assertEqual(len(self.sessions), 1)
        self.sessions[0].open_detail.assert_called_once_with(URL1)
        self.sessions[0].wait_until_closed.assert_called_once()

    def test_detail_opened_signal_emitted(self):
        self.worker.open_detail(URL1)
        self.assertEqual(self.captured["detail_opened"], [URL1])
        self.assertEqual(self.captured["error"], [])

    def test_finally_release_called_same_stack(self):
        """finally 释放：shutdown 在同一 slot 栈内被调用一次。"""
        self.worker.open_detail(URL1)
        s1 = self.sessions[0]
        s1.shutdown.assert_called_once()  # 同栈 finally 释放
        self.assertIsNone(self.worker._session)  # 会话已归还为空

    # ---- 4. 第二次打开不触发旧 session 跨栈 shutdown ----

    def test_second_open_no_cross_stack_shutdown(self):
        self.worker.open_detail(URL1)
        s1 = self.sessions[0]
        self.assertEqual(s1.shutdown.call_count, 1)  # 第一次 finally 内
        self.assertIsNone(self.worker._session)

        self.worker.open_detail(URL2)
        s2 = self.sessions[1]
        # 关键回归：旧 session 不会被第二次任务跨栈 shutdown
        self.assertEqual(s1.shutdown.call_count, 1)
        self.assertEqual(s2.shutdown.call_count, 1)  # 第二次自己的 finally
        self.assertEqual(s2.open_detail.call_args[0][0], URL2)
        self.assertEqual(len(self.sessions), 2)  # 新建会话而非复用

    def test_detail_then_search_no_cross_stack_shutdown(self):
        """详情页之后的搜索任务同样不跨栈触碰旧 session。"""
        self.worker.open_detail(URL1)
        s1 = self.sessions[0]
        self.worker.search(mock.Mock(research_direction="太赫兹"))
        # search 的 _new_session 不会对 s1.shutdown（s1 已在第一次 finally 释放）
        self.assertEqual(s1.shutdown.call_count, 1)
        self.assertEqual(len(self.sessions), 2)

    # ---- 6. 异常路径 ----

    def test_open_detail_error_still_releases(self):
        s1 = self._preset_session()
        s1.open_detail.side_effect = Exception("导航失败")
        self.worker.open_detail(URL1)
        self.assertTrue(self.captured["error"])
        s1.shutdown.assert_called_once()  # finally 仍释放
        self.assertIsNone(self.worker._session)

    def test_release_called_on_timeout(self):
        """wait_until_closed 超时返回 False 后仍释放（超时保护语义）。"""
        s1 = self._preset_session()
        s1.wait_until_closed.return_value = False
        self.worker.open_detail(URL1)
        s1.shutdown.assert_called_once()
        self.assertIsNone(self.worker._session)


# ============================================================
# wait_until_closed 语义（BrowserSession 直接实例化，不启动浏览器）
# ============================================================

class TestWaitUntilClosed(unittest.TestCase):
    def _session_with(self, pages):
        from tju_info_retrieval.browser.session import BrowserSession

        session = BrowserSession()
        ctx = mock.Mock()
        ctx.pages = pages
        session._context = ctx
        return session

    def test_all_pages_closed_returns_true(self):
        session = self._session_with(pages=[])
        self.assertTrue(session.wait_until_closed(timeout_s=0.2, poll_s=0.05))

    def test_context_dead_returns_true(self):
        from tju_info_retrieval.browser.session import BrowserSession

        session = BrowserSession()
        ctx = mock.Mock()
        type(ctx).pages = mock.PropertyMock(side_effect=Exception("closed"))
        session._context = ctx
        self.assertTrue(session.wait_until_closed(timeout_s=0.2, poll_s=0.05))

    def test_timeout_returns_false(self):
        alive_page = mock.Mock()
        session = self._session_with(pages=[alive_page])
        self.assertFalse(session.wait_until_closed(timeout_s=0.3, poll_s=0.05))

    def test_default_timeout_is_1800(self):
        import inspect

        from tju_info_retrieval.browser.session import BrowserSession
        sig = inspect.signature(BrowserSession.wait_until_closed)
        self.assertEqual(sig.parameters["timeout_s"].default, 1800.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
