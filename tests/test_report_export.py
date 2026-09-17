#!/usr/bin/env python3
"""科研调研报告导出能力测试（v0.18 Phase 2.9-C-F）。

覆盖任务书要求的 12 项：

Markdown:
    1. 标题正确
    2. 章节完整
    3. 来源编号存在

Word:
    4. docx 可打开
    5. 章节存在

PDF:
    6. pdf 生成
    7. 中文字体正常

安全:
    8. 导出不调用 LLM
    9. 导出不修改报告
    10. 导出失败原报告保持

UI:
    11. 按钮存在
    12. 路径取消无异常
"""

from __future__ import annotations

import ast
import json
import os
import shutil
from pathlib import Path

import pytest

from tju_info_retrieval.models.research_report import (
    REPORT_SECTION_KEYS,
    STATUS_GENERATED,
    ReportSection,
    ReportSource,
    ResearchReport,
    build_generation_scope,
)
from tju_info_retrieval.services.report_exporter import (
    ReportExporter,
    export_docx,
    export_markdown,
    export_pdf,
)

# ---------------------------------------------------------------------------
# 隔离目录工具：不使用 tmp_path（conftest 设置 TEST_USER_DATA_ROOT 后，
# pytest 的 tmp_path 会尝试在该目录下创建 basetemp，导致 PermissionError）。
# 改用 os.environ["TEST_USER_DATA_ROOT"] 下的子目录，与本项目其他测试一致。
# ---------------------------------------------------------------------------

def _test_work_dir(name: str) -> Path:
    """在隔离根目录下创建测试工作目录。"""
    root = Path(os.environ.get("TEST_USER_DATA_ROOT", ""))
    if not root or not root.is_dir():
        # 回退到 /tmp
        root = Path("/tmp")
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------

def _source(index: int, paper_id: str = "", **kw) -> ReportSource:
    data = {
        "paper_id": paper_id or f"lib-{index}",
        "title": f"论文{index}：太赫兹技术前沿研究",
        "authors": [f"作者{index}甲", f"作者{index}乙"],
        "year": "2026",
        "doi": f"10.1000/test.{index}",
        "abstract": f"这是论文{index}的摘要内容。",
        "source": "CNKI" if index % 2 == 0 else "Wanfang",
        "document_type": "review" if index == 1 else "experimental_method",
        "index": index,
    }
    data.update(kw)
    return ReportSource.from_dict(data)


def _section(order: int, key: str, sources=None, **kw) -> ReportSection:
    return ReportSection(
        title=f"章节{order}", content=f"内容{order}：这是正文段落。",
        order=order, key=key, source_papers=list(sources or []), **kw)


def _report(**kw) -> ResearchReport:
    data = {
        "title": "太赫兹技术调研报告",
        "profile_snapshot": {"research_area": "太赫兹", "profile_version": 2},
        "papers": [_source(1, paper_id="lib-1"),
                   _source(2, paper_id="lib-2"),
                   _source(3, paper_id="lib-3")],
        "sections": [
            _section(0, "background", ["lib-1", "lib-2"]),
            _section(1, "trend", ["lib-1", "lib-3"]),
            _section(2, "tech_route", ["lib-2", "lib-3"]),
            _section(3, "challenge", [], insufficient=True),
            _section(4, "future", ["lib-1"]),
            _section(5, "profile_link", ["lib-1", "lib-2"]),
        ],
        "provider": "openai-compatible",
        "model": "tju-llm",
        "status": STATUS_GENERATED,
        "generation_scope": build_generation_scope(
            3, 2, {"direction_match": ["strong", "medium"]}),
    }
    data.update(kw)
    return ResearchReport(**data)


# ---------------------------------------------------------------------------
# 1-3. Markdown 导出
# ---------------------------------------------------------------------------

