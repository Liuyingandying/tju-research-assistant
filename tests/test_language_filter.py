#!/usr/bin/env python3
"""v0.9.1 语言筛选测试：模式默认值、来源联动、请求前过滤、翻译链路（offscreen）。

v0.13 恢复英文模式（en=仅 IEEE Xplore），并新增约束：最终 sources 含
IEEE Xplore 时禁止严格匹配（英文库单位元数据不足会大量误删）——
en+strict、双语勾选 IEEE+strict 在 _on_search 提交前拦截；
zh+strict、双语不勾 IEEE+strict、en+soft 正常放行。
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

from tju_info_retrieval.services.query_builder import QueryBuilder
from tju_info_retrieval.services.query_expansion import QueryExpansionService
from tju_info_retrieval.services.query_translator import QueryTranslator
from tju_info_retrieval.ui.main_window import MainWindow


class TestLanguageFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    # ---------- 1. 默认模式 ----------

    def test_default_mode_bilingual(self):
        self.assertEqual(self.window.language_mode, "bilingual")
        self.assertEqual(self.window.lang_group.checkedButton().text(), "中英双语")
        self.assertTrue(self.window.chk_cnki.isEnabled())
        self.assertTrue(self.window.chk_wanfang.isEnabled())
        self.assertTrue(self.window.chk_ieee.isEnabled())
        for btn in self.window.lang_buttons.values():
            self.assertTrue(btn.isEnabled())
            self.assertTrue(btn.isCheckable())

    def test_language_buttons_exclusive(self):
        self.window.lang_buttons["zh"].click()
        self.assertTrue(self.window.lang_buttons["zh"].isChecked())
        self.assertFalse(self.window.lang_buttons["bilingual"].isChecked())

    # ---------- 2. 英文模式（v0.13 恢复） ----------

    def test_en_mode_restored(self):
        """三个语言按钮齐全：中文 / 英文 / 中英双语。"""
        self.assertIn("en", self.window.lang_buttons)
        self.assertIn("英文", [btn.text() for btn in self.window.lang_buttons.values()])
        self.assertEqual(
            set(self.window.lang_buttons.keys()), {"zh", "en", "bilingual"}
        )

    def test_en_mode_only_ieee(self):
        self.window.lang_buttons["en"].click()
        self.assertEqual(self.window.language_mode, "en")
        self.assertFalse(self.window.chk_cnki.isEnabled())
        self.assertFalse(self.window.chk_wanfang.isEnabled())
        self.assertTrue(self.window.chk_ieee.isEnabled())

        # 即使 CNKI/万方勾选被保留（禁用态），请求也只包含 IEEE
        self.window.input_direction.setText("太赫兹通信")
        self.window.chk_cnki.setChecked(True)
        self.window.chk_wanfang.setChecked(True)
        self.window.chk_ieee.setChecked(True)
        with mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
            query = m.emit.call_args[0][0]
        self.assertEqual(query.sources, ["IEEE Xplore"])

    def test_back_to_bilingual_restores_all(self):
        # 双语下勾选 CNKI + IEEE
        self.window.chk_cnki.setChecked(True)
        self.window.chk_wanfang.setChecked(True)
        self.window.chk_ieee.setChecked(True)
        # 切英文：CNKI/万方禁用（勾选保留）
        self.window.lang_buttons["en"].click()
        self.assertFalse(self.window.chk_cnki.isEnabled())
        self.assertTrue(self.window.chk_cnki.isChecked())
        # 切回双语：全部恢复且勾选状态仍在
        self.window.lang_buttons["bilingual"].click()
        self.assertTrue(self.window.chk_cnki.isEnabled())
        self.assertTrue(self.window.chk_wanfang.isEnabled())
        self.assertTrue(self.window.chk_ieee.isEnabled())
        self.assertTrue(self.window.chk_ieee.isChecked())
        self.window.input_direction.setText("太赫兹通信")
        with mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
            query = m.emit.call_args[0][0]
        self.assertEqual(query.sources, ["CNKI", "万方", "IEEE Xplore"])

    # ---------- 3. 严格匹配 × IEEE Xplore 组合约束（按最终 sources 判定） ----------

    def test_english_strict_blocked(self):
        """en + strict：提交前拦截，提示切换智能匹配，不发出请求。"""
        self.window.lang_buttons["en"].click()
        self.window.chk_ieee.setChecked(True)
        self.window.combo_affiliation_mode.setCurrentIndex(1)  # 严格匹配
        self.window.input_direction.setText("太赫兹通信")
        with mock.patch.object(QMessageBox, "warning", return_value=None) as warn, \
                mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
        self.assertEqual(warn.call_args[0][2], "英文检索暂不支持严格匹配，请切换为智能匹配。")
        m.emit.assert_not_called()

    def test_english_soft_allowed(self):
        """en + soft：正常放行，请求仅含 IEEE 且模式为 soft。"""
        self.window.lang_buttons["en"].click()
        self.window.chk_ieee.setChecked(True)
        self.window.input_direction.setText("太赫兹通信")
        with mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
            query = m.emit.call_args[0][0]
        self.assertEqual(query.sources, ["IEEE Xplore"])
        self.assertEqual(query.affiliation_filter_mode, "soft")

    def test_bilingual_with_ieee_strict_blocked(self):
        """双语 + 勾选 IEEE + strict：最终 sources 含 IEEE，提交前拦截。"""
        self.window.chk_cnki.setChecked(True)
        self.window.chk_wanfang.setChecked(True)
        self.window.chk_ieee.setChecked(True)
        self.window.combo_affiliation_mode.setCurrentIndex(1)  # 严格匹配
        self.window.input_direction.setText("太赫兹通信")
        with mock.patch.object(QMessageBox, "warning", return_value=None) as warn, \
                mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
        self.assertEqual(
            warn.call_args[0][2],
            "IEEE Xplore 暂不支持严格匹配：请切换为智能匹配，或取消勾选 IEEE Xplore。",
        )
        m.emit.assert_not_called()

    def test_bilingual_without_ieee_strict_allowed(self):
        """双语 + 不勾 IEEE + strict：最终 sources 无 IEEE，正常放行。"""
        self.window.chk_cnki.setChecked(True)
        self.window.chk_wanfang.setChecked(True)
        self.window.chk_ieee.setChecked(False)
        self.window.combo_affiliation_mode.setCurrentIndex(1)  # 严格匹配
        self.window.input_direction.setText("太赫兹通信")
        with mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
            query = m.emit.call_args[0][0]
        self.assertEqual(query.sources, ["CNKI", "万方"])
        self.assertEqual(query.affiliation_filter_mode, "strict")

    # ---------- 4. 中文模式 + 严格匹配不受影响 ----------

    def test_zh_strict_allowed(self):
        self.window.lang_buttons["zh"].click()
        self.window.chk_cnki.setChecked(True)
        self.window.chk_wanfang.setChecked(True)
        self.window.combo_affiliation_mode.setCurrentIndex(1)  # 严格匹配
        self.window.input_direction.setText("太赫兹通信")
        with mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
            query = m.emit.call_args[0][0]
        self.assertEqual(query.sources, ["CNKI", "万方"])
        self.assertEqual(query.affiliation_filter_mode, "strict")

    # ---------- 5. 中文模式 ----------

    def test_zh_mode_excludes_ieee(self):
        self.window.lang_buttons["zh"].click()
        self.assertEqual(self.window.language_mode, "zh")
        self.assertTrue(self.window.chk_cnki.isEnabled())
        self.assertTrue(self.window.chk_wanfang.isEnabled())
        self.assertFalse(self.window.chk_ieee.isEnabled())

        # 即使 IEEE 勾选被保留（禁用态），请求也不包含 IEEE
        self.window.input_direction.setText("太赫兹通信")
        self.window.chk_cnki.setChecked(True)
        self.window.chk_wanfang.setChecked(True)
        self.window.chk_ieee.setChecked(True)
        with mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
            query = m.emit.call_args[0][0]
        self.assertEqual(query.sources, ["CNKI", "万方"])

    # ---------- 6. 中文输入仍进入翻译/构建链路（英文模式全链路） ----------

    def test_chinese_input_reaches_translator_and_builder(self):
        self.window.chk_cnki.setChecked(True)
        self.window.chk_wanfang.setChecked(True)
        self.window.chk_ieee.setChecked(True)
        self.window.input_direction.setText("太赫兹通信")
        with mock.patch.object(self.window, "search_requested") as m:
            self.window._on_search()
            query = m.emit.call_args[0][0]
        # GUI 不拦截中文输入：请求原样携带中文关键词与全量来源
        self.assertEqual(query.research_direction, "太赫兹通信")
        self.assertEqual(query.sources, ["CNKI", "万方", "IEEE Xplore"])

        # 与 SearchService 相同的链路：Expansion → Translator → Builder
        expanded = QueryExpansionService().expand(
            query.research_direction, query.expansion_direction
        )
        concepts = QueryTranslator().translate_concepts(expanded, query.sources)
        ieee_query = QueryBuilder().build("IEEE Xplore", concepts["IEEE Xplore"])
        self.assertEqual(ieee_query, "(terahertz OR THz) AND (communication)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
