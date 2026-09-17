#!/usr/bin/env python3
"""正式测试：Browser profile 安全检查与配置复用（不启动真实浏览器）。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest
from unittest import mock

import poc_browser as poc
from tju_info_retrieval.browser.session import BrowserSession, CNKI_HOME_URL


class TestBrowserProfileSafety(unittest.TestCase):
    def test_profile_dir_under_runtime(self):
        self.assertTrue(str(poc.EDGE_PROFILE_DIR).startswith(str(poc.RUNTIME_DIR)))

    def test_profile_not_system_default(self):
        local = os.environ.get("LOCALAPPDATA", "")
        system_default = os.path.join(local, "Microsoft", "Edge", "User Data")
        self.assertNotEqual(str(poc.EDGE_PROFILE_DIR).lower(), system_default.lower())

    def test_chromium_sandbox_enabled(self):
        kwargs = poc.build_launch_kwargs(edge_path="C:/fake/msedge.exe")
        self.assertIs(kwargs.get("chromium_sandbox"), True)

    def test_headless_false(self):
        kwargs = poc.build_launch_kwargs()
        self.assertFalse(kwargs["headless"])

    def test_no_incognito(self):
        kwargs = poc.build_launch_kwargs()
        self.assertNotIn("--incognito", kwargs.get("args", []))

    def test_cnki_home_url(self):
        self.assertEqual(CNKI_HOME_URL, "https://www.cnki.net/")


class TestStartUrl(unittest.TestCase):
    def test_start_url_is_tju_platform(self):
        # 启动后默认打开天津大学平台，而不是 about:blank
        self.assertEqual(BrowserSession.START_URL, poc.DEFAULT_URL)
        self.assertNotEqual(BrowserSession.START_URL, "about:blank")
        self.assertIn("p.lib.tju.edu.cn", BrowserSession.START_URL)

    @mock.patch("tju_info_retrieval.browser.session.find_edge_executable", return_value="C:/fake/msedge.exe")
    def test_start_navigates_to_start_url(self, _mock_edge):
        # 验证 start() 实际把页面导航到天津大学平台入口（非 about:blank）
        with mock.patch("tju_info_retrieval.browser.session.sync_playwright") as mock_sync:
            page = mock.Mock()
            ctx = mock.Mock()
            ctx.pages = [page]
            pw = mock.Mock()
            pw.chromium.launch_persistent_context.return_value = ctx
            mock_sync.return_value.start.return_value = pw

            session = BrowserSession()
            session.start()

            page.goto.assert_called_once_with(
                poc.DEFAULT_URL, wait_until="domcontentloaded", timeout=60000
            )

    @mock.patch("tju_info_retrieval.browser.session.find_edge_executable", return_value="C:/fake/msedge.exe")
    def test_start_url_load_failure_is_not_fatal(self, _mock_edge):
        # 入口页面加载失败不应让 start() 崩溃（后续 goto 仍可导航）
        with mock.patch("tju_info_retrieval.browser.session.sync_playwright") as mock_sync:
            page = mock.Mock()
            page.goto.side_effect = RuntimeError("network down")
            ctx = mock.Mock()
            ctx.pages = [page]
            pw = mock.Mock()
            pw.chromium.launch_persistent_context.return_value = ctx
            mock_sync.return_value.start.return_value = pw

            session = BrowserSession()
            session.start()  # 不应抛出

            self.assertIs(session.page(), page)


class TestCheckTjuAuthWaiting(unittest.TestCase):
    """check_tju_auth 条件轮询：等待登录/授权条件，超时才返回 unknown。

    回归防护：不得依赖固定 sleep 单次采样（否则会与门户 SPA 客户端
    重定向到 /login 竞态，过早返回 unknown）。
    """

    @staticmethod
    def _make_session(page, ctx):
        session = BrowserSession()
        session._context = ctx
        session._page = page
        return session

    def test_logged_in_when_authorized_text(self):
        page = mock.Mock()
        page.url = "https://p.lib.tju.edu.cn/"
        page.inner_text.return_value = "首页 已授权 退出"
        ctx = mock.Mock()
        ctx.pages = [page]
        session = self._make_session(page, ctx)
        result = session.check_tju_auth(timeout_ms=3000)
        self.assertEqual(result, "logged_in")
        self.assertEqual(page.inner_text.call_args.args[0], "body")

    def test_login_page_when_url_has_login(self):
        page = mock.Mock()
        page.url = "https://p.lib.tju.edu.cn/login"
        page.inner_text.return_value = "登录 天津大学"
        ctx = mock.Mock()
        ctx.pages = [page]
        session = self._make_session(page, ctx)
        result = session.check_tju_auth(timeout_ms=3000)
        self.assertEqual(result, "login_page")

    def test_login_page_when_body_has_login_entry(self):
        page = mock.Mock()
        page.url = "https://p.lib.tju.edu.cn/"
        page.inner_text.return_value = "登录 天津大学图书馆"
        ctx = mock.Mock()
        ctx.pages = [page]
        session = self._make_session(page, ctx)
        result = session.check_tju_auth(timeout_ms=3000)
        self.assertEqual(result, "login_page")

    def test_waits_until_authorized_appears(self):
        # 模拟门户 SPA：前几次探测未就绪，随后客户端渲染出“已授权”
        counter = {"n": 0}

        def fake_body(_selector):
            counter["n"] += 1
            return "已授权" if counter["n"] >= 3 else "正在加载资源列表..."

        page = mock.Mock()
        page.url = "https://p.lib.tju.edu.cn/"
        page.inner_text.side_effect = fake_body
        ctx = mock.Mock()
        ctx.pages = [page]
        session = self._make_session(page, ctx)
        result = session.check_tju_auth(timeout_ms=10000)
        self.assertEqual(result, "logged_in")
        self.assertGreaterEqual(counter["n"], 3, "应轮询等待而非一次采样")

    def test_unknown_logs_context_on_timeout(self):
        page = mock.Mock()
        page.url = "https://p.lib.tju.edu.cn/"
        page.inner_text.return_value = "加载中..."
        ctx = mock.Mock()
        ctx.pages = [page]
        session = self._make_session(page, ctx)
        with self.assertLogs(
            "tju_info_retrieval.browser.session", level="WARNING"
        ) as cm:
            result = session.check_tju_auth(timeout_ms=100)
        self.assertEqual(result, "unknown")
        self.assertTrue(any("无法确定" in msg for msg in cm.output))

    def test_unknown_logs_traceback_on_read_failure(self):
        # 读取瞬间页面导航/执行上下文被销毁：记录 traceback，不静默吞掉
        page = mock.Mock()
        page.url = "https://p.lib.tju.edu.cn/"
        page.inner_text.side_effect = RuntimeError("Execution context was destroyed")
        ctx = mock.Mock()
        ctx.pages = [page]
        session = self._make_session(page, ctx)
        with self.assertLogs(
            "tju_info_retrieval.browser.session", level="WARNING"
        ) as cm:
            result = session.check_tju_auth(timeout_ms=100)
        self.assertEqual(result, "unknown")
        joined = "\n".join(cm.output)
        self.assertIn("Execution context was destroyed", joined)
        self.assertIn("Traceback", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