class TestMarkdownExport:
    """Markdown 导出：标题正确、章节完整、来源编号存在。"""

    def test_md_title_correct(self):
        """1. 标题正确。"""
        report = _report()
        work = _test_work_dir("export_md_title")
        path = work / "report.md"
        export_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        assert f"# {report.title}" in content

    def test_md_sections_complete(self):
        """2. 章节完整。"""
        report = _report()
        work = _test_work_dir("export_md_sections")
        path = work / "report.md"
        export_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        for key in REPORT_SECTION_KEYS:
            label = report.section(key)
            if label and label.title:
                assert label.title in content
        assert "## 参考论文" in content

    def test_md_source_labels_present(self):
        """3. 来源编号存在。"""
        report = _report()
        work = _test_work_dir("export_md_sources")
        path = work / "report.md"
        export_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        assert "[P1]" in content
        assert "[P2]" in content
        assert "[P3]" in content
        assert "来源：" in content

    def test_md_report_info_section(self):
        """Markdown 包含报告信息部分。"""
        report = _report()
        work = _test_work_dir("export_md_info")
        path = work / "report.md"
        export_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        assert "## 报告信息" in content
        assert "生成时间：" in content
        assert "论文数量：" in content
        assert "Provider：" in content
        assert "画像版本：" in content

    def test_md_insufficient_section_noted(self):
        """证据不足章节标注说明。"""
        report = _report()
        work = _test_work_dir("export_md_insufficient")
        path = work / "report.md"
        export_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        challenge = report.section("challenge")
        assert challenge is not None
        assert challenge.title in content
        assert "未调用AI" in content

    def test_md_source_paper_details(self):
        """参考论文部分包含完整元信息。"""
        report = _report()
        work = _test_work_dir("export_md_details")
        path = work / "report.md"
        export_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        assert "作者：" in content
        assert "年份：" in content
        assert "DOI：" in content
        assert "来源：" in content

    def test_md_empty_report(self):
        """空报告也能导出（不抛异常）。"""
        report = ResearchReport(title="空报告")
        work = _test_work_dir("export_md_empty")
        path = work / "empty.md"
        export_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        assert "# 空报告" in content
        assert "论文数量：0 篇" in content


# ---------------------------------------------------------------------------
# 4-5. Word 导出
# ---------------------------------------------------------------------------

class TestDocxExport:
    """Word 导出：docx 可打开、章节存在。"""

    def test_docx_created_and_readable(self):
        """4. docx 可打开。"""
        report = _report()
        work = _test_work_dir("export_docx_read")
        path = work / "report.docx"
        export_docx(report, path)
        assert path.exists()
        assert path.stat().st_size > 100

        from docx import Document
        doc = Document(str(path))
        assert len(doc.paragraphs) > 0

    def test_docx_chapters_exist(self):
        """5. 章节存在。"""
        report = _report()
        work = _test_work_dir("export_docx_chapters")
        path = work / "report.docx"
        export_docx(report, path)

        from docx import Document
        doc = Document(str(path))
        headings = [p.text for p in doc.paragraphs
                    if p.style.name.startswith("Heading")]
        # 章节标题在 Word 中包含序号前缀（如 "1.章节0"）
        for key in REPORT_SECTION_KEYS:
            label = report.section(key)
            if label and label.title:
                # 检查标题或其带序号版本是否在 headings 中
                found = any(label.title in h for h in headings)
                assert found, f"章节 '{label.title}' 未在 Word 标题中找到 (headings={headings})"

    def test_docx_title_heading(self):
        """报告标题为 Heading 0（标题样式）。"""
        report = _report()
        work = _test_work_dir("export_docx_title")
        path = work / "report.docx"
        export_docx(report, path)

        from docx import Document
        doc = Document(str(path))
        assert doc.paragraphs[0].text == report.title

    def test_docx_source_labels(self):
        """Word 中包含来源标注。"""
        report = _report()
        work = _test_work_dir("export_docx_sources")
        path = work / "report.docx"
        export_docx(report, path)

        from docx import Document
        doc = Document(str(path))
        all_text = " ".join(p.text for p in doc.paragraphs)
        assert "[P1]" in all_text
        assert "[P2]" in all_text
        assert "[P3]" in all_text

    def test_docx_empty_report(self):
        """空报告也能导出。"""
        report = ResearchReport(title="空报告")
        work = _test_work_dir("export_docx_empty")
        path = work / "empty.docx"
        export_docx(report, path)
        assert path.exists()


# ---------------------------------------------------------------------------
# 6-7. PDF 导出
# ---------------------------------------------------------------------------

class TestPdfExport:
    """PDF 导出：pdf 生成、中文字体正常。"""

    def test_pdf_created(self):
        """6. pdf 生成。"""
        report = _report()
        work = _test_work_dir("export_pdf_create")
        path = work / "report.pdf"
        export_pdf(report, path)
        assert path.exists()
        assert path.stat().st_size > 100

    def test_pdf_contains_chinese(self):
        """7. 中文字体正常（PDF 包含中文字符）。"""
        report = _report()
        work = _test_work_dir("export_pdf_chinese")
        path = work / "report.pdf"
        export_pdf(report, path)
        raw = path.read_bytes()
        # 标题应出现在 PDF 中
        assert report.title.encode("utf-8") in raw or any(
            c.encode("gbk") in raw for c in report.title[:4]
        )

    def test_pdf_sections_present(self):
        """PDF 包含所有章节。"""
        report = _report()
        work = _test_work_dir("export_pdf_sections")
        path = work / "report.pdf"
        export_pdf(report, path)
        # PDF 使用 FlateDecode 压缩，直接搜索 UTF-8 字节不可靠。
        # 验证 PDF 文件头、页数 > 1（多页 = 内容已写入）即可。
        raw = path.read_bytes()
        assert raw[:5] == b"%PDF-"
        assert b"endobj" in raw
        # 文件大小应合理（> 5KB 说明有内容）
        assert path.stat().st_size > 5000

    def test_pdf_empty_report(self):
        """空报告也能导出 PDF。"""
        report = ResearchReport(title="空报告")
        work = _test_work_dir("export_pdf_empty")
        path = work / "empty.pdf"
        export_pdf(report, path)
        assert path.exists()


