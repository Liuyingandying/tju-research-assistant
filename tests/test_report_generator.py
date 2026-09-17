#!/usr/bin/env python3
"""正式测试：基础报告生成（检索条件 / 数据源统计 / 文献列表 / 空安全 / UTF-8）。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest

from tju_info_retrieval.services.report_generator import (
    generate_markdown,
    write_markdown,
)

QUERY_INFO = {
    "research_direction": "太赫兹",
    "expansion_direction": "理论",
    "sources": ["CNKI", "万方"],
}

RESULTS_FULL = [
    {
        "rank": 1,
        "title": "太赫兹成像方法",
        "authors": ["张三", "李四"],
        "source": "期刊A",
        "year": "2026",
        "document_type": "期刊论文",
        "database": "CNKI",
        "detail_url": "https://kns.cnki.net/a1",
        "abstract": "摘要内容",
        "doi": "10.1234/a",
        "keywords": ["太赫兹", "成像"],
        "venue": "期刊A",
        "authors_raw": "张三；李四",
    },
    {
        "rank": 2,
        "title": "Terahertz Method",
        "authors": ["Wang"],
        "source": "IEEE Journal",
        "year": "2024",
        "document_type": "Conference Paper",
        "database": "IEEE Xplore",
        "detail_url": "https://ieee.org/doc/1",
    },
]


class TestReportSections:
    def test_version_in_report(self):
        md = generate_markdown(RESULTS_FULL, QUERY_INFO)
        assert "系统版本：v0.9.0" in md

    def test_basic_sections_present(self):
        md = generate_markdown(RESULTS_FULL, QUERY_INFO)
        assert "# 文献检索报告" in md
        assert "## 检索条件" in md
        assert "## 数据源统计" in md
        assert "## 文献列表" in md

    def test_query_info_rendered(self):
        md = generate_markdown(RESULTS_FULL, QUERY_INFO)
        assert "研究方向：太赫兹" in md
        assert "扩展方向：理论" in md
        assert "数据来源：CNKI、万方" in md
        assert "结果数量：2" in md

    def test_query_info_missing_is_safe(self):
        md = generate_markdown(RESULTS_FULL)
        assert "研究方向：—" in md
        assert "数据来源：—" in md

    def test_literature_entry_fields(self):
        md = generate_markdown(RESULTS_FULL, QUERY_INFO)
        assert "### 1. 太赫兹成像方法" in md
        assert "作者：张三；李四" in md
        assert "来源：期刊A" in md
        assert "年份：2026" in md
        assert "数据库：CNKI" in md
        assert "类型：期刊论文" in md
        assert "https://kns.cnki.net/a1" in md
        assert "### 2. Terahertz Method" in md

    def test_conditional_fields_only_when_present(self):
        md = generate_markdown(RESULTS_FULL, QUERY_INFO)
        assert "DOI：10.1234/a" in md
        assert "关键词：太赫兹、成像" in md
        # 第二条无 DOI/关键词，不应出现该条目的值
        assert "DOI：—" not in md  # 缺失字段直接省略整行


class TestSourceStatistics:
    def test_statistics_counts_per_database(self):
        md = generate_markdown(RESULTS_FULL, QUERY_INFO)
        assert "| CNKI | 1 |" in md
        assert "| IEEE Xplore | 1 |" in md

    def test_statistics_multi_result_same_database(self):
        results = RESULTS_FULL + [
            dict(RESULTS_FULL[0], rank=3, title="第三条", database="CNKI"),
        ]
        md = generate_markdown(results, QUERY_INFO)
        assert "| CNKI | 2 |" in md
        assert "| IEEE Xplore | 1 |" in md
        assert "共 3 条" in md


class TestEmptySafety:
    def test_empty_results(self):
        md = generate_markdown([], QUERY_INFO)
        assert "共 0 条" in md
        assert "（无结果）" in md
        assert "（无检索结果）" in md

    def test_missing_fields_placeholder(self):
        results = [{"rank": 1, "title": "T", "database": "CNKI"}]
        md = generate_markdown(results)
        assert "### 1. T" in md
        assert "作者：—" in md
        assert "来源：—" in md
        assert "链接：—" in md
        assert "摘要" not in md.replace("摘要：—", "")  # 无摘要字段时整行省略
        assert "DOI" not in md
        assert "关键词" not in md

    def test_none_title_safe(self):
        md = generate_markdown([{"rank": 1, "title": None, "database": "CNKI"}])
        assert "### 1. —" in md


class TestWrite:
    def test_write_markdown_utf8(self):
        md = generate_markdown(RESULTS_FULL, QUERY_INFO)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "报告.md"
            write_markdown(md, p)
            text = p.read_text(encoding="utf-8")
            assert "太赫兹成像方法" in text
            assert text == md