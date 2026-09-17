#!/usr/bin/env python3
"""正式测试：科研展示首页 / Dashboard（v0.18 Phase 2.10-B Phase B）。

覆盖：

1. Dashboard 演示模式：系统名 / 画像 / 统计（3-2-1）/ 最近活动
2. Dashboard 正常模式：从真实 store 读取（只读）；空库友好提示
3. 快捷入口：三个信号（搜索 / 论文库 / 报告）
4. DashboardDialog：信号转发 + 关闭
5. 数据隔离：Dashboard 只读，不写仓库 data/ 与 runtime/
6. MainWindow 集成契约：菜单入口方法存在（不实例化 MainWindow，
   避免加入 Qt 聚合崩溃面）
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests._isolation_support import project_root
from tju_info_retrieval.models.library import LibraryRecord
from tju_info_retrieval.models.research_report import (
    REPORT_SECTION_KEYS,
    STATUS_GENERATED,
    ReportSection,
    ReportSource,
    ResearchReport,
)
from tju_info_retrieval.services.demo_provider import DemoDataProvider
from tju_info_retrieval.services.library_store import LibraryStore
from tju_info_retrieval.services.research_profile_store import (
    ResearchProfileStore,
)
from tju_info_retrieval.services.research_report_store import (
    ResearchReportStore,
)
from tju_info_retrieval.version import PRODUCT_NAME, RELEASE_LABEL


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _qt_platform():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def isolated_stores(tmp_path):
    """隔离的论文库 / 报告库 / 画像 store（全部落在 tmp_path）。"""
    library = LibraryStore(tmp_path / "library.json")
    reports = ResearchReportStore(tmp_path / "reports")
    profile = ResearchProfileStore(tmp_path / "research_profile.json")
    return library, reports, profile


def _paper(title: str, saved_at: str, analyzed: bool = False,
           paper_id: str = "") -> LibraryRecord:
    record = LibraryRecord.from_search_result({
        "title": title,
        "authors": ["演示作者"],
        "year": "2026",
        "source": "期刊",
        "database": "CNKI",
        "detail_url": "https://example.invalid/x",
        "abstract": "摘要",
    })
    record.saved_at = saved_at
    if paper_id:
        record.id = paper_id
    if analyzed:
        record.enhanced_summary = {"research_background": "背景"}
    return record


def _report(title: str, created_time: str, paper_count: int = 3
            ) -> ResearchReport:
    sources = [
        ReportSource(paper_id=f"p{i}", title=f"论文{i}", index=i)
        for i in range(1, paper_count + 1)
    ]
    sections = [
        ReportSection(title=key, content=f"内容-{key}", order=i, key=key,
                      source_papers=[sources[0].paper_id])
        for i, key in enumerate(REPORT_SECTION_KEYS)
    ]
    return ResearchReport(
        title=title, created_time=created_time, papers=sources,
        sections=sections, status=STATUS_GENERATED)


def _make_widget(stores, demo_mode=False):
    from tju_info_retrieval.ui.dashboard_widget import DashboardWidget

    if stores is None:
        library = reports = profile = None
    else:
        library, reports, profile = stores
    return DashboardWidget(
        library_store=library, report_store=reports,
        profile_store=profile, demo_mode=demo_mode)


# ============================================================
# 1. 演示模式
# ============================================================

class TestDashboardDemoMode:
    def test_system_name(self, qt_app):
        widget = _make_widget(None, demo_mode=True)
        assert widget.label_system_name.text() == PRODUCT_NAME

    def test_demo_stats(self, qt_app):
        widget = _make_widget(None, demo_mode=True)
        assert widget.stat_values() == {
            "paper_count": 3, "analyzed_count": 2, "report_count": 1}
        assert widget.label_stat_papers.text() == "3"
        assert widget.label_stat_analyzed.text() == "2"
        assert widget.label_stat_reports.text() == "1"

    def test_demo_profile(self, qt_app):
        widget = _make_widget(None, demo_mode=True)
        assert "太赫兹技术" in widget.label_profile_area.text()
        assert "硕士生" in widget.label_profile_stage.text()

    def test_demo_recent_activity(self, qt_app):
        widget = _make_widget(None, demo_mode=True)
        assert len(widget.recent_paper_titles()) == 3
        assert len(widget.recent_report_titles()) == 1
        assert widget.demo_mode is True

    def test_demo_mode_uses_provider_snapshot(self, qt_app):
        widget = _make_widget(None, demo_mode=True)
        assert widget.snapshot()["stats"] == \
            DemoDataProvider.snapshot()["stats"]


# ============================================================
# 2. 正常模式（真实 store，只读）
# ============================================================

class TestDashboardLiveMode:
    def test_empty_stores_show_zeros_and_hints(self, qt_app, isolated_stores):
        widget = _make_widget(isolated_stores)
        assert widget.stat_values() == {
            "paper_count": 0, "analyzed_count": 0, "report_count": 0}
        assert "尚未设置" in widget.label_profile_area.text()
        # 空列表显示引导文案而非空白
        assert widget.list_recent_papers.count() == 1
        assert "暂无" in widget.list_recent_papers.item(0).text()

    def test_counts_from_real_stores(self, qt_app, isolated_stores):
        library, reports, _profile = isolated_stores
        library.save(_paper("论文A", "2026-09-01T00:00:00+00:00",
                            analyzed=True, paper_id="a"))
        library.save(_paper("论文B", "2026-09-02T00:00:00+00:00",
                            paper_id="b"))
        library.save(_paper("论文C", "2026-09-03T00:00:00+00:00",
                            analyzed=True, paper_id="c"))
        reports.save(_report("报告1", "2026-09-05T00:00:00+00:00"))
        reports.save(_report("报告2", "2026-09-06T00:00:00+00:00"))

        widget = _make_widget(isolated_stores)
        assert widget.stat_values() == {
            "paper_count": 3, "analyzed_count": 2, "report_count": 2}

    def test_recent_papers_sorted_desc(self, qt_app, isolated_stores):
        library, _reports, _profile = isolated_stores
        library.save(_paper("旧论文", "2026-01-01T00:00:00+00:00",
                            paper_id="old"))
        library.save(_paper("新论文", "2026-09-01T00:00:00+00:00",
                            paper_id="new"))
        widget = _make_widget(isolated_stores)
        titles = widget.recent_paper_titles()
        assert titles[0] == "新论文"
        assert "旧论文" in titles

    def test_recent_reports_newest_first(self, qt_app, isolated_stores):
        _library, reports, _profile = isolated_stores
        reports.save(_report("早报告", "2026-01-01T00:00:00+00:00"))
        reports.save(_report("晚报告", "2026-09-01T00:00:00+00:00"))
        widget = _make_widget(isolated_stores)
        assert widget.recent_report_titles()[0] == "晚报告"

    def test_profile_from_store(self, qt_app, isolated_stores):
        _library, _reports, profile = isolated_stores
        obj = profile.load()
        obj.research_area = "太赫兹成像"
        obj.research_stage = "phd"
        profile.save(obj)
        widget = _make_widget(isolated_stores)
        assert "太赫兹成像" in widget.label_profile_area.text()
        assert "博士生" in widget.label_profile_stage.text()


# ============================================================
# 3. 快捷入口信号
# ============================================================

class TestDashboardQuickEntry:
    def test_search_signal(self, qt_app):
        widget = _make_widget(None, demo_mode=True)
        hits = []
        widget.search_requested.connect(lambda: hits.append("s"))
        widget.btn_search.click()
        assert hits == ["s"]

    def test_library_signal(self, qt_app):
        widget = _make_widget(None, demo_mode=True)
        hits = []
        widget.library_requested.connect(lambda: hits.append("l"))
        widget.btn_library.click()
        assert hits == ["l"]

    def test_report_signal(self, qt_app):
        widget = _make_widget(None, demo_mode=True)
        hits = []
        widget.report_requested.connect(lambda: hits.append("r"))
        widget.btn_report.click()
        assert hits == ["r"]

    def test_three_quick_buttons_exist(self, qt_app):
        widget = _make_widget(None, demo_mode=True)
        assert widget.btn_search.text() == "搜索论文"
        assert widget.btn_library.text() == "打开论文库"
        assert widget.btn_report.text() == "生成报告"


# ============================================================
# 4. DashboardDialog
# ============================================================

class TestDashboardDialog:
    def test_dialog_demo_mode(self, qt_app):
        from tju_info_retrieval.ui.dashboard_widget import DashboardDialog

        dialog = DashboardDialog(demo_mode=True)
        assert dialog.demo_mode is True
        assert dialog.stat_values()["paper_count"] == 3
        dialog.close()

    def test_dialog_forwards_search_and_closes(self, qt_app):
        from tju_info_retrieval.ui.dashboard_widget import DashboardDialog

        dialog = DashboardDialog(demo_mode=True)
        hits = []
        dialog.search_requested.connect(lambda: hits.append("s"))
        dialog.widget.btn_search.click()
        assert hits == ["s"]
        assert not dialog.isVisible()      # 转发前已关闭

    def test_dialog_forwards_library_and_report(self, qt_app):
        from tju_info_retrieval.ui.dashboard_widget import DashboardDialog

        dialog = DashboardDialog(demo_mode=True)
        hits = []
        dialog.library_requested.connect(lambda: hits.append("l"))
        dialog.report_requested.connect(lambda: hits.append("r"))
        dialog.widget.btn_library.click()
        dialog.widget.btn_report.click()
        assert hits == ["l", "r"]

    def test_dialog_applies_theme(self, qt_app):
        from tju_info_retrieval.ui.dashboard_widget import DashboardDialog

        dialog = DashboardDialog(demo_mode=True)
        assert dialog.styleSheet()      # 非空 → 已应用主题
        dialog.close()


# ============================================================
# 5. 数据隔离
# ============================================================

class TestDashboardIsolation:
    def test_dashboard_reads_do_not_touch_repo_dirs(self, qt_app,
                                                    isolated_stores):
        library, reports, _profile = isolated_stores
        library.save(_paper("论文X", "2026-09-01T00:00:00+00:00",
                            analyzed=True, paper_id="x"))
        reports.save(_report("报告X", "2026-09-02T00:00:00+00:00"))

        root = project_root()
        data_dir = root / "data"
        runtime_dir = root / "runtime"

        def snap(d: Path) -> set[str]:
            return {p.name for p in d.iterdir()} if d.is_dir() else set()

        before_data, before_runtime = snap(data_dir), snap(runtime_dir)

        widget = _make_widget(isolated_stores)
        widget.refresh()
        widget.stat_values()
        widget.recent_paper_titles()

        assert snap(data_dir) == before_data
        assert snap(runtime_dir) == before_runtime

    def test_dashboard_does_not_modify_store_files(self, qt_app,
                                                  isolated_stores):
        library, reports, _profile = isolated_stores
        library.save(_paper("论文Y", "2026-09-01T00:00:00+00:00",
                            paper_id="y"))
        reports.save(_report("报告Y", "2026-09-02T00:00:00+00:00"))

        lib_bytes = library.path.read_bytes()
        report_files = {
            p.name: p.read_bytes() for p in reports.dir.iterdir()}

        _make_widget(isolated_stores).refresh()

        assert library.path.read_bytes() == lib_bytes
        assert {p.name: p.read_bytes() for p in reports.dir.iterdir()} == \
            report_files

    def test_demo_mode_no_repo_writes(self, qt_app):
        root = project_root()
        data_dir = root / "data"
        runtime_dir = root / "runtime"

        def snap(d: Path) -> set[str]:
            return {p.name for p in d.iterdir()} if d.is_dir() else set()

        before_data, before_runtime = snap(data_dir), snap(runtime_dir)
        _make_widget(None, demo_mode=True).refresh()
        assert snap(data_dir) == before_data
        assert snap(runtime_dir) == before_runtime


# ============================================================
# 6. MainWindow 集成契约（不实例化，避免 Qt 聚合崩溃面）
# ============================================================

class TestMainWindowIntegrationContract:
    def test_main_window_exposes_dashboard_entry(self):
        from tju_info_retrieval.ui.main_window import MainWindow

        assert hasattr(MainWindow, "_build_menu")
        assert hasattr(MainWindow, "_on_open_dashboard")
        assert hasattr(MainWindow, "_on_dashboard_search")
        assert hasattr(MainWindow, "_on_dashboard_report")

    def test_on_open_dashboard_uses_dashboard_dialog(self):
        import ast

        import tju_info_retrieval.ui.main_window as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        # 找到 _on_open_dashboard 函数体
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and \
                    node.name == "_on_open_dashboard":
                target = node
                break
        assert target is not None
        body = ast.dump(target)
        assert "DashboardDialog" in body
        assert "ResearchReportStore" in body

    def test_dashboard_dialog_accepts_mainwindow_store_types(self, qt_app):
        """MainWindow 传入的 store 类型可被 DashboardDialog 接受。"""
        from tju_info_retrieval.ui.dashboard_widget import DashboardDialog

        dialog = DashboardDialog(
            library_store=LibraryStore(),
            report_store=ResearchReportStore(),
            profile_store=ResearchProfileStore(),
            demo_mode=False,
        )
        # 正常模式可渲染（不抛异常）
        assert dialog.stat_values()["paper_count"] >= 0
        dialog.close()


# ============================================================
# 版本信息（Phase D）
# ============================================================

class TestVersionInfo:
    def test_release_label(self):
        assert RELEASE_LABEL == "v0.18 RC"

    def test_capabilities_non_empty(self):
        from tju_info_retrieval.version import CAPABILITIES

        assert len(CAPABILITIES) >= 5
        assert all(c.strip() for c in CAPABILITIES)

    def test_settings_dialog_has_version_tab(self, qt_app):
        from tju_info_retrieval.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog()
        titles = [dialog.tabs.tabText(i)
                  for i in range(dialog.tabs.count())]
        assert "版本 / 关于" in titles
        assert RELEASE_LABEL in dialog.label_version.text()
        dialog.close()