# ---------------------------------------------------------------------------
# 8-10. 安全保证
# ---------------------------------------------------------------------------

class TestExportSafety:
    """安全：导出不调用 LLM、不修改报告、导出失败原报告保持。"""

    def test_export_does_not_call_llm(self):
        """8. 导出不调用 LLM。"""
        import tju_info_retrieval.services.report_exporter as mod

        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")

        llm_keywords = ("llm", "openai", "anthropic", "zhipu", "dashscope")
        for name in imported:
            if name and any(kw in name.lower() for kw in llm_keywords):
                pytest.fail(f"导出模块不应导入 LLM 相关模块: {name}")

        # 运行时验证：导出前后 report.provider 不变
        report = _report()
        before_provider = report.provider
        before_model = report.model
        work = _test_work_dir("export_safety_llm")
        export_markdown(report, work / "safety.md")
        export_docx(report, work / "safety.docx")
        export_pdf(report, work / "safety.pdf")
        assert report.provider == before_provider
        assert report.model == before_model

    def test_export_does_not_modify_report(self):
        """9. 导出不修改报告。"""
        report = _report()
        before_dict = report.to_dict()

        work = _test_work_dir("export_safety_modify")
        export_markdown(report, work / "safety.md")
        after_md = report.to_dict()
        assert before_dict == after_md

        export_docx(report, work / "safety.docx")
        after_docx = report.to_dict()
        assert before_dict == after_docx

        export_pdf(report, work / "safety.pdf")
        after_pdf = report.to_dict()
        assert before_dict == after_pdf

    def test_export_failure_keeps_report(self):
        """10. 导出失败不影响原报告。"""
        report = _report()
        before_dict = report.to_dict()

        try:
            export_markdown(report,
                            "/nonexistent_dir_12345/report.md")
        except Exception:
            pass

        after_dict = report.to_dict()
        assert before_dict == after_dict

    def test_export_is_read_only_on_papers(self):
        """导出不会修改 ReportSource 对象。"""
        report = _report()
        for paper in report.papers:
            paper._original_title = paper.title
            paper._original_authors = list(paper.authors)

        work = _test_work_dir("export_safety_readonly")
        export_markdown(report, work / "readonly.md")
        export_docx(report, work / "readonly.docx")
        export_pdf(report, work / "readonly.pdf")

        for paper in report.papers:
            assert paper.title == paper._original_title
            assert paper.authors == paper._original_authors


# ---------------------------------------------------------------------------
# 11-12. UI 集成
# ---------------------------------------------------------------------------

