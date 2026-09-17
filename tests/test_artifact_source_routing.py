#!/usr/bin/env python3
"""v0.17 Phase 2.2-D：成果类型 × 数据库 → effective source 路由测试。

覆盖：
- resolver 纯函数矩阵；
- GUI 默认行为（论文 checked / 专利 unchecked → 旧 source 行为完全一致）；
- 专利 + 万方 → request 含「万方专利」；
- 仅专利 + CNKI/IEEE → 阻止 + 明确提示；
- 论文+专利 + CNKI → 非阻断状态提示（仅检索论文）；
- strict 按 resolve 后 effective sources 判定（专利仅万方 + IEEE checkbox
  不误杀；论文+专利 + IEEE → 仍阻止）；
- 语言模式回归（中文/英文/双语不改变既有行为）；
- integration：SearchService sources=["万方专利"] 真实路由到 WanfangPatentAdapter。
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
from unittest import mock as _mock

from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.services.artifact_routing import (
    patent_has_no_supported_database,
    resolve_effective_sources,
)
from tju_info_retrieval.services.search_service import SearchService


# ============================================================
# 1. resolver 纯函数矩阵
# ============================================================

class TestResolveEffectiveSources(unittest.TestCase):
    def test_paper_single_source(self):
        self.assertEqual(resolve_effective_sources(["CNKI"], ["paper"]), ["CNKI"])
        self.assertEqual(resolve_effective_sources(["万方"], ["paper"]), ["万方"])
        self.assertEqual(resolve_effective_sources(["IEEE Xplore"], ["paper"]),
                         ["IEEE Xplore"])

    def test_patent_wanfang(self):
        self.assertEqual(resolve_effective_sources(["万方"], ["patent"]), ["万方专利"])

    def test_paper_patent_wanfang(self):
        self.assertEqual(resolve_effective_sources(["万方"], ["paper", "patent"]),
                         ["万方", "万方专利"])

    def test_paper_patent_three_sources(self):
        self.assertEqual(
            resolve_effective_sources(["CNKI", "万方", "IEEE Xplore"],
                                      ["paper", "patent"]),
            ["CNKI", "万方", "IEEE Xplore", "CNKI专利", "万方专利"],
        )

    def test_patent_cnki_supported(self):
        """v0.17 2.2-F：patent + CNKI → CNKI专利。"""
        self.assertEqual(resolve_effective_sources(["CNKI"], ["patent"]),
                         ["CNKI专利"])

    def test_patent_cnki_wanfang_joint(self):
        self.assertEqual(resolve_effective_sources(["CNKI", "万方"], ["patent"]),
                         ["CNKI专利", "万方专利"])

    def test_patent_only_ieee_empty(self):
        """patent + IEEE → 仍无有效来源（IEEE 无专利 adapter）。"""
        self.assertEqual(resolve_effective_sources(["IEEE Xplore"], ["patent"]), [])
        self.assertEqual(
            resolve_effective_sources(["CNKI", "IEEE Xplore"], ["patent"]),
            ["CNKI专利"])

    def test_none_artifacts_legacy_paper(self):
        """None = legacy 调用方未提供成果类型 → 默认论文（旧调用兼容）。"""
        self.assertEqual(resolve_effective_sources(["CNKI", "万方"], None),
                         ["CNKI", "万方"])
        self.assertEqual(resolve_effective_sources(["万方"], None), ["万方"])

    def test_explicit_empty_artifacts_no_paper_fallback(self):
        """[] = GUI 明确选择零种成果类型 → 返回 []，不得回退 paper。"""
        self.assertEqual(resolve_effective_sources(["万方"], []), [])
        self.assertEqual(resolve_effective_sources(["CNKI", "万方"], []),
                         [])

    def test_dedupe_and_order(self):
        self.assertEqual(
            resolve_effective_sources(["万方", "万方"], ["paper", "patent"]),
            ["万方", "万方专利"])

    def test_news_only_no_database(self):
        """news 不依赖数据库选择：一个库不勾也可执行。"""
        self.assertEqual(resolve_effective_sources([], ["news"]),
                         ["天津大学新闻网"])

    def test_news_with_cnki(self):
        self.assertEqual(resolve_effective_sources(["CNKI"], ["news"]),
                         ["天津大学新闻网"])

    def test_paper_news_cnki(self):
        self.assertEqual(resolve_effective_sources(["CNKI"], ["paper", "news"]),
                         ["CNKI", "天津大学新闻网"])

    def test_patent_news_wanfang(self):
        self.assertEqual(resolve_effective_sources(["万方"], ["patent", "news"]),
                         ["万方专利", "天津大学新闻网"])

    def test_all_artifacts_all_databases(self):
        self.assertEqual(
            resolve_effective_sources(["CNKI", "万方", "IEEE Xplore"],
                                      ["paper", "patent", "news"]),
            ["CNKI", "万方", "IEEE Xplore", "CNKI专利", "万方专利",
             "天津大学新闻网"],
        )

    def test_patent_has_no_supported_database_helper(self):
        self.assertFalse(patent_has_no_supported_database(["CNKI"]))
        self.assertTrue(patent_has_no_supported_database(["IEEE Xplore"]))
        self.assertFalse(patent_has_no_supported_database(["万方"]))
        self.assertFalse(patent_has_no_supported_database(["CNKI", "万方"]))


# ============================================================
# 2/3/4/5. GUI 行为
# ============================================================

class TestGuiArtifactRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        self.window = MainWindow()
        self.captured = []
        self.window.search_requested.connect(self.captured.append)
        self.window.input_direction.setText("太赫兹")

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def _search(self):
        self.window._on_search()

    def _artifacts(self, paper=True, patent=False):
        self.window.chk_artifact_paper.setChecked(paper)
        self.window.chk_artifact_patent.setChecked(patent)

    def _dbs(self, cnki=True, wanfang=False, ieee=False):
        self.window.chk_cnki.setChecked(cnki)
        self.window.chk_wanfang.setChecked(wanfang)
        self.window.chk_ieee.setChecked(ieee)

    def test_default_artifacts_paper_checked_patent_unchecked(self):
        self.assertTrue(self.window.chk_artifact_paper.isChecked())
        self.assertFalse(self.window.chk_artifact_patent.isChecked())

    def test_both_artifacts_unchecked_blocked(self):
        """hotfix（人工 GUI 验收 Case B）：论文/专利全未勾选 → 阻止 +
        精确提示，不自动回退 paper、不发请求。"""
        self._artifacts(paper=False, patent=False)
        self._dbs(cnki=False, wanfang=True, ieee=False)
        with mock.patch.object(QMessageBox, "warning") as warn:
            self._search()
        warn.assert_called_once()
        self.assertEqual(warn.call_args.args[2], "请至少选择一种成果类型。")
        self.assertEqual(self.captured, [])  # SearchService 未被调用

    def test_news_default_unchecked(self):
        self.assertFalse(self.window.chk_artifact_news.isChecked())

    def test_news_only_no_database_executes(self):
        """仅新闻 + 数据库全不勾 → 天津大学新闻网 可执行。"""
        self._artifacts(paper=False, patent=False)
        self.window.chk_artifact_news.setChecked(True)
        self._dbs(cnki=False, wanfang=False, ieee=False)
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].sources, ["天津大学新闻网"])

    def test_paper_news_cnki_joint(self):
        self._artifacts(paper=True, patent=False)
        self.window.chk_artifact_news.setChecked(True)
        self._dbs(cnki=True, wanfang=False, ieee=False)
        self._search()
        self.assertEqual(self.captured[0].sources, ["CNKI", "天津大学新闻网"])

    def test_patent_news_cnki_both_execute(self):
        """v0.17 2.2-F：专利+新闻 + CNKI → CNKI专利 + 新闻 均执行。"""
        self._artifacts(paper=False, patent=True)
        self.window.chk_artifact_news.setChecked(True)
        self._dbs(cnki=True, wanfang=False, ieee=False)
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].sources, ["CNKI专利", "天津大学新闻网"])

    def test_all_three_wanfang_joint(self):
        self._artifacts(paper=True, patent=True)
        self.window.chk_artifact_news.setChecked(True)
        self._dbs(cnki=False, wanfang=True, ieee=False)
        self._search()
        self.assertEqual(self.captured[0].sources,
                         ["万方", "万方专利", "天津大学新闻网"])

    def test_all_three_all_databases_joint(self):
        self._artifacts(paper=True, patent=True)
        self.window.chk_artifact_news.setChecked(True)
        self._dbs(cnki=True, wanfang=True, ieee=True)
        self._search()
        self.assertEqual(self.captured[0].sources,
                         ["CNKI", "万方", "IEEE Xplore", "CNKI专利", "万方专利",
                          "天津大学新闻网"])

    def test_news_source_hint_visibility_toggles(self):
        # 离屏测试窗口未 show()，用 isHidden（显式隐藏态）断言
        self.assertTrue(self.window.label_news_source.isHidden())
        self.window.chk_artifact_news.setChecked(True)
        self.assertFalse(self.window.label_news_source.isHidden())
        self.assertIn("天津大学新闻网", self.window.label_news_source.text())

    def test_news_english_blocked_with_language_message(self):
        """仅新闻 + 英文 → 阻止 + 切换语言提示。"""
        self.window.lang_buttons["en"].setChecked(True)
        self.window._apply_language_mode()
        self._artifacts(paper=False, patent=False)
        self.window.chk_artifact_news.setChecked(True)
        self._dbs(cnki=False, wanfang=False, ieee=False)
        with mock.patch.object(QMessageBox, "warning") as warn:
            self._search()
        warn.assert_called_once()
        self.assertIn("仅支持中文新闻检索", warn.call_args.args[2])
        self.assertEqual(self.captured, [])

    def test_paper_news_english_ieee_news_skipped_notice(self):
        """论文+新闻 + 英文 + IEEE → IEEE 可执行，新闻跳过 + 提示。"""
        self.window.lang_buttons["en"].setChecked(True)
        self.window._apply_language_mode()
        self._artifacts(paper=True, patent=False)
        self.window.chk_artifact_news.setChecked(True)
        self._dbs(cnki=False, wanfang=False, ieee=True)
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].sources, ["IEEE Xplore"])
        self.assertIn("中文新闻", self.window.label_status.text())

    def test_strict_news_only_ieee_checkbox_not_blocked(self):
        """仅新闻 + IEEE 复选框 + strict → effective 无 IEEE → 不误杀。"""
        self._artifacts(paper=False, patent=False)
        self.window.chk_artifact_news.setChecked(True)
        self._dbs(cnki=False, wanfang=False, ieee=True)
        self.window.combo_affiliation_mode.setCurrentIndex(1)  # strict
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].sources, ["天津大学新闻网"])

    def test_strict_paper_news_ieee_blocked(self):
        """论文+新闻 + IEEE + strict → effective 含 IEEE → 仍阻止。"""
        self._artifacts(paper=True, patent=False)
        self.window.chk_artifact_news.setChecked(True)
        self._dbs(cnki=False, wanfang=False, ieee=True)
        self.window.combo_affiliation_mode.setCurrentIndex(1)  # strict
        with mock.patch.object(QMessageBox, "warning") as warn:
            self._search()
        warn.assert_called_once()
        self.assertIn("IEEE Xplore 暂不支持严格匹配", warn.call_args.args[2])
        self.assertEqual(self.captured, [])

    def test_news_only_affiliation_strict_blocked(self):
        """全新闻来源 + 单位 + strict → 必然 0 条，提交前阻止。"""
        self._artifacts(paper=False, patent=False)
        self.window.chk_artifact_news.setChecked(True)
        self._dbs(cnki=False, wanfang=False, ieee=False)
        self.window.input_author_affiliation.setText("天津大学")
        self.window.combo_affiliation_mode.setCurrentIndex(1)  # strict
        with mock.patch.object(QMessageBox, "warning") as warn:
            self._search()
        warn.assert_called_once()
        self.assertIn("严格单位匹配将无法保留新闻结果", warn.call_args.args[2])
        self.assertEqual(self.captured, [])

    def test_news_only_affiliation_soft_allowed(self):
        self._artifacts(paper=False, patent=False)
        self.window.chk_artifact_news.setChecked(True)
        self._dbs(cnki=False, wanfang=False, ieee=False)
        self.window.input_author_affiliation.setText("天津大学")
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].sources, ["天津大学新闻网"])

    def test_default_behavior_identical_to_legacy(self):
        """默认（论文 + CNKI）→ sources == ["CNKI"]，与改动前完全一致。"""
        self._dbs(cnki=True, wanfang=False, ieee=False)
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].sources, ["CNKI"])
        self.assertEqual(self.captured[0].databases, ["CNKI"])

    def test_legacy_default_all_cnki_matches_old(self):
        self._dbs(cnki=True, wanfang=False, ieee=False)
        self._search()
        self.assertEqual(self.captured[0].sources, ["CNKI"])

    def test_paper_only_multi_sources_unchanged(self):
        self._dbs(cnki=True, wanfang=True, ieee=True)
        self._search()
        self.assertEqual(self.captured[0].sources,
                         ["CNKI", "万方", "IEEE Xplore"])

    def test_patent_wanfang_enters_request(self):
        """勾专利 + 万方 → request.sources 含「万方专利」。"""
        self._artifacts(paper=False, patent=True)
        self._dbs(cnki=False, wanfang=True, ieee=False)
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].sources, ["万方专利"])

    def test_paper_patent_wanfang_joint(self):
        self._artifacts(paper=True, patent=True)
        self._dbs(cnki=False, wanfang=True, ieee=False)
        self._search()
        self.assertEqual(self.captured[0].sources, ["万方", "万方专利"])

    def test_paper_patent_all_sources_joint(self):
        self._artifacts(paper=True, patent=True)
        self._dbs(cnki=True, wanfang=True, ieee=True)
        self._search()
        self.assertEqual(self.captured[0].sources,
                         ["CNKI", "万方", "IEEE Xplore", "CNKI专利", "万方专利"])

    def test_patent_only_cnki_submits(self):
        """v0.17 2.2-F：仅专利 + CNKI → 可提交（CNKI专利）。"""
        self._artifacts(paper=False, patent=True)
        self._dbs(cnki=True, wanfang=False, ieee=False)
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].sources, ["CNKI专利"])

    def test_patent_only_cnki_wanfang_two_sources(self):
        self._artifacts(paper=False, patent=True)
        self._dbs(cnki=True, wanfang=True, ieee=False)
        self._search()
        self.assertEqual(self.captured[0].sources, ["CNKI专利", "万方专利"])

    def test_patent_only_ieee_blocked_with_new_message(self):
        """仅专利 + IEEE → 阻止 + 新提示（支持 CNKI 和万方）。"""
        self._artifacts(paper=False, patent=True)
        self._dbs(cnki=False, wanfang=False, ieee=True)
        with mock.patch.object(QMessageBox, "warning") as warn:
            self._search()
        warn.assert_called_once()
        self.assertEqual(
            warn.call_args.args[2],
            "当前专利检索支持 CNKI 和万方，请至少勾选其中一个数据源。")
        self.assertEqual(self.captured, [])

    def test_paper_patent_cnki_both_execute(self):
        """v0.17 2.2-F：论文+专利 + CNKI → CNKI 论文 + CNKI专利 均执行。"""
        self._artifacts(paper=True, patent=True)
        self._dbs(cnki=True, wanfang=False, ieee=False)
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].sources, ["CNKI", "CNKI专利"])

    # ---------- strict 按 effective sources 判定 ----------

    def test_strict_patent_only_wanfang_ieee_checkbox_not_blocked(self):
        """仅专利 + 万方+IEEE 复选框 + strict：effective 无 IEEE → 不误杀。"""
        self._artifacts(paper=False, patent=True)
        self._dbs(cnki=False, wanfang=True, ieee=True)
        self.window.combo_affiliation_mode.setCurrentIndex(1)  # strict
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].sources, ["万方专利"])

    def test_strict_paper_patent_with_ieee_blocked(self):
        """论文+专利 + 万方+IEEE + strict：effective 含 IEEE Xplore → 仍阻止。"""
        self._artifacts(paper=True, patent=True)
        self._dbs(cnki=False, wanfang=True, ieee=True)
        self.window.combo_affiliation_mode.setCurrentIndex(1)  # strict
        with mock.patch.object(QMessageBox, "warning") as warn:
            self._search()
        warn.assert_called_once()
        self.assertIn("IEEE Xplore 暂不支持严格匹配", warn.call_args.args[2])
        self.assertEqual(self.captured, [])

    def test_strict_paper_without_ieee_not_blocked(self):
        self._artifacts(paper=True, patent=False)
        self._dbs(cnki=True, wanfang=True, ieee=False)
        self.window.combo_affiliation_mode.setCurrentIndex(1)  # strict
        self._search()
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.captured[0].sources, ["CNKI", "万方"])

    # ---------- 语言模式回归 ----------

    def test_english_mode_ieee_only_paper_unchanged(self):
        """英文模式：IEEE 勾选 -> effective 含 IEEE Xplore（既有行为）。"""
        self.window.lang_buttons["en"].setChecked(True)
        self.window._apply_language_mode()
        self._dbs(cnki=False, wanfang=False, ieee=True)
        self._search()
        self.assertEqual(self.captured[0].sources, ["IEEE Xplore"])

    def test_english_mode_patent_only_blocked(self):
        """英文模式（万方禁用）下仅专利 → 无有效来源 → 明确阻止。"""
        self.window.lang_buttons["en"].setChecked(True)
        self.window._apply_language_mode()
        self._artifacts(paper=False, patent=True)
        self._dbs(cnki=False, wanfang=False, ieee=True)
        with mock.patch.object(QMessageBox, "warning") as warn:
            self._search()
        warn.assert_called_once()
        self.assertEqual(self.captured, [])


# ============================================================
# 6. integration：万方专利 经生产链路由到 registry adapter
# ============================================================

class TestExpansionDirectionHint(unittest.TestCase):
    """v0.17 Phase 2.5-B：扩展方向 applicability 非阻塞提示（status bar）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        self.window = MainWindow()
        self.captured = []
        self.window.search_requested.connect(self.captured.append)
        self.window.input_direction.setText("太赫兹")

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def _artifacts(self, paper=True, patent=False, news=False):
        self.window.chk_artifact_paper.setChecked(paper)
        self.window.chk_artifact_patent.setChecked(patent)
        self.window.chk_artifact_news.setChecked(news)

    def _search(self):
        self.window._on_search()

    def status_text(self):
        return self.window.label_status.text()

    def test_paper_patent_review_one_hint(self):
        self._artifacts(paper=True, patent=True)
        self.window.chk_cnki.setChecked(True)
        self.window.chk_wanfang.setChecked(False)
        self.window.chk_ieee.setChecked(False)
        self.window.combo_expansion.setCurrentText("综述")
        self._search()
        self.assertEqual(len(self.captured), 1)
        assert "专利结果将按综合主题模式检索" in self.status_text()

    def test_paper_only_review_no_hint(self):
        self._artifacts(paper=True, patent=False)
        self.window.chk_cnki.setChecked(True)
        self.window.combo_expansion.setCurrentText("综述")
        self._search()
        self.assertNotIn("扩展方向", self.status_text())

    def test_general_no_hint(self):
        self._artifacts(paper=True, patent=True)
        self.window.chk_cnki.setChecked(True)
        self.window.combo_expansion.setCurrentText("综合")
        self._search()
        self.assertNotIn("扩展方向", self.status_text())

    def test_wanfang_paper_review_hint(self):
        self._artifacts(paper=True)
        self.window.chk_cnki.setChecked(False)
        self.window.chk_wanfang.setChecked(True)
        self.window.combo_expansion.setCurrentText("综述")
        self._search()
        self.assertEqual(len(self.captured), 1)
        assert "万方论文" in self.status_text()

    def test_pure_person_review_no_hint(self):
        self._artifacts(paper=True)
        self.window.input_direction.setText("")
        self.window.input_author_name.setText("张学")
        self.window.chk_cnki.setChecked(True)
        self.window.combo_expansion.setCurrentText("综述")
        self._search()
        self.assertEqual(len(self.captured), 1)
        assert self.captured[0].research_direction == ""
        self.assertNotIn("扩展方向", self.status_text())


