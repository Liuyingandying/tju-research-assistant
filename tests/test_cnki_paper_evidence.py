#!/usr/bin/env python3
"""v0.17 Phase 2.4-C-B：CNKI 论文 on-demand 摘要获取测试。

覆盖：fetcher（摘要解析/空白归一/无摘要/超时/真阻断/休眠验证码成功/cache/
无效 URL）、worker 错误映射、Summary UI 集成（CNKI paper 无摘要→fetch→
整理；已有摘要不 fetch；非 CNKI paper 保持不足；fetch 失败不整理；
single-flight；搜索完成 fetcher 调用计数=0）。
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

from PySide6.QtWidgets import QApplication, QMessageBox

from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.sources.cnki_paper_evidence import (
    AUTH,
    CAPTCHA,
    NETWORK,
    NO_ABSTRACT,
    NO_URL,
    TIMEOUT,
    CnkiPaperEvidenceFetcher,
    EvidenceError,
    parse_abstract,
)

DETAIL_OK = ("申请(专利)号： 无\n摘要：\t太赫兹技术由于对极性分子振动的高敏感性，"
             "在生物检测领域展现出重要应用潜力。本文围绕太赫兹光谱与成像技术"
             "梳理研究进展。安全验证 拖动滑块")
DETAIL_NO_ABS = "只有正文没有摘要的页面内容。安全验证 拖动滑块"


def _fake_session(body=DETAIL_OK, url="https://kns.cnki.net/kcms2/article/abstract?v=1",
                  goto_err=None):
    page = mock.Mock()
    page.url = url
    page.inner_text.return_value = body
    page.wait_for_timeout.side_effect = lambda ms: time.sleep(0)
    if goto_err:
        page.goto.side_effect = goto_err
    ctx = mock.Mock()
    ctx.new_page.return_value = page
    session = mock.Mock()
    session.context = ctx
    return session, page


class TestFetcherParse(unittest.TestCase):
    def test_parse_abstract_first_line(self):
        assert parse_abstract(DETAIL_OK).startswith("太赫兹技术由于")
        assert parse_abstract("无摘要") == ""
        assert parse_abstract("") == ""

    def test_whitespace_normalized(self):
        a = parse_abstract("摘要：\t太赫兹技术\n应用  前景。 更多内容")
        assert "\t" not in a and "  " not in a


class TestFetcher(unittest.TestCase):
    def test_success(self):
        session, page = _fake_session()
        abstract = CnkiPaperEvidenceFetcher(session, cache={}, timeout_s=3).fetch(
            "https://kns.cnki.net/kcms2/article/abstract?v=1")
        assert abstract.startswith("太赫兹技术由于")
        page.close.assert_called_once()

    def test_cache_hit_no_second_open(self):
        cache = {}
        session, page = _fake_session()
        fetcher = CnkiPaperEvidenceFetcher(session, cache=cache, timeout_s=3)
        url = "https://kns.cnki.net/kcms2/article/abstract?v=cache"
        fetcher.fetch(url)
        session2, page2 = _fake_session(body="摘要：\t另一种摘要内容。")
        fetcher2 = CnkiPaperEvidenceFetcher(session2, cache=cache, timeout_s=3)
        a = fetcher2.fetch(url)
        assert a.startswith("太赫兹技术由于")  # 命中缓存
        page2.inner_text.assert_not_called()

    def test_dormant_captcha_with_abstract_succeeds(self):
        """休眠验证码元素 + 摘要可读 → 成功（探针 06 json 语义）。"""
        session, _page = _fake_session(body=DETAIL_OK)
        abstract = CnkiPaperEvidenceFetcher(session, cache={}, timeout_s=3).fetch(
            "https://kns.cnki.net/kcms2/article/abstract?v=d")
        assert abstract.startswith("太赫兹技术由于")

    def test_true_captcha_block_raises(self):
        """摘要不可得 + 验证码标记 → CAPTCHA（真阻断）。"""
        session, _page = _fake_session(body=DETAIL_NO_ABS)
        with self.assertRaises(EvidenceError) as ctx:
            CnkiPaperEvidenceFetcher(session, cache={}, timeout_s=3).fetch(
                "https://kns.cnki.net/kcms2/article/abstract?v=b")
        assert ctx.exception.code == CAPTCHA

    def test_no_abstract_raises(self):
        session, _page = _fake_session(
            body="这是一段普通的纯页面文本，没有任何摘要字段信息。")
        with self.assertRaises(EvidenceError) as ctx:
            CnkiPaperEvidenceFetcher(session, cache={}, timeout_s=3).fetch(
                "https://kns.cnki.net/kcms2/article/abstract?v=n")
        assert ctx.exception.code == NO_ABSTRACT

    def test_timeout_raises(self):
        from playwright._impl._errors import TimeoutError as PwTimeout
        session, _page = _fake_session(
            goto_err=PwTimeout("goto timed out"))
        with self.assertRaises(EvidenceError) as ctx:
            CnkiPaperEvidenceFetcher(session, cache={}, timeout_s=3).fetch(
                "https://kns.cnki.net/kcms2/article/abstract?v=t")
        assert ctx.exception.code == TIMEOUT

    def test_invalid_url_raises(self):
        session, _page = _fake_session()
        with self.assertRaises(EvidenceError) as ctx:
            CnkiPaperEvidenceFetcher(session, cache={}, timeout_s=3).fetch("not-a-url")
        assert ctx.exception.code == NO_URL
        with self.assertRaises(EvidenceError):
            CnkiPaperEvidenceFetcher(session, cache={}, timeout_s=3).fetch("")

    def test_auth_redirect_raises(self):
        session, _page = _fake_session(
            body="请登录后访问", url="https://kns.cnki.net/login")
        with self.assertRaises(EvidenceError) as ctx:
            CnkiPaperEvidenceFetcher(session, cache={}, timeout_s=3).fetch(
                "https://kns.cnki.net/kcms2/article/abstract?v=a")
        assert ctx.exception.code == AUTH

    def test_network_error_raises(self):
        session, _page = _fake_session(goto_err=RuntimeError("conn reset"))
        with self.assertRaises(EvidenceError) as ctx:
            CnkiPaperEvidenceFetcher(session, cache={}, timeout_s=3).fetch(
                "https://kns.cnki.net/kcms2/article/abstract?v=nw")
        assert ctx.exception.code == NETWORK


class TestEvidenceWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _run_worker(self, detail_url):
        from tju_info_retrieval.ui.evidence_worker import PaperEvidenceWorker
        worker = PaperEvidenceWorker()
        ready, failed = [], []
        worker.abstract_ready.connect(ready.append)
        worker.evidence_failed.connect(failed.append)
        with mock.patch("tju_info_retrieval.ui.evidence_worker.BrowserSession") as bs:
            bs.return_value.start.return_value = None
            worker.fetch_abstract("CNKI", detail_url)
        return ready, failed

    def test_success_emits_abstract(self):
        session, _page = _fake_session()
        with mock.patch("tju_info_retrieval.ui.evidence_worker.BrowserSession") as bs:
            inst = bs.return_value
            inst.start.return_value = None
            # fetcher 在 worker 内用 inst.context；mock inst.context 提供页面
            inst.context = _fake_session()[0].context
            from tju_info_retrieval.ui.evidence_worker import PaperEvidenceWorker
            worker = PaperEvidenceWorker()
            ready, failed = [], []
            worker.abstract_ready.connect(ready.append)
            worker.evidence_failed.connect(failed.append)
            worker.fetch_abstract("CNKI",
                                    "https://kns.cnki.net/kcms2/article/abstract?v=w")
        assert ready and not failed

    def test_failure_emits_user_message(self):
        session, _page = _fake_session(body=DETAIL_NO_ABS)
        with mock.patch("tju_info_retrieval.ui.evidence_worker.BrowserSession") as bs:
            inst = bs.return_value
            inst.start.return_value = None
            inst.context = _fake_session(body=DETAIL_NO_ABS)[0].context
            from tju_info_retrieval.ui.evidence_worker import PaperEvidenceWorker
            worker = PaperEvidenceWorker()
            ready, failed = [], []
            worker.abstract_ready.connect(ready.append)
            worker.evidence_failed.connect(failed.append)
            worker.fetch_abstract("CNKI",
                                    "https://kns.cnki.net/kcms2/article/abstract?v=f")
        assert not ready
        assert failed and "人工验证" in failed[0]


def _cnki_paper(abstract=None, detail_url="https://kns.cnki.net/kcms2/article/abstract?v=p"):
    return SearchResult(rank=1, title="太赫兹技术在生物检测中的研究进展",
                        database="CNKI", detail_url=detail_url,
                        artifact_type="paper", abstract=abstract).to_dict()


def _patent_row():
    return SearchResult(rank=1, title="一种专利", database="万方专利",
                        artifact_type="patent", abstract="专利摘要。").to_dict()


class TestSummaryUiOnDemand(unittest.TestCase):
    """UI：选中 CNKI paper 无摘要 → 触发 evidence；已有摘要不触发。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        self.window = MainWindow()
        self.window._on_search_done([])

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def _select(self, row):
        self.window._results = [row]
        self.window.table.setRowCount(1)
        self.window.table.selectRow(0)

    def _patched_summarize(self):
        """模块级 mock：避免真实 SummaryDialog 模态 exec。"""
        from tju_info_retrieval.ui import main_window
        return mock.patch.object(main_window, "SummaryDialog")

    def test_search_done_never_fetches(self):
        """No batch：搜索完成后不触发任何 evidence 请求。"""
        sig = mock.Mock()
        with mock.patch.object(self.window, "evidence_requested") as patched:
            self.window._on_search_done([_cnki_paper()])
            patched.emit.assert_not_called()

    def test_cnki_paper_no_abstract_triggers_fetch(self):
        self._select(_cnki_paper(abstract=None))
        with mock.patch.object(self.window, "evidence_requested") as sig:
            self.window._on_summarize()
        sig.emit.assert_called_once()
        self.assertEqual(sig.emit.call_args.args[0], "CNKI")

    def test_ieee_paper_no_abstract_triggers_fetch(self):
        row = _cnki_paper(abstract=None)
        row["database"] = "IEEE Xplore"
        self._select(row)
        with mock.patch.object(self.window, "evidence_requested") as sig:
            self.window._on_summarize()
        sig.emit.assert_called_once()
        self.assertEqual(sig.emit.call_args.args[0], "IEEE Xplore")

    def test_cnki_paper_with_abstract_no_fetch(self):
        """已有摘要 → 直接整理，不调用 fetcher。"""
        self._select(_cnki_paper(abstract="太赫兹研究进展摘要内容，梳理了技术路径与应用。"))
        with mock.patch.object(self.window, "evidence_requested") as sig, \
                self._patched_summarize():
            self.window._on_summarize()
        sig.emit.assert_not_called()

    def test_non_fetchable_paper_no_abstract_keeps_insufficient(self):
        """万方 paper 无摘要 → 现有 信息不足 路径，不 fetch（本阶段不接万方）。"""
        row = _cnki_paper(abstract=None)
        row["database"] = "万方"
        self._select(row)
        with mock.patch.object(self.window, "evidence_requested") as sig,                 self._patched_summarize():
            self.window._on_summarize()
        sig.emit.assert_not_called()

    def test_non_paper_keeps_existing(self):
        self._select(_patent_row())
        with mock.patch.object(self.window, "evidence_requested") as sig,                 self._patched_summarize():
            self.window._on_summarize()
        sig.emit.assert_not_called()

    def test_single_flight_ignores_second_click(self):
        self._select(_cnki_paper(abstract=None))
        self.window._evidence_busy = True  # fetch 进行中
        with mock.patch.object(self.window, "evidence_requested") as sig:
            self.window._on_summarize()
        sig.emit.assert_not_called()

    def test_evidence_ready_writes_abstract_and_summarizes(self):
        """fetch 成功 → 写回行 abstract → 离线整理（integration 语义）。"""
        row = _cnki_paper(abstract=None)
        self._select(row)
        self.window._pending_summary_row = row
        with self._patched_summarize():
            self.window._on_evidence_ready(
                "太赫兹技术由于对极性分子的高敏感性在生物检测领域有重要应用，"
                "本文围绕光谱与成像技术梳理进展。")
        assert row["abstract"].startswith("太赫兹技术")
        self.window._on_evidence_finished()
        assert not self.window._evidence_busy

    def test_evidence_failure_no_summary(self):
        self.window._pending_summary_row = _cnki_paper(abstract=None)
        with mock.patch.object(QMessageBox, "information") as info:
            self.window._on_evidence_failed("当前 CNKI 页面需要人工验证，暂时无法获取摘要。")
        info.assert_called_once()
        self.window._on_evidence_finished()
        assert not self.window._evidence_busy


class TestSummaryIntegration(unittest.TestCase):
    def test_cnki_paper_abstract_summarizes(self):
        from tju_info_retrieval.services.offline_summary import OfflineSummaryEngine
        r = SearchResult.from_dict(_cnki_paper(abstract=(
            "太赫兹技术由于对极性分子振动及弱相互作用的高敏感性，在生物检测领域展现出重要应用潜力。"
            "本文围绕太赫兹光谱与成像两类核心技术，系统梳理了其在生物检测中的研究进展。")))
        s = OfflineSummaryEngine(cache={}).summarize(r)
        # 证据充分字段有真实文本；全部字段要么源自 abstract 要么诚实不足
        assert s.research_content != ""
        combined = s.research_content + s.core_technology + s.main_results \
            + s.application_value
        from tju_info_retrieval.services.offline_summary import INSUFFICIENT_TEXT
        for val in (s.research_content, s.core_technology,
                    s.main_results, s.application_value):
            assert val == INSUFFICIENT_TEXT or "太赫兹" in val or "光谱" in val \
                or "成像" in val or "应用" in val or "技术" in val


if __name__ == "__main__":
    unittest.main(verbosity=2)