#!/usr/bin/env python3
"""v0.9.0 验收检查脚本。

自动检查：Python 版本、依赖、核心模块导入、版本信息、测试状态（pytest + JUnit XML）。
输出：Markdown 验收报告到 stdout，并写入 runtime/acceptance_report.md。

用法：python scripts/run_acceptance_check.py
退出码：全部通过 = 0，存在失败 = 1。
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

JUNIT_PATH = ROOT / "runtime" / "junit.xml"
REPORT_PATH = ROOT / "runtime" / "acceptance_report.md"

CORE_MODULES = [
    "tju_info_retrieval.version",
    "tju_info_retrieval.models.result",
    "tju_info_retrieval.models.query",
    "tju_info_retrieval.browser.session",
    "tju_info_retrieval.browser.portal",
    "tju_info_retrieval.sources.cnki",
    "tju_info_retrieval.sources.wanfang",
    "tju_info_retrieval.sources.ieee",
    "tju_info_retrieval.services.adapter_registry",
    "tju_info_retrieval.services.query_expansion",
    "tju_info_retrieval.services.search_service",
    "tju_info_retrieval.services.result_merger",
    "tju_info_retrieval.services.ranking",
    "tju_info_retrieval.services.export",
    "tju_info_retrieval.services.report_generator",
    "tju_info_retrieval.services.test_report",
    "tju_info_retrieval.demo_data",
    "tju_info_retrieval.ui.main_window",
    "tju_info_retrieval.ui.worker",
]


def check_python_version() -> tuple[bool, str]:
    v = sys.version_info
    ok = v >= (3, 10)
    detail = f"Python {v.major}.{v.minor}.{v.micro}"
    return ok, detail + ("" if ok else "（要求 >= 3.10）")


def check_dependencies() -> tuple[bool, str]:
    missing = []
    for name in ("PySide6", "playwright"):
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    ok = not missing
    detail = "PySide6, playwright 已安装" if ok else f"缺失依赖: {', '.join(missing)}"
    return ok, detail


def check_core_modules() -> tuple[bool, str]:
    missing = []
    for name in CORE_MODULES:
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    ok = not missing
    detail = f"{len(CORE_MODULES)} 个核心模块导入成功" if ok else f"导入失败: {', '.join(missing)}"
    return ok, detail


def check_version_info() -> tuple[bool, str]:
    try:
        from tju_info_retrieval.version import FEATURES, SUPPORTED_SOURCES, VERSION
        ok = VERSION == "0.9.0" and len(SUPPORTED_SOURCES) == 3 and len(FEATURES) >= 6
        return ok, f"v{VERSION}（数据源 {len(SUPPORTED_SOURCES)} 个，功能 {len(FEATURES)} 项）"
    except Exception as exc:
        return False, f"版本信息读取失败: {type(exc).__name__}"


def run_tests() -> tuple[bool, str, dict | None]:
    """运行 pytest（生成 JUnit XML）并汇总结果。"""
    cmd = [sys.executable, "-m", "pytest", "-q", f"--junitxml={JUNIT_PATH}"]
    try:
        subprocess.run(cmd, cwd=str(ROOT), check=False, timeout=600)
    except Exception as exc:
        return False, f"pytest 运行失败: {type(exc).__name__}", None
    try:
        from tju_info_retrieval.services.test_report import (
            format_test_status,
            summarize_junitxml,
            write_summary,
        )
        summary = summarize_junitxml(JUNIT_PATH)
        write_summary(summary, ROOT / "runtime" / "test_summary.json")
        ok = summary["failures"] == 0 and summary["errors"] == 0
        return ok, format_test_status(summary), summary
    except Exception as exc:
        return False, f"测试摘要解析失败: {type(exc).__name__}: {exc}", None


def build_report(checks: list[tuple[str, bool, str]], summary: dict | None) -> str:
    now = time.strftime("%Y-%m-%d %H:%M")
    lines = ["# v0.9.0 Acceptance Report", "", f"生成时间：{now}", ""]
    lines.append("| 检查项 | 结果 | 详情 |")
    lines.append("| --- | --- | --- |")
    all_ok = True
    for name, ok, detail in checks:
        all_ok = all_ok and ok
        lines.append(f"| {name} | {'✅ PASS' if ok else '❌ FAIL'} | {detail} |")
    lines.append("")
    lines.append(f"**总体结论**：{'✅ 验收通过' if all_ok else '❌ 存在失败项'}")
    if summary:
        lines.append("")
        lines.append(
            f"测试摘要：total={summary['total']}, passed={summary['passed']}, "
            f"failures={summary['failures']}, errors={summary['errors']}, "
            f"skipped={summary['skipped']}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Python 版本", *check_python_version()))
    checks.append(("依赖安装", *check_dependencies()))
    checks.append(("核心模块导入", *check_core_modules()))
    checks.append(("版本信息", *check_version_info()))
    test_ok, test_detail, summary = run_tests()
    checks.append(("自动测试", test_ok, test_detail))

    report = build_report(checks, summary)
    print(report)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n报告已写入: {REPORT_PATH}")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
