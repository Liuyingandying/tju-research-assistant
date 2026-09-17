#!/usr/bin/env python3
"""正式测试：调研报告 UI 工作流（v0.18 Phase 2.9-C-E）。

覆盖任务书 10 项：

1. 按钮存在（论文库“生成调研报告”）
2. 选择数量显示
3. 0 篇禁止生成
4. 1-4 篇二次确认
5. >20 拒绝
6. worker 线程启动
7. 成功打开报告窗口
8. 失败降级显示
9. 关闭窗口线程清理
10. UI 不直接调用 LLM Provider

隔离：全部在 pytest 隔离目录内运行；不触碰 data/library.json /
runtime/research_profile.json / 真实缓存。
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from unittest import mock

import pytest

os_import = __import__("os")
os_import.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from tests._isolation_support import project_root
from tju_info_retrieval.models.library import LibraryRecord
from tju_info_retrieval.services.library_store import LibraryStore
from tju_info_retrieval.services.report_cache import ReportCache
from tju_info_retrieval.services.report_provider import MockReportProvider
from tju_info_retrieval.services.report_service import (
    DEGRADED_NOT_CONFIGURED,
    RESULT_CACHED,
    RESULT_DRAFT,
    RESULT_GENERATED,
    ReportService,
)
from tju_info_retrieval.services.research_report_store import ResearchReportStore
from tju_info_retrieval.ui.library_window import (
    COL_CHECK,
    REPORT_MAX_PAPERS,
    REPORT_MIN_PAPERS_SOFT,
    LibraryWindow,
)
from tju_info_retrieval.ui.report_worker import ReportWorker
from tju_info_retrieval.ui.research_report_dialog import ResearchReportDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def library(tmp_path) -> LibraryStore:
    return LibraryStore(tmp_path / "cache" / "library.json")


def _seed(store: LibraryStore, count: int) -> list[str]:
    ids = []
    for i in range(count):
        record = LibraryRecord.from_search_result({
            "title": f"测试论文{i}", "authors": [f"作者{i}"], "year": "2026",
            "source": "中国知网", "database": "CNKI",
            "abstract": f"第{i}篇摘要：太赫兹波传播特性。",
        })
        record.id = f"lib-{i}"
        if i < 2:
            record.set_enhanced_summary(
                {"document_type": "review",
                 "direction_match_level": "medium",
                 "reading_priority": "priority_read",
                 "research_background": "背景"},
                provider="openai-compatible", model="tju-llm",
                prompt_version="v4", profile_version="p2")
        store.save(record)
        ids.append(record.id)
    return ids


def _window(store: LibraryStore) -> LibraryWindow:
    window = LibraryWindow(store)
    window.set_profile_version("p2")
    window.set_ai_configured(True)
    return window


_UNSET = object()   # 区分"未指定（→Mock）"与"显式无 Provider（→降级）"


def _service(tmp_path, store, provider=_UNSET) -> ReportService:
    if provider is _UNSET:
        provider = MockReportProvider()
    return ReportService(
        provider=provider,
        store=ResearchReportStore(tmp_path / "reports"),
        cache=ReportCache(tmp_path / "cache" / "research_reports"),
        library_store=store)


def _pump(app, predicate, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()
    return predicate()


def _check(window: LibraryWindow, row: int) -> None:
    window.table.item(row, COL_CHECK).setCheckState(Qt.CheckState.Checked)


# ============================================================
# 1-2 按钮与选择计数
# ============================================================

class TestSelectionUi:
    def test_1_generate_button_exists(self, app, library):
        window = _window(library)
        assert hasattr(window, "btn_generate_report")
        assert window.btn_generate_report.text() == "生成调研报告"
        # 与主窗口"生成报告"（检索结果报告）文案区分
        assert window.btn_generate_report.text() != "生成报告"
        window.close()

    def test_1b_checkbox_column_exists(self, app, library):
        window = _window(library)
        assert window.table.columnCount() == 9
        assert window.table.horizontalHeaderItem(COL_CHECK).text() == "选择"
        window.close()

    def test_2_selection_count_displayed(self, app, library):
        _seed(library, 6)
        window = _window(library)
        assert window.label_selected.text() == "已选 0 篇"
        _check(window, 0)
        assert window.label_selected.text() == "已选 1 篇"
        _check(window, 1)
        _check(window, 2)
        assert window.label_selected.text() == "已选 3 篇"
        assert len(window.checked_paper_ids()) == 3
        window.close()

    def test_2b_uncheck_updates_count(self, app, library):
        _seed(library, 4)
        window = _window(library)
        _check(window, 0)
        _check(window, 1)
        window.table.item(0, COL_CHECK).setCheckState(Qt.CheckState.Unchecked)
        assert window.label_selected.text() == "已选 1 篇"
        window.close()

    def test_2c_select_all_capped_and_clear(self, app, library):
        _seed(library, REPORT_MAX_PAPERS + 3)
        window = _window(library)
        window._on_select_all()
        assert len(window.checked_paper_ids()) == REPORT_MAX_PAPERS
        assert f"已选 {REPORT_MAX_PAPERS} 篇" == window.label_selected.text()
        window._on_clear_selection()
        assert window.checked_paper_ids() == []
        assert window.label_selected.text() == "已选 0 篇"
        window.close()


# ============================================================
# 3-5 数量规则
# ============================================================

class TestQuantityRules:
    def test_3_zero_selection_blocks_generate(self, app, library):
        _seed(library, 5)
        window = _window(library)
        assert window.btn_generate_report.isEnabled() is False
        emitted: list = []
        window.generate_report_requested.connect(emitted.append)
        with mock.patch.object(QMessageBox, "information") as info:
            window._on_generate_report()
        assert emitted == []
        info.assert_called_once()
        assert "勾选" in info.call_args.args[2]
        window.close()

    def test_4_few_papers_requires_confirmation(self, app, library):
        ids = _seed(library, 3)
        window = _window(library)
        assert REPORT_MIN_PAPERS_SOFT == 5
        for row in range(3):
            _check(window, row)
        emitted: list = []
        window.generate_report_requested.connect(emitted.append)

        # 取消 → 不发出请求
        with mock.patch.object(QMessageBox, "question",
                               return_value=QMessageBox.StandardButton.No) as q:
            window._on_generate_report()
        q.assert_called_once()
        assert "可能不完整" in q.call_args.args[2]
        assert emitted == []

        # 确认 → 发出请求
        with mock.patch.object(QMessageBox, "question",
                               return_value=QMessageBox.StandardButton.Yes):
            window._on_generate_report()
        assert len(emitted) == 1
        assert sorted(emitted[0]) == sorted(ids)
        window.close()

    def test_4b_five_papers_no_confirmation(self, app, library):
        _seed(library, 5)
        window = _window(library)
        for row in range(5):
            _check(window, row)
        emitted: list = []
        window.generate_report_requested.connect(emitted.append)
        with mock.patch.object(QMessageBox, "question") as q:
            window._on_generate_report()
        q.assert_not_called()                       # 5 篇直接生成
        assert len(emitted) == 1
        window.close()

    def test_5_over_twenty_rejected(self, app, library):
        _seed(library, REPORT_MAX_PAPERS + 4)
        window = _window(library)
        window._on_select_all()
        assert len(window.checked_paper_ids()) == REPORT_MAX_PAPERS

        # 再勾第 21 篇 → 拒绝并回退
        target_row = None
        for row in range(window.table.rowCount()):
            item = window.table.item(row, COL_CHECK)
            if item is not None and item.checkState() != Qt.CheckState.Checked:
                target_row = row
                break
        assert target_row is not None
        with mock.patch.object(QMessageBox, "warning") as warning:
            _check(window, target_row)
        warning.assert_called_once()
        assert f"{REPORT_MAX_PAPERS}" in warning.call_args.args[2]
        assert len(window.checked_paper_ids()) == REPORT_MAX_PAPERS
        assert (window.table.item(target_row, COL_CHECK).checkState()
                != Qt.CheckState.Checked)
        window.close()


# ============================================================
# 6 worker 线程
# ============================================================

class TestReportWorker:
    def test_6_worker_runs_in_thread_and_emits(self, app, tmp_path, library):
        ids = _seed(library, 5)
        from tju_info_retrieval.ui.main_window import MainWindow

        window = MainWindow()
        window._library_store = library
        window._report_service = _service(tmp_path, library)
        captured: dict = {}
        window._report_worker.succeeded.connect(
            lambda d: captured.update(d))

        # 报告查看窗口是模态 exec()：测试中必须替身，否则会阻塞在嵌套事件循环
        with mock.patch("tju_info_retrieval.ui.main_window."
                        "ResearchReportDialog") as Dlg:
            Dlg.return_value.exec.return_value = None
            window._generate_library_report(ids)
            assert window._report_busy is True          # 提交后处于忙碌
            assert _pump(app, lambda: not window._report_busy),                 "worker 未在超时内完成"
            Dlg.assert_called_once()
        assert captured.get("status") == RESULT_GENERATED
        # ResearchReport.to_dict() 以 papers 列表承载篇数（paper_count 是属性）
        assert len(captured["report"]["papers"]) == 5
        window.close()
        window.deleteLater()
        app.processEvents()

    def test_6b_worker_signals_direct(self, app, tmp_path, library):
        """ReportWorker 可直接调用（同线程），信号齐备。"""
        ids = _seed(library, 5)
        worker = ReportWorker(_service(tmp_path, library))
        progress: list = []
        done: list = []
        worker.progress.connect(lambda c, t, s: progress.append((c, t, s)))
        worker.finished.connect(lambda: done.append(True))
        worker.run_report(ids, "")
        assert done == [True]
        assert progress and progress[-1][0] == len(ids)
        assert worker._service is not None

    def test_6c_worker_without_service_reports_failure(self, app):
        worker = ReportWorker()
        failed: list = []
        finished: list = []
        worker.failed.connect(failed.append)
        worker.finished.connect(lambda: finished.append(True))
        worker.run_report(["a"], "")
        assert failed and "失败" in failed[0]
        assert finished == [True]

    def test_6d_worker_never_raises(self, app, library):
        class Boom:
            def generate_report(self, ids, title=None):
                raise RuntimeError("boom")

        worker = ReportWorker(Boom())
        failed: list = []
        worker.failed.connect(failed.append)
        worker.run_report(["a"], "")
        assert failed


# ============================================================
# 7-8 报告窗口与降级
# ============================================================

class TestReportDialog:
    def test_7_success_opens_report_dialog(self, app, tmp_path, library):
        ids = _seed(library, 5)
        from tju_info_retrieval.ui.main_window import MainWindow

        window = MainWindow()
        window._library_store = library
        window._report_service = _service(tmp_path, library)
        with mock.patch(
                "tju_info_retrieval.ui.main_window.ResearchReportDialog") as Dlg:
            Dlg.return_value.exec.return_value = None
            window._generate_library_report(ids)
            assert _pump(app, lambda: not window._report_busy)
            Dlg.assert_called_once()
            report = Dlg.call_args.args[0]
            assert report.paper_count == 5
            assert len(report.sections) == 6
            result = Dlg.call_args.kwargs.get("result")
            assert result is not None and result.status == RESULT_GENERATED
        window.close()
        window.deleteLater()
        app.processEvents()

    def test_7b_dialog_renders_sections_and_sources(self, app, tmp_path, library):
        ids = _seed(library, 5)
        service = _service(tmp_path, library)
        result = service.generate_report(ids)
        dialog = ResearchReportDialog(result.report, result=result)
        assert dialog.report.paper_count == 5
        assert dialog.list_sections.count() == 6
        # 每节来源标注（[P#]）
        assert "[P" in dialog.section_sources_text(0)
        assert "[P" in dialog.section_sources_text(5)
        # 元信息：论文数量 / Provider / Prompt 版本
        meta = dialog.label_meta.text()
        assert "论文数量：5 篇" in meta
        assert "Prompt 版本：r1" in meta
        assert "Provider：" in meta
        # 来源论文附录
        assert "来源论文附录" in dialog.browser_sources.toHtml()
        dialog.close()

    def test_7c_dialog_accepts_dict_payload(self, app, tmp_path, library):
        ids = _seed(library, 5)
        result = _service(tmp_path, library).generate_report(ids)
        dialog = ResearchReportDialog(result.to_dict())
        assert dialog.report.paper_count == 5
        dialog.close()

    def test_8_degraded_report_displayed(self, app, tmp_path, library):
        ids = _seed(library, 5)
        from tju_info_retrieval.ui.main_window import MainWindow

        window = MainWindow()
        window._library_store = library
        window._report_service = _service(tmp_path, library, provider=None)
        with mock.patch(
                "tju_info_retrieval.ui.main_window.ResearchReportDialog") as Dlg:
            Dlg.return_value.exec.return_value = None
            window._generate_library_report(ids)
            assert _pump(app, lambda: not window._report_busy)
            result = Dlg.call_args.kwargs.get("result")
            assert result is not None
            assert result.status == RESULT_DRAFT
            assert result.degraded_reason == DEGRADED_NOT_CONFIGURED
            # 降级文案可读（不出现 traceback 字样）
            assert "基础报告" in result.message
            assert "Traceback" not in result.message
            report = Dlg.call_args.args[0]
            keys = [s.key for s in report.sections]
            assert "collection_overview" in keys
            assert "existing_directions" in keys
        window.close()
        window.deleteLater()
        app.processEvents()

    def test_8b_dialog_shows_degraded_notice(self, app, tmp_path, library):
        ids = _seed(library, 5)
        result = _service(tmp_path, library, provider=None).generate_report(ids)
        dialog = ResearchReportDialog(result.report, result=result)
        assert dialog.label_notice.isVisible() or dialog.label_notice.text()
        assert "未调用 AI" in dialog.label_notice.text()
        assert "Traceback" not in dialog.label_notice.text()
        dialog.close()

    def test_8c_failure_path_shows_message_not_traceback(self, app, tmp_path, library):
        from tju_info_retrieval.ui.main_window import MainWindow

        window = MainWindow()
        with mock.patch.object(QMessageBox, "information") as info:
            window._on_report_failed("调研报告生成失败")
        info.assert_called_once()
        assert "Traceback" not in info.call_args.args[2]
        assert window._report_busy is False
        window.close()
        window.deleteLater()
        app.processEvents()

    def test_8d_cache_hit_notice(self, app, tmp_path, library):
        ids = _seed(library, 5)
        service = _service(tmp_path, library)
        service.generate_report(ids)
        second = service.generate_report(ids)
        assert second.status == RESULT_CACHED
        dialog = ResearchReportDialog(second.report, result=second)
        assert "缓存" in dialog.label_notice.text()
        dialog.close()

    def test_dialog_stale_hint(self, app, tmp_path, library):
        ids = _seed(library, 5)
        result = _service(tmp_path, library).generate_report(ids)
        dialog = ResearchReportDialog(
            result.report, result=result,
            current_profile_version="p9", current_prompt_version="r2")
        assert "旧科研画像" in dialog.label_notice.text() or \
            "旧 Prompt" in dialog.label_notice.text()
        dialog.close()


# ============================================================
# 9-10 线程清理与不直接调用 LLM
# ============================================================

class TestLifecycleAndIsolation:
    def test_9_close_window_cleans_report_thread(self, app, tmp_path, library):
        from tju_info_retrieval.ui.main_window import MainWindow

        window = MainWindow()
        window._library_store = library
        thread = window._report_thread
        assert thread.isRunning()
        window.close()
        window.deleteLater()
        app.processEvents()
        assert not thread.isRunning()               # 关闭后线程已退出
        assert window._report_progress is None

    def test_9b_progress_dialog_closed_after_finish(self, app, tmp_path, library):
        ids = _seed(library, 5)
        from tju_info_retrieval.ui.main_window import MainWindow

        window = MainWindow()
        window._library_store = library
        window._report_service = _service(tmp_path, library)
        with mock.patch(
                "tju_info_retrieval.ui.main_window.ResearchReportDialog") as Dlg:
            Dlg.return_value.exec.return_value = None
            window._generate_library_report(ids)
            assert window._report_progress is not None   # 生成中显示进度
            assert _pump(app, lambda: not window._report_busy)
            assert window._report_progress is None       # 完成后关闭
        window.close()
        window.deleteLater()
        app.processEvents()

    def test_9c_busy_flag_prevents_concurrent_runs(self, app, tmp_path, library):
        ids = _seed(library, 5)
        from tju_info_retrieval.ui.main_window import MainWindow

        window = MainWindow()
        window._library_store = library
        window._report_service = _service(tmp_path, library)
        window._report_busy = True                  # 模拟进行中
        with mock.patch.object(window, "report_requested") as signal:
            window._generate_library_report(ids)
            signal.emit.assert_not_called()
        window._report_busy = False
        window.close()
        window.deleteLater()
        app.processEvents()

    def test_10_ui_does_not_call_llm_provider(self, app, tmp_path, library):
        """注入 Mock 服务的完整流程中，LLM Provider.complete 从未被调用。"""
        ids = _seed(library, 5)
        from tju_info_retrieval.ui.main_window import MainWindow
        from tju_info_retrieval.services.llm_provider import (
            OpenAICompatibleProvider,
        )

        window = MainWindow()
        window._library_store = library
        window._report_service = _service(tmp_path, library)

        def _boom(*args, **kwargs):
            raise AssertionError("UI 流程不得直接调用 LLM Provider")

        with mock.patch.object(OpenAICompatibleProvider, "complete", _boom), \
                mock.patch("tju_info_retrieval.ui.main_window."
                           "ResearchReportDialog") as Dlg:
            Dlg.return_value.exec.return_value = None
            window._generate_library_report(ids)
            assert _pump(app, lambda: not window._report_busy)
            assert Dlg.called                        # 正常出报告
        window.close()
        window.deleteLater()
        app.processEvents()

    def test_10b_ui_modules_have_no_direct_network(self):
        """UI 模块不 import requests / 不直接使用 LLM Report Provider 调用协议。"""
        modules = [
            project_root() / "src/tju_info_retrieval/ui/report_worker.py",
            project_root() / "src/tju_info_retrieval/ui/research_report_dialog.py",
            project_root() / "src/tju_info_retrieval/ui/library_window.py",
        ]
        for module in modules:
            tree = ast.parse(module.read_text(encoding="utf-8"))
            names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names.append(node.module or "")
                    names += [a.name for a in node.names]
            assert not any("requests" in n.lower() for n in names), module.name

    def test_10c_worker_only_calls_service(self, app, tmp_path, library):
        """ReportWorker 只调用 ReportService.generate_report。"""
        ids = _seed(library, 5)
        calls: list = []

        class SpyService:
            def generate_report(self, paper_ids, title=None):
                calls.append((list(paper_ids), title))
                return _service(tmp_path, library).generate_report(paper_ids)

        worker = ReportWorker(SpyService())
        worker.run_report(ids, "")
        assert calls and calls[0][0] == ids

    def test_11_library_json_untouched_by_ui_flow(self, app, tmp_path, library):
        """UI 流程不写入论文库文件（字节不变）。"""
        ids = _seed(library, 5)
        path = library.path
        before = path.read_bytes()
        from tju_info_retrieval.ui.main_window import MainWindow

        window = MainWindow()
        window._library_store = library
        window._report_service = _service(tmp_path, library)
        with mock.patch(
                "tju_info_retrieval.ui.main_window.ResearchReportDialog") as Dlg:
            Dlg.return_value.exec.return_value = None
            window._generate_library_report(ids)
            _pump(app, lambda: not window._report_busy)
        assert path.read_bytes() == before
        window.close()
        window.deleteLater()
        app.processEvents()

    def test_11b_real_library_untouched(self, app, tmp_path, library):
        real = project_root() / "data" / "library.json"
        if not real.is_file():
            pytest.skip("真实论文库不存在")
        before = real.read_bytes()
        window = _window(library)
        window._on_select_all()
        window._on_clear_selection()
        window.close()
        assert real.read_bytes() == before
