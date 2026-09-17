#!/usr/bin/env python3
"""v0.9.2 测试：IEEE 原始站 URL 重建纯函数 + “访问IEEE原始站”按钮（offscreen）。

覆盖：IEEE 代理 URL、相对 document 路径、非 IEEE URL、GUI 按钮触发、
无 document id 时安全失败。
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

from PySide6.QtWidgets import QApplication, QMessageBox

from tju_info_retrieval.sources.ieee import ieee_original_url, parse_document_id
from tju_info_retrieval.ui.main_window import MainWindow


class TestIeeeUrlUtils(unittest.TestCase):
    def test_relative_document_path(self):
        self.assertEqual(parse_document_id("/document/8663550/"), "8663550")
        self.assertEqual(
            ieee_original_url("/document/8663550/"),
            "https://ieeexplore.ieee.org/document/8663550/",
        )

    def test_full_proxy_url(self):
        proxied = (
            "https://ieeexplore-io-org-443.webvpn.p.lib.tju.edu.cn"
            "/document/123456/?ai=1&arnumber=123456"
        )
        self.assertEqual(parse_document_id(proxied), "123456")
        self.assertEqual(
            ieee_original_url(proxied),
            "https://ieeexplore.ieee.org/document/123456/",
        )
        # 网关型代理（路径带编码前缀）同样可解析
        gateway = (
            "https://p.lib.tju.edu.cn/http-77726476706e69737468656265737421"
            "e0f85288223e7c4377068ca88d52203b26a5/document/456789/"
        )
        self.assertEqual(
            ieee_original_url(gateway),
            "https://ieeexplore.ieee.org/document/456789/",
        )

    def test_original_ieee_url_roundtrip(self):
        url = "https://ieeexplore.ieee.org/document/98765/"
        self.assertEqual(ieee_original_url(url), url)

    def test_non_ieee_url_returns_none(self):
        for bad in (
            None,
            "",
            "https://kns.cnki.net/kcms2/article/abstract?v=abc123",
            "https://www.wanfangdata.com.cn/perio/hxydqx",
            "https://example.com/paper/123",
        ):
            self.assertIsNone(parse_document_id(bad), msg=str(bad))
            self.assertIsNone(ieee_original_url(bad), msg=str(bad))

    def test_non_numeric_document_returns_none(self):
        self.assertIsNone(parse_document_id("/document/abc/"))
        self.assertIsNone(ieee_original_url("/document/abc/"))


class TestIeeeOriginalButton(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    @staticmethod
    def _row(detail_url):
        return {
            "rank": 1,
            "title": "A survey on terahertz communications",
            "authors": ["Zhi Chen"],
            "source": "China Communications",
            "year": "2019",
            "document_type": "Magazine Article",
            "database": "IEEE Xplore",
            "detail_url": detail_url,
        }

    def test_button_exists_and_initially_disabled(self):
        self.assertEqual(self.window.btn_ieee_original.text(), "访问IEEE原始站")
        self.assertFalse(self.window.btn_ieee_original.isEnabled())

    def test_enabled_after_search_done_and_disabled_after_failure(self):
        self.window._on_search_done([self._row("/document/8663550/")])
        self.assertTrue(self.window.btn_ieee_original.isEnabled())
        with mock.patch.object(QMessageBox, "warning", return_value=None):
            self.window._on_search_failed("测试错误")
        self.assertFalse(self.window.btn_ieee_original.isEnabled())

    def test_button_connected(self):
        self.window.btn_ieee_original.setEnabled(True)
        with mock.patch.object(self.window, "_on_open_ieee_original") as m:
            self.window.btn_ieee_original.click()
            m.assert_called_once()

    def test_click_emits_original_url(self):
        self.window._on_search_done([self._row("/document/8663550/")])
        self.window.table.selectRow(0)
        with mock.patch.object(self.window, "open_detail_requested") as m:
            self.window.btn_ieee_original.click()
            m.emit.assert_called_once_with(
                "https://ieeexplore.ieee.org/document/8663550/"
            )
        self.assertIn("IEEE原始站", self.window.label_status.text())

    def test_click_with_proxy_url_emits_original(self):
        proxied = (
            "https://ieeexplore-io-org-443.webvpn.p.lib.tju.edu.cn/document/123456/"
        )
        self.window._on_search_done([self._row(proxied)])
        self.window.table.selectRow(0)
        with mock.patch.object(self.window, "open_detail_requested") as m:
            self.window.btn_ieee_original.click()
            m.emit.assert_called_once_with(
                "https://ieeexplore.ieee.org/document/123456/"
            )

    def test_no_document_id_safe_failure(self):
        # detail_url 为 None：提示且不发出请求
        self.window._on_search_done([self._row(None)])
        self.window.table.selectRow(0)
        with mock.patch.object(QMessageBox, "information", return_value=None) as info:
            with mock.patch.object(self.window, "open_detail_requested") as m:
                self.window.btn_ieee_original.click()
        m.emit.assert_not_called()
        self.assertIn("没有可用的IEEE原始站链接", info.call_args.args[2])

        # 非 IEEE URL（CNKI）：同样安全失败
        self.window._on_search_done(
            [self._row("https://kns.cnki.net/kcms2/article/abstract?v=abc")]
        )
        self.window.table.selectRow(0)
        with mock.patch.object(QMessageBox, "information", return_value=None) as info:
            with mock.patch.object(self.window, "open_detail_requested") as m:
                self.window.btn_ieee_original.click()
        m.emit.assert_not_called()
        self.assertIn("没有可用的IEEE原始站链接", info.call_args.args[2])

    def test_click_without_selection_no_emit(self):
        self.window._on_search_done([self._row("/document/8663550/")])
        with mock.patch.object(QMessageBox, "information", return_value=None) as info:
            with mock.patch.object(self.window, "open_detail_requested") as m:
                self.window.btn_ieee_original.click()
        m.emit.assert_not_called()
        self.assertIn("请先在表格中选择一条结果", info.call_args.args[2])

    def test_detail_button_behavior_unchanged(self):
        # 既有“打开全文页面”语义不受影响：仍直接打开 detail_url
        self.window._on_search_done([self._row("https://kns.cnki.net/x")])
        self.window.table.selectRow(0)
        with mock.patch.object(self.window, "open_detail_requested") as m:
            self.window._on_open_detail()
            m.emit.assert_called_once_with("https://kns.cnki.net/x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
