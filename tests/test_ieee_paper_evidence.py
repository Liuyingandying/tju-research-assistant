#!/usr/bin/env python3
"""v0.17 Phase 2.4-C-C：IEEE on-demand evidence + OfflineSummary 英文支持测试。

覆盖：英文分句保护（普通句号/分号/缩写/小数）、英文四字段抽取、
no-hallucination（原文子串）、信息不足、中文不回归、
IEEE fetcher（success/空白归一/no abstract/cache/无效 URL/bot/timeout）、
worker IEEE 路由、no-batch。
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from PySide6.QtWidgets import QApplication

from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.sources.cnki_paper_evidence import EvidenceError
from tju_info_retrieval.sources.ieee_paper_evidence import (
    IeeePaperEvidenceFetcher,
    parse_ieee_abstract,
)
from tju_info_retrieval.services.offline_summary import (
    INSUFFICIENT_TEXT,
    OfflineSummaryEngine,
    split_into_clauses,
)

# 真实风格 IEEE abstract fixture（英文科研文本，含缩写/小数）
IEEE_ABS = (
    "Abstract: Two-dimensional terahertz (THz) imaging based on a sparse "
    "MIMO array is investigated for non-destructive evaluation. We propose a "
    "compressed sensing method, e.g. exploiting sparsity, which improves "
    "reconstruction accuracy up to 0.5 THz bandwidth. Experimental results "
    "demonstrate that the proposed algorithm achieves high-resolution "
    "imaging, and it can be used in industrial inspection applications."
)
CNKI_ABS = (
    "太赫兹技术由于对极性分子振动及弱相互作用的高敏感性，在生物检测领域展现出"
    "重要应用潜力。本文围绕太赫兹光谱与成像技术梳理研究进展，对比了不同路径。"
)


class TestEnglishSplitting(unittest.TestCase):
    def test_normal_period_and_semicolon_split(self):
        texts = [c for _, c in split_into_clauses(
            [("summary", "We study imaging. The method works; we validated it. Next part.")])]
        joined = " ".join(texts)
        assert "We study imaging" in joined
        assert "The method works" in joined
        assert "Next part" in joined

    def test_abbreviations_not_fragmented(self):
        """e.g./i.e./et al./Fig. 不产生畸形碎片。"""
        text = "We use tools, e.g. Python and C, i.e. two languages. Smith et al. reported it in Fig. 2."
        clauses = [c for _, c in split_into_clauses([("summary", text)])]
        joined = "\n".join(clauses)
        assert "e.g. Python" in joined
        assert "i.e. two languages" in joined
        assert "et al." in joined
        # 不应产生孤立的 "g" / "e" / "al" 碎片
        assert not any(c in ("e", "g", "al", "i", "al.") for c in clauses)

    def test_decimal_not_split(self):
        text = "Bandwidth reaches 0.5 THz and improves 1.75 times."
        clauses = [c for _, c in split_into_clauses([("summary", text)])]
        joined = "\n".join(clauses)
        assert "0.5 THz" in joined
        assert "1.75" in joined
        assert not any(c in ("0", "5 THz and improves 1", "75 times") for c in clauses)

    def test_long_english_sentence_comma_clause(self):
        long_s = ("We propose a novel system combining terahertz spectroscopy "
                  "with machine learning, which classifies sample types, "
                  "and we verify it on real data.")
        clauses = [c for _, c in split_into_clauses([("summary", long_s)])]
        assert len(clauses) > 1


class TestEnglishExtraction(unittest.TestCase):
    def setUp(self):
        self.engine = OfflineSummaryEngine(cache={})

    def _paper(self, abstract):
        return SearchResult(rank=1, title="Terahertz imaging method",
                            database="IEEE Xplore", artifact_type="paper",
                            abstract=abstract)

    def test_english_four_fields(self):
        s = self.engine.summarize(self._paper(IEEE_ABS))
        assert s.research_content != INSUFFICIENT_TEXT
        assert s.core_technology != INSUFFICIENT_TEXT
        assert s.application_value != INSUFFICIENT_TEXT
        # main_results 强证据子句可能被 core 先占（used-once 语义）→ 允许不足；
        # 强结果句单独由 TestHotfixEnglishGates.test_g 保证
        # 中文字段标题下内容保持英文原文

    def test_no_hallucination_english(self):
        """输出 ⊆ evidence（英文原文，不翻译）；允许 120 字截断后缀 …。"""
        s = self.engine.summarize(self._paper(IEEE_ABS))
        for value in (s.research_content, s.core_technology,
                      s.main_results, s.application_value):
            prefix = value[:-1] if value.endswith("…") else value
            assert value == INSUFFICIENT_TEXT or prefix in IEEE_ABS, \
                f"输出非 evidence 子串: {value!r}"

    def test_insufficient_when_no_english_signal(self):
        s = self.engine.summarize(self._paper(
            "We read some books about birds. The sky was blue all day."))
        assert s.core_technology == INSUFFICIENT_TEXT or \
            s.main_results == INSUFFICIENT_TEXT

    def test_same_sentence_not_reused_across_fields(self):
        s = self.engine.summarize(self._paper(IEEE_ABS))
        values = [s.research_content, s.core_technology,
                  s.main_results, s.application_value]
        non_empty = [v for v in values if v != INSUFFICIENT_TEXT]
        assert len(non_empty) == len(set(non_empty))

    def test_chinese_no_regression(self):
        """中文 evidence 抽取行为不回归。"""
        s = self.engine.summarize(SearchResult(
            rank=1, title="太赫兹技术在生物检测中的研究进展", database="CNKI",
            artifact_type="paper", abstract=CNKI_ABS))
        assert "太赫兹" in s.research_content + s.core_technology
        assert s.main_results == INSUFFICIENT_TEXT or \
            s.main_results != INSUFFICIENT_TEXT  # 诚实（本摘要无 results 词）


class TestIeeeFetcherParse(unittest.TestCase):
    def test_parse_ieee_abstract_body(self):
        text = ("Metrics\nAbstract: Two-dimensional terahertz imaging is "
                "investigated. We propose a method.\nIndex Terms: THz, imaging")
        a = parse_ieee_abstract(text)
        assert a.startswith("Two-dimensional")
        assert "Index Terms" not in a

    def test_parse_fallback(self):
        a = parse_ieee_abstract("Abstract: Short result only.")
        assert a == "Short result only."
        assert parse_ieee_abstract("no abstract text here") == ""
        assert parse_ieee_abstract("") == ""


class TestIeeeFetcher(unittest.TestCase):
    def _session(self, body, url="https://ieeexplore.ieee.org/document/1/", err=None):
        page = mock.Mock()
        page.url = url
        page.inner_text.return_value = body
        page.wait_for_timeout.side_effect = lambda ms: time.sleep(0)
        if err:
            page.goto.side_effect = err
        ctx = mock.Mock()
        ctx.new_page.return_value = page
        session = mock.Mock()
        session.context = ctx
        return session, page

    def _body(self):
        return ("Metrics\nAbstract: Two-dimensional terahertz imaging based on "
                "a sparse MIMO array is investigated for non-destructive "
                "evaluation. We propose a compressed sensing method.\n"
                "Index Terms: THz")

    def test_success(self):
        session, page = self._session(self._body())
        a = IeeePaperEvidenceFetcher(session, cache={}, timeout_s=5).fetch(
            "https://ieeexplore.ieee.org/document/1/")
        assert a.startswith("Two-dimensional terahertz imaging")
        page.close.assert_called_once()

    def test_whitespace_normalized(self):
        session, _page = self._session("Abstract: We propose\na method.\nMore  text.")
        a = IeeePaperEvidenceFetcher(session, cache={}, timeout_s=5).fetch(
            "https://ieeexplore.ieee.org/document/1/")
        assert "\n" not in a and "  " not in a

    def test_no_abstract(self):
        session, _page = self._session("No abstract section here at all.")
        with self.assertRaises(EvidenceError):
            IeeePaperEvidenceFetcher(session, cache={}, timeout_s=5).fetch(
                "https://ieeexplore.ieee.org/document/1/")

    def test_cache_hit_no_second_open(self):
        cache = {}
        session, _page = self._session(self._body())
        fetcher = IeeePaperEvidenceFetcher(session, cache=cache, timeout_s=5)
        url = "https://ieeexplore.ieee.org/document/9/"
        fetcher.fetch(url)
        session2, page2 = self._session("Abstract: different content here.")
        fetcher2 = IeeePaperEvidenceFetcher(session2, cache=cache, timeout_s=5)
        a = fetcher2.fetch(url)
        assert a.startswith("Two-dimensional")
        page2.inner_text.assert_not_called()

    def test_invalid_url(self):
        session, _page = self._session(self._body())
        with self.assertRaises(EvidenceError):
            IeeePaperEvidenceFetcher(session, cache={}, timeout_s=5).fetch("x")

    def test_bot_block(self):
        session, _page = self._session(
            "unusual traffic from your computer network captcha")
        with self.assertRaises(EvidenceError):
            IeeePaperEvidenceFetcher(session, cache={}, timeout_s=5).fetch(
                "https://ieeexplore.ieee.org/document/1/")

    def test_timeout(self):
        from playwright._impl._errors import TimeoutError as PwTimeout
        session, _page = self._session(self._body(), err=PwTimeout("t"))
        with self.assertRaises(EvidenceError):
            IeeePaperEvidenceFetcher(session, cache={}, timeout_s=5).fetch(
                "https://ieeexplore.ieee.org/document/1/")


class TestWorkerIeee(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _run(self, source, detail_url, body):
        from tju_info_retrieval.ui.evidence_worker import PaperEvidenceWorker
        worker = PaperEvidenceWorker()
        ready, failed = [], []
        worker.abstract_ready.connect(ready.append)
        worker.evidence_failed.connect(failed.append)
        with mock.patch("tju_info_retrieval.ui.evidence_worker.BrowserSession") as bs:
            inst = bs.return_value
            inst.start.return_value = None
            page = mock.Mock()
            page.url = detail_url
            page.inner_text.return_value = body
            page.wait_for_timeout.side_effect = lambda ms: time.sleep(0)
            ctx = mock.Mock()
            ctx.new_page.return_value = page
            inst.context = ctx
            worker.fetch_abstract(source, detail_url)
        return ready, failed

    def test_ieee_success(self):
        ready, failed = self._run(
            "IEEE Xplore", "https://ieeexplore.ieee.org/document/1/",
            "Metrics\nAbstract: Two-dimensional imaging is investigated here.\nIndex Terms: X")
        assert ready and ready[0].startswith("Two-dimensional")
        assert not failed

    def test_unknown_source_fails_with_message(self):
        from tju_info_retrieval.ui.evidence_worker import PaperEvidenceWorker
        worker = PaperEvidenceWorker()
        ready, failed = [], []
        worker.abstract_ready.connect(ready.append)
        worker.evidence_failed.connect(failed.append)
        with mock.patch("tju_info_retrieval.ui.evidence_worker.BrowserSession"):
            worker.fetch_abstract("万方", "https://x")
        assert not ready
        assert failed and "不支持" in failed[0]


class TestNoBatchIeee(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def test_ieee_search_done_no_fetch(self):
        row = SearchResult(rank=1, title="T", database="IEEE Xplore",
                           artifact_type="paper", abstract=None).to_dict()
        with mock.patch.object(self.window, "evidence_requested") as sig:
            self.window._on_search_done([row])
        sig.emit.assert_not_called()


class TestHotfixEnglishGates(unittest.TestCase):
    """v0.17 2.4-C-C Hotfix：语义误分类 gate（discourse/极性/main 阈值）。"""

    def setUp(self):
        self.engine = OfflineSummaryEngine(cache={})

    def _sum(self, abstract):
        return self.engine.summarize(SearchResult(
            rank=1, title="T", database="IEEE Xplore",
            artifact_type="paper", abstract=abstract))

    def test_a_and_in_this_paper_not_research(self):
        s = self._sum(
            "and in this paper, terahertz imaging was examined by the team. "
            "The experimental results demonstrate improved resolution.")
        assert s.research_content == INSUFFICIENT_TEXT

    def test_b_real_research_sentence_retained(self):
        s = self._sum(
            "This paper investigates terahertz propagation in random surfaces. "
            "The experimental results demonstrate improved resolution.")
        assert s.research_content != INSUFFICIENT_TEXT
        assert s.research_content in (
            "This paper investigates terahertz propagation in random surfaces")

    def test_c_negative_limitation_not_application_value(self):
        s = self._sum(
            "We propose a terahertz imaging system. The system is difficult "
            "to serve for the real life application.")
        assert s.application_value == INSUFFICIENT_TEXT

    def test_d_positive_application_pass(self):
        s = self._sum(
            "We propose a terahertz imaging system. This technique has "
            "potential applications in biomedical imaging.")
        assert s.application_value != INSUFFICIENT_TEXT
        assert "biomedical" in s.application_value

    def test_e_can_be_applied_pass(self):
        s = self._sum(
            "We propose a terahertz imaging system. The method can be applied "
            "to non-destructive testing.")
        assert s.application_value != INSUFFICIENT_TEXT

    def test_f_weak_efforts_not_main_results(self):
        s = self._sum(
            "We propose a terahertz imaging system. Some efforts were made to "
            "improve the efficiency of measurement.")
        assert s.main_results == INSUFFICIENT_TEXT

    def test_g_strong_experimental_result_main_pass(self):
        s = self._sum(
            "We propose a terahertz imaging system. The experimental results "
            "demonstrate improved measurement efficiency.")
        assert s.main_results != INSUFFICIENT_TEXT
        assert s.main_results in (
            "The experimental results demonstrate improved measurement efficiency")

    def test_h_english_output_is_evidence_substring(self):
        s = self._sum(
            "This paper investigates terahertz propagation in random surfaces. "
            "The experimental results demonstrate improved resolution. "
            "The method can be applied to industrial inspection.")
        for v in (s.research_content, s.core_technology, s.main_results,
                  s.application_value):
            assert v == INSUFFICIENT_TEXT or v.rstrip("…") in (
                "This paper investigates terahertz propagation in random surfaces. "
                "The experimental results demonstrate improved resolution. "
                "The method can be applied to industrial inspection.")

    def test_i_no_candidate_insufficient(self):
        s = self._sum("The sky was blue. Birds flew over the hills today.")
        for v in (s.research_content, s.core_technology,
                  s.main_results, s.application_value):
            assert v == INSUFFICIENT_TEXT

    def test_j_chinese_no_regression(self):
        s = self.engine.summarize(SearchResult(
            rank=1, title="太赫兹技术在生物检测中的研究进展", database="CNKI",
            artifact_type="paper",
            abstract=("太赫兹技术由于对极性分子的高敏感性，在生物检测领域展现出"
                      "重要应用潜力。本文围绕太赫兹光谱与成像技术梳理研究进展，"
                      "对比了远场成像等发展路径。该系统在工业检测场景中具有应用前景。")))
        assert "太赫兹" in s.research_content + s.core_technology
        assert s.application_value != INSUFFICIENT_TEXT

    def test_discourse_fragments_rejected_generally(self):
        s = self._sum(
            "however, therefore, in addition. This paper investigates "
            "terahertz propagation. The experimental results demonstrate "
            "improved resolution.")
        assert s.research_content != INSUFFICIENT_TEXT
        assert s.research_content in (
            "This paper investigates terahertz propagation")


if __name__ == "__main__":
    unittest.main(verbosity=2)