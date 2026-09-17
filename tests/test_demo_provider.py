#!/usr/bin/env python3
"""正式测试：DemoDataProvider 只读演示数据（v0.18 Phase 2.10-B Phase C）。

覆盖：

1. 数量：3 篇论文 / 2 份 AI 分析 / 1 份调研报告
2. 报告结构：6 章节、来源可追溯、无悬空引用
3. 只读保证：无写调用（AST）、返回副本互不影响、不写真实目录
4. 快照结构：stats / profile / recent_papers / recent_reports
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests._isolation_support import project_root
from tju_info_retrieval.models.enhanced_summary import EnhancedSummary
from tju_info_retrieval.models.library import LibraryRecord
from tju_info_retrieval.models.research_report import (
    REPORT_SECTION_KEYS,
    STATUS_GENERATED,
    ResearchReport,
)
from tju_info_retrieval.services.demo_provider import DemoDataProvider


# ============================================================
# 1. 数量与类型
# ============================================================

class TestDemoProviderCounts:
    def test_three_papers(self):
        papers = DemoDataProvider.papers()
        assert len(papers) == 3
        assert DemoDataProvider.PAPER_COUNT == 3
        for p in papers:
            assert isinstance(p, LibraryRecord)
            assert p.title
            assert p.saved_at          # 最近活动排序需要

    def test_two_analyses(self):
        analyses = DemoDataProvider.analyses()
        assert len(analyses) == 2
        assert DemoDataProvider.ANALYSIS_COUNT == 2
        for a in analyses:
            assert isinstance(a, EnhancedSummary)
            assert a.ai_generated is True
            assert a.provider == "demo"

    def test_analysis_pairs_reference_valid_indices(self):
        pairs = DemoDataProvider.analysis_pairs()
        assert len(pairs) == 2
        for idx, summary in pairs:
            assert 0 <= idx < DemoDataProvider.PAPER_COUNT
            assert isinstance(summary, EnhancedSummary)

    def test_one_report(self):
        report = DemoDataProvider.report()
        assert isinstance(report, ResearchReport)
        assert DemoDataProvider.REPORT_COUNT == 1
        assert report.paper_count == 3
        assert report.status == STATUS_GENERATED


# ============================================================
# 2. 报告结构
# ============================================================

class TestDemoReport:
    def test_six_sections_with_fixed_keys(self):
        report = DemoDataProvider.report()
        assert len(report.sections) == 6
        assert {s.key for s in report.sections} == set(REPORT_SECTION_KEYS)

    def test_sections_ordered(self):
        report = DemoDataProvider.report()
        orders = [s.order for s in report.ordered_sections()]
        assert orders == sorted(orders)

    def test_sources_traceable_no_dangling(self):
        report = DemoDataProvider.report()
        assert report.traced_paper_ids()          # 有来源
        assert report.dangling_source_ids() == set()   # 无悬空引用

    def test_report_has_profile_snapshot(self):
        report = DemoDataProvider.report()
        assert report.profile_snapshot.get("research_area") == "太赫兹技术"
        assert report.profile_version() == "1"


# ============================================================
# 3. 只读保证
# ============================================================

class TestDemoProviderReadOnly:
    def test_module_has_no_store_imports(self):
        """demo_provider 不得导入任何 store（否则存在写路径嫌疑）。"""
        import tju_info_retrieval.services.demo_provider as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        assert not any("store" in name.lower() for name in imported), imported

    def test_module_has_no_write_calls(self):
        """AST 层面无 write / save / mkdir / open 写调用。"""
        import tju_info_retrieval.services.demo_provider as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        banned = {"write_text", "write_bytes", "save", "mkdir", "replace",
                  "unlink", "rename", "dump", "dump_json"}
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in banned:
                offenders.append(node.attr)
            # open(...) 调用
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "open":
                offenders.append("open")
        assert not offenders, f"demo_provider 出现写调用: {offenders}"

    def test_papers_returns_independent_copies(self):
        first = DemoDataProvider.papers()
        first[0].title = "被篡改"
        first[0].authors.append("篡改者")
        second = DemoDataProvider.papers()
        assert second[0].title != "被篡改"
        assert "篡改者" not in second[0].authors

    def test_report_returns_independent_copies(self):
        first = DemoDataProvider.report()
        first.title = "被篡改"
        first.sections[0].content = "被篡改"
        second = DemoDataProvider.report()
        assert second.title != "被篡改"
        assert second.sections[0].content != "被篡改"

    def test_snapshot_returns_independent_copies(self):
        snap = DemoDataProvider.snapshot()
        snap["profile"]["research_area"] = "被篡改"
        snap["recent_papers"][0]["title"] = "被篡改"
        again = DemoDataProvider.snapshot()
        assert again["profile"]["research_area"] == "太赫兹技术"
        assert again["recent_papers"][0]["title"] != "被篡改"

    def test_accessors_do_not_create_files_in_repo(self):
        """调用全部访问器后，仓库 data/ 与 runtime/ 不得出现演示数据文件。"""
        root = project_root()
        data_dir = root / "data"
        runtime_dir = root / "runtime"

        def _snapshot(d: Path) -> set[str]:
            if not d.is_dir():
                return set()
            return {p.name for p in d.iterdir()}

        data_before = _snapshot(data_dir)
        runtime_before = _snapshot(runtime_dir)

        DemoDataProvider.papers()
        DemoDataProvider.analyses()
        DemoDataProvider.report()
        DemoDataProvider.snapshot()

        assert _snapshot(data_dir) == data_before
        assert _snapshot(runtime_dir) == runtime_before


# ============================================================
# 4. 快照结构
# ============================================================

class TestDemoSnapshot:
    def test_stats_shape(self):
        stats = DemoDataProvider.snapshot()["stats"]
        assert stats == {
            "paper_count": 3, "analyzed_count": 2, "report_count": 1}

    def test_profile_shape(self):
        profile = DemoDataProvider.snapshot()["profile"]
        assert profile["research_area"] == "太赫兹技术"
        assert profile["research_stage"] == "master"

    def test_recent_papers_sorted_desc_by_saved_at(self):
        recent = DemoDataProvider.snapshot()["recent_papers"]
        times = [p["saved_at"] for p in recent]
        assert times == sorted(times, reverse=True)

    def test_recent_reports_present(self):
        recent = DemoDataProvider.snapshot()["recent_reports"]
        assert len(recent) == 1
        assert recent[0]["paper_count"] == 3
        assert recent[0]["title"]
