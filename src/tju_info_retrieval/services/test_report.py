"""测试结果摘要：读取 pytest JUnit XML，输出测试数量与状态。

供验收脚本与 GUI“关于”面板引用，替代手写静态测试基线。
不依赖 pytest 运行时，仅解析 JUnit XML（标准库 xml.etree）。
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

# 默认摘要文件位置（项目根/runtime/test_summary.json）
_DEFAULT_SUMMARY_PATH = Path(__file__).resolve().parents[3] / "runtime" / "test_summary.json"


def summarize_junitxml(path: str | Path) -> dict:
    """解析 pytest --junitxml 生成的 XML，返回测试数量摘要。"""
    tree = ET.parse(path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    total = failures = errors = skipped = 0
    for suite in suites:
        total += int(suite.get("tests", 0) or 0)
        failures += int(suite.get("failures", 0) or 0)
        errors += int(suite.get("errors", 0) or 0)
        skipped += int(suite.get("skipped", 0) or 0)
    passed = total - failures - errors - skipped
    return {
        "total": total,
        "passed": passed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
    }


def format_test_status(summary: dict) -> str:
    """把摘要格式化为人类可读状态（如 '178 passed, 0 failed'）。"""
    return (
        f"{summary.get('passed', 0)} passed, "
        f"{summary.get('failures', 0)} failed, "
        f"{summary.get('errors', 0)} errors"
    )


def write_summary(summary: dict, path: str | Path) -> None:
    """以 UTF-8 JSON 写出测试摘要。"""
    Path(path).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_summary(path: str | Path) -> dict | None:
    """读取测试摘要 JSON；不存在或损坏时返回 None。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and "passed" in data else None
    except Exception:
        return None


def current_test_status(path: str | Path | None = None) -> str:
    """当前测试状态文本：优先读取摘要文件，缺失时返回回退提示。"""
    p = Path(path) if path else _DEFAULT_SUMMARY_PATH
    summary = load_summary(p)
    if summary is not None:
        return format_test_status(summary)
    return "测试摘要未生成（请运行 scripts/run_acceptance_check.py）"
