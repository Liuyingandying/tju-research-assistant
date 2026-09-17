#!/usr/bin/env python3
"""v0.13 B′ 万方详情按需解析测试（全部 mock，不开真浏览器）。

覆盖：
1. match_top_title 归一化精确匹配（纯逻辑）
2. DetailWorker title 模式：resolver 调用、detail_opened、同栈释放
3. DetailWorker title 模式失败：error 信号 + 仍释放
4. DetailWorker url 模式回归：不触发 resolver
5. DetailManager.open_detail_by_title：构造参数、防重、空标题拒绝
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

from tju_info_retrieval.browser.wanfang_resolver import (
    ResolverError,
    match_top_title,
)
from tju_info_retrieval.ui.detail_manager import DetailManager
from tju_info_retrieval.ui.detail_worker import DetailWorker

TITLE = "基于太赫兹成像技术的GFRP复合材料缺陷检测研究"


class TestMatchTopTitle(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(match_top_title(TITLE, TITLE))

    def test_normalized_match(self):
        # 高亮 span 不影响、空白/标点/大小写归一
        self.assertTrue(match_top_title("基于 太赫兹成像 技术 的研究", "基于太赫兹成像技术的研究"))
        self.assertTrue(match_top_title("THz Imaging: A Review.", "thz imaging a review"))

    def test_mismatch(self):
        self.assertFalse(match_top_title("另一篇论文", TITLE))

    def test_empty_inputs(self):
        self.assertFalse(match_top_title("", TITLE))
        self.assertFalse(match_top_title(TITLE, ""))
        self.assertFalse(match_top_title(None, TITLE))

    def test_partial_is_not_match(self):
        """严谨模式：top-1 为目标标题子串也算不匹配。"""
        self.assertFalse(match_top_title("太赫兹成像技术研究", TITLE))


class TestDetailWorkerTitleMode(unittest.TestCase):
    """DetailWorker title 模式：resolver 同栈调用与 finally 释放。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_worker(self, title=TITLE):
        worker = DetailWorker(None, title=title)
        captured = {"opened": [], "error": [], "status": []}
        worker.detail_opened.connect(lambda t: captured["opened"].append(t))
        worker.error.connect(lambda m: captured["error"].append(m))
        worker.status.connect(lambda m: captured["status"].append(m))
        return worker, captured

    def _patch_env(self):
        session = mock.Mock()
        session.is_open.return_value = False  # 等待循环立即退出
        patchers = [
            mock.patch("tju_info_retrieval.ui.detail_worker.DetailBrowserSession"),
            mock.patch("tju_info_retrieval.ui.detail_worker.resolve_detail_page"),
        ]
        cls_session = patchers[0].start()
        cls_session.return_value = session
        mock_resolve = patchers[1].start()
        for p in patchers:
            self.addCleanup(p.stop)
        return session, mock_resolve

    def test_title_mode_calls_resolver_and_adopts(self):
        worker, captured = self._make_worker()
        session, mock_resolve = self._patch_env()
        worker.run()
        mock_resolve.assert_called_once()
        self.assertIs(mock_resolve.call_args.args[0], session)
        self.assertEqual(mock_resolve.call_args.args[1], TITLE)
        self.assertEqual(captured["opened"], [TITLE])
        self.assertEqual(captured["error"], [])
        session.shutdown.assert_called_once()  # 同栈 finally 释放
        self.assertIsNone(worker._session)

    def test_title_mode_resolver_failure_emits_error_and_releases(self):
        worker, captured = self._make_worker()
        session, mock_resolve = self._patch_env()
        mock_resolve.side_effect = ResolverError("万方首条结果与论文标题不匹配")
        worker.run()
        self.assertTrue(captured["error"], "resolver 失败应发出 error 信号")
        self.assertIn("打开详情页失败", captured["error"][0])
        self.assertEqual(captured["opened"], [])
        session.shutdown.assert_called_once()  # 失败仍同栈释放
        self.assertIsNone(worker._session)

    def test_url_mode_does_not_call_resolver(self):
        """url 模式回归：CNKI/IEEE 路径不触发 resolver。"""
        worker = DetailWorker("https://kns.cnki.net/x")
        captured = {"opened": [], "error": []}
        worker.detail_opened.connect(lambda t: captured["opened"].append(t))
        worker.error.connect(lambda m: captured["error"].append(m))
        session, mock_resolve = self._patch_env()
        worker.run()
        mock_resolve.assert_not_called()
        session.open_detail.assert_called_once_with("https://kns.cnki.net/x")
        self.assertEqual(captured["opened"], ["https://kns.cnki.net/x"])
        session.shutdown.assert_called_once()


class TestDetailManagerTitleMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_manager(self):
        manager = DetailManager()
        captured = {"status": [], "error": []}
        manager.status.connect(captured["status"].append)
        manager.error.connect(captured["error"].append)
        return manager, captured

    def test_open_detail_by_title_creates_title_mode_worker(self):
        manager, captured = self._make_manager()
        with mock.patch("tju_info_retrieval.ui.detail_manager.DetailWorker") as cls:
            worker = cls.return_value
            worker.isRunning.return_value = False
            manager.open_detail_by_title(f"  {TITLE}  ")
        cls.assert_called_once_with(None, None, title=TITLE.strip())
        worker.start.assert_called_once()
        self.assertTrue(any("万方" in s for s in captured["status"]))

    def test_open_detail_by_title_rejects_empty(self):
        manager, captured = self._make_manager()
        with mock.patch("tju_info_retrieval.ui.detail_manager.DetailWorker") as cls:
            manager.open_detail_by_title("   ")
        cls.assert_not_called()

    def test_open_detail_by_title_dedup_while_running(self):
        manager, captured = self._make_manager()
        with mock.patch("tju_info_retrieval.ui.detail_manager.DetailWorker") as cls:
            worker = cls.return_value
            worker.isRunning.return_value = True
            manager.open_detail_by_title(TITLE)
            manager.open_detail_by_title(TITLE)
        cls.assert_called_once()  # 运行中防重
        self.assertTrue(any("已在打开列表中" in s for s in captured["status"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
