#!/usr/bin/env python3
"""正式测试：测试结果摘要（JUnit XML 解析 / 状态格式化 / 摘要读写 / 回退）。

注：使用 tempfile 而非 pytest tmp_path（本机 pytest-of 根目录存在扫描权限问题）。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tju_info_retrieval.services import test_report

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuite tests="4" errors="1" failures="1" skipped="1" time="0.5">
  <testcase classname="a" name="t1" time="0.1"/>
  <testcase classname="a" name="t2" time="0.1"><failure msg="f"/></testcase>
  <testcase classname="a" name="t3" time="0.1"><error msg="e"/></testcase>
  <testcase classname="a" name="t4" time="0.1"><skipped msg="s"/></testcase>
</testsuite>
"""


def test_summarize_junitxml():
    with tempfile.TemporaryDirectory() as d:
        xml = Path(d) / "junit.xml"
        xml.write_text(JUNIT, encoding="utf-8")
        s = test_report.summarize_junitxml(xml)
        assert s == {"total": 4, "passed": 1, "failures": 1, "errors": 1, "skipped": 1}


def test_format_test_status():
    s = {"total": 178, "passed": 178, "failures": 0, "errors": 0, "skipped": 0}
    assert test_report.format_test_status(s) == "178 passed, 0 failed, 0 errors"


def test_summary_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test_summary.json"
        s = {"total": 178, "passed": 178, "failures": 0, "errors": 0, "skipped": 0}
        test_report.write_summary(s, p)
        assert test_report.load_summary(p) == s


def test_load_summary_missing_returns_none():
    with tempfile.TemporaryDirectory() as d:
        assert test_report.load_summary(Path(d) / "missing.json") is None


def test_load_summary_corrupt_returns_none():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.json"
        p.write_text("not json", encoding="utf-8")
        assert test_report.load_summary(p) is None


def test_current_test_status_fallback():
    with tempfile.TemporaryDirectory() as d:
        status = test_report.current_test_status(Path(d) / "missing.json")
        assert "未生成" in status or "acceptance" in status


def test_current_test_status_from_file():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test_summary.json"
        test_report.write_summary(
            {"total": 178, "passed": 178, "failures": 0, "errors": 0, "skipped": 0}, p
        )
        assert test_report.current_test_status(p).startswith("178 passed")