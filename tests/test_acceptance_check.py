#!/usr/bin/env python3
"""正式测试：验收脚本（Python 版本 / 依赖 / 核心模块 / 版本信息 / 报告构建）。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_acceptance_check.py"

_spec = importlib.util.spec_from_file_location("run_acceptance_check", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_spec and mod)


class TestAcceptanceCheck:
    def test_python_version_check_passes(self):
        ok, detail = mod.check_python_version()
        assert ok is True
        assert "Python" in detail

    def test_dependencies_check_passes(self):
        ok, detail = mod.check_dependencies()
        assert ok is True
        assert "PySide6" in detail

    def test_core_modules_check_passes(self):
        ok, detail = mod.check_core_modules()
        assert ok is True
        assert "核心模块" in detail

    def test_version_info_check_passes(self):
        ok, detail = mod.check_version_info()
        assert ok is True
        assert "v0.9.0" in detail

    def test_build_report_all_pass(self):
        checks = [
            ("Python 版本", True, "Python 3.13"),
            ("自动测试", True, "178 passed"),
        ]
        report = mod.build_report(checks, {"total": 178, "passed": 178,
                                           "failures": 0, "errors": 0, "skipped": 0})
        assert "# v0.9.0 Acceptance Report" in report
        assert "✅ 验收通过" in report
        assert "total=178" in report

    def test_build_report_with_failure(self):
        checks = [("自动测试", False, "1 failed")]
        report = mod.build_report(checks, {"total": 10, "passed": 9,
                                           "failures": 1, "errors": 0, "skipped": 0})
        assert "❌ 存在失败项" in report

    def test_core_modules_list_covers_sources(self):
        for name in ("sources.cnki", "sources.wanfang", "sources.ieee"):
            assert any(name in m for m in mod.CORE_MODULES)