class TestSearchServiceRouting(unittest.TestCase):
    def test_default_registry_has_wanfang_patent(self):
        registry = SearchService._default_registry()
        self.assertIn("万方专利", registry.available_sources())
        from tju_info_retrieval.sources.wanfang_patent import WanfangPatentAdapter
        self.assertIs(registry.get("万方专利"), WanfangPatentAdapter)

    def test_search_service_routes_wanfang_patent_source(self):
        """sources=["万方专利"] → 真实调用 WanfangPatentAdapter。"""
        from tju_info_retrieval.sources.wanfang_patent import WanfangPatentAdapter
        session = mock.Mock()
        session.check_tju_auth.return_value = "logged_in"
        req = QueryRequest(research_direction="太赫兹", sources=["万方专利"],
                           result_count=5)
        with mock.patch.object(
            WanfangPatentAdapter, "search", return_value=[]
        ) as pat_search:
            service = SearchService(session)
            service.search(req)
        pat_search.assert_called_once()
        # SearchService 传扩展 Boolean 串作 keyword；万方专利 adapter 内部
        # 以 query_context.research_direction 取原始词（test_wanfang_patent 覆盖）
        self.assertIsInstance(pat_search.call_args.args[0], str)
        self.assertEqual(pat_search.call_args.args[1], 5)  # count 为位置参数
        self.assertEqual(pat_search.call_args.kwargs["query_context"], req)


if __name__ == "__main__":
    unittest.main(verbosity=2)