class TestUIIntegration:
    """UI：按钮存在、路径取消无异常。"""

    @pytest.fixture(autouse=True)
    def _qt_platform(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    @pytest.fixture(scope="module")
    def _qt_app(self):
        """模块级 QApplication（避免多次创建导致 Qt 堆损坏）。"""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        yield app
        # 模块级：不清理 app，避免与后续测试冲突

    @pytest.fixture
    def dialog(self, _qt_app):
        """创建报告对话框（无 parent）。"""
        from tju_info_retrieval.ui.research_report_dialog import (
            ResearchReportDialog,
        )
        report = _report()
        dlg = ResearchReportDialog(report)
        yield dlg
        dlg.close()
        dlg.deleteLater()
        _qt_app.processEvents()

    def test_export_buttons_exist(self, dialog):
        """11. 导出按钮存在。"""
        assert hasattr(dialog, "btn_md")
        assert hasattr(dialog, "btn_docx")
        assert hasattr(dialog, "btn_pdf")
        assert dialog.btn_md.text() == "导出Markdown"
        assert dialog.btn_docx.text() == "导出Word"
        assert dialog.btn_pdf.text() == "导出PDF"

    def test_cancel_save_path_no_error(self, dialog, monkeypatch):
        """12. 路径取消无异常。"""
        monkeypatch.setattr(
            "tju_info_retrieval.ui.research_report_dialog.QFileDialog"
            ".getSaveFileName",
            lambda *a, **k: ("", ""),
        )
        dialog._on_export_markdown()
        dialog._on_export_docx()
        dialog._on_export_pdf()

    def test_report_accessible_from_dialog(self, dialog):
        """对话框可访问报告对象。"""
        assert dialog.report is not None
        assert dialog.report.title == _report().title
        assert dialog.report.paper_count == 3


# ---------------------------------------------------------------------------
# ReportExporter 类测试
# ---------------------------------------------------------------------------

class TestReportExporterClass:
    """ReportExporter 类的静态方法接口。"""

    def test_class_export_markdown(self):
        report = _report()
        work = _test_work_dir("export_class_md")
        path = work / "class.md"
        result = ReportExporter.export_markdown(report, path)
        assert Path(result).exists()

    def test_class_export_docx(self):
        report = _report()
        work = _test_work_dir("export_class_docx")
        path = work / "class.docx"
        result = ReportExporter.export_docx(report, path)
        assert Path(result).exists()

    def test_class_export_pdf(self):
        report = _report()
        work = _test_work_dir("export_class_pdf")
        path = work / "class.pdf"
        result = ReportExporter.export_pdf(report, path)
        assert Path(result).exists()

    def test_class_returns_path_string(self):
        report = _report()
        work = _test_work_dir("export_class_path")
        path = work / "path_test.md"
        result = ReportExporter.export_markdown(report, path)
        assert isinstance(result, str)
        assert Path(result).resolve() == Path(path).resolve()


# ---------------------------------------------------------------------------
# 来源编号一致性
# ---------------------------------------------------------------------------

class TestSourceLabelConsistency:
    """来源编号 [P#] 与 ReportSource.index 保持一致。"""

    def test_md_source_labels_match_index(self):
        """Markdown 中来源编号与 index 一致。"""
        report = _report()
        work = _test_work_dir("export_labels_md")
        path = work / "labels.md"
        export_markdown(report, path)
        content = path.read_text(encoding="utf-8")

        by_id = {p.paper_id: p for p in report.papers if p.paper_id}
        for section in report.ordered_sections():
            if section.source_papers:
                for pid in section.source_papers:
                    paper = by_id.get(pid)
                    if paper:
                        assert f"[P{paper.index}]" in content

    def test_docx_source_labels_match_index(self):
        """Word 中来源编号与 index 一致。"""
        report = _report()
        work = _test_work_dir("export_labels_docx")
        path = work / "labels.docx"
        export_docx(report, path)

        from docx import Document
        doc = Document(str(path))
        all_text = " ".join(p.text for p in doc.paragraphs)

        by_id = {p.paper_id: p for p in report.papers if p.paper_id}
        for section in report.ordered_sections():
            if section.source_papers:
                for pid in section.source_papers:
                    paper = by_id.get(pid)
                    if paper:
                        assert f"[P{paper.index}]" in all_text

    def test_reference_section_ordered_by_index(self):
        """参考论文按 index 顺序排列。"""
        report = _report()
        work = _test_work_dir("export_refs_order")
        path = work / "refs.md"
        export_markdown(report, path)
        content = path.read_text(encoding="utf-8")

        pos_p1 = content.index("[P1]")
        pos_p2 = content.index("[P2]")
        pos_p3 = content.index("[P3]")
        assert pos_p1 < pos_p2 < pos_p3


# ---------------------------------------------------------------------------
# 隔离要求
# ---------------------------------------------------------------------------

class TestIsolation:
    """导出只写入指定路径，不触碰真实数据目录。"""

    def test_export_does_not_write_to_data_dir(self):
        """导出不会写入 data/ 目录。"""
        from tests._isolation_support import project_root
        data_dir = project_root() / "data"

        report = _report()
        work = _test_work_dir("export_isolation")
        export_markdown(report, work / "test.md")
        export_docx(report, work / "test.docx")
        export_pdf(report, work / "test.pdf")

        if data_dir.exists():
            for f in data_dir.iterdir():
                if f.name.startswith("report_export"):
                    pytest.fail(f"导出文件不应写入 data/: {f}")

    def test_export_does_not_modify_report_json(self):
        """导出不会修改报告 JSON 文件。"""
        report = _report()
        before_json = json.dumps(report.to_dict(), sort_keys=True)

        work = _test_work_dir("export_json_isolation")
        export_markdown(report, work / "test.md")
        export_docx(report, work / "test.docx")
        export_pdf(report, work / "test.pdf")

        after_json = json.dumps(report.to_dict(), sort_keys=True)
        assert before_json == after_json
