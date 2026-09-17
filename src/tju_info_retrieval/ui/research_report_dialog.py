"""科研调研报告查看窗口（v0.18 Phase 2.9-C-E-E）。

展示内容：

- 标题；
- 基本信息：论文数量 / 生成时间 / Provider（+模型）/ Prompt 版本 / 画像版本 / 状态；
- 章节列表（左）与章节正文（右），每个章节显示 `来源：[P#]`；
- 来源论文附录（[P#] 标题（作者, 年份））；
- 降级/缓存/告警提示与"基于旧画像/旧 Prompt"过期提示。

本窗口**只读展示**：不调用 LLM、不写论文库。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from tju_info_retrieval.models.research_report import (
    REPORT_STATUS_LABELS,
    STATUS_GENERATED,
    ReportSource,
    ResearchReport,
)
from tju_info_retrieval.services.report_exporter import (
    export_docx,
    export_markdown,
    export_pdf,
)
from tju_info_retrieval.services.report_service import ReportGenerationResult

_STATUS_DISPLAY = {
    STATUS_GENERATED: "AI 生成",
    "draft": "基础报告（未调用 AI）",
    "failed": "生成失败",
}


class ResearchReportDialog(QDialog):
    """调研报告查看（非模态使用；只读）。"""

    def __init__(
        self,
        report,
        parent: QWidget | None = None,
        result: ReportGenerationResult | None = None,
        current_profile_version: str = "",
        current_prompt_version: str = "",
        library_paper_ids=None,
    ) -> None:
        super().__init__(parent)
        if isinstance(report, dict):
            # 既接受"报告 dict"，也接受 Worker 传来的
            # ReportGenerationResult.to_dict()（含 report / status / ok 键）
            if any(k in report for k in ("report", "status", "ok")):
                parsed = ReportGenerationResult.from_dict(report)
                if result is None:
                    result = parsed
                report = parsed.report or ResearchReport()
            else:
                report = ResearchReport.from_dict(report)
        self._report: ResearchReport = report or ResearchReport()
        self._result = result
        self._current_profile_version = str(current_profile_version or "")
        self._current_prompt_version = str(current_prompt_version or "")
        self._library_ids = {str(p) for p in (library_paper_ids or [])}
        self._setup_ui()

    # ---------- 对外只读访问（供测试/调用方检查） ----------

    @property
    def report(self) -> ResearchReport:
        return self._report

    # ---------- UI ----------

    def _setup_ui(self) -> None:
        self.setWindowTitle("科研调研报告")
        self.resize(900, 640)
        self.setMinimumSize(640, 460)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        title = QLabel(self._report.title or "（未命名报告）")
        title.setObjectName("summaryTitle")
        title.setWordWrap(True)
        root.addWidget(title)

        self.label_meta = QLabel(self._meta_text())
        self.label_meta.setObjectName("summaryMeta")
        self.label_meta.setWordWrap(True)
        root.addWidget(self.label_meta)

        # 提示区：降级 / 缓存 / 过期 / 告警
        self.label_notice = QLabel("")
        self.label_notice.setObjectName("summaryMeta")
        self.label_notice.setWordWrap(True)
        notices = self._notice_texts()
        if notices:
            self.label_notice.setText("\n".join(notices))
        self.label_notice.setVisible(bool(notices))
        root.addWidget(self.label_notice)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_section_list())
        splitter.addWidget(self._build_content_tabs())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self.btn_md = QPushButton("导出Markdown")
        self.btn_md.setObjectName("btnSecondary")
        self.btn_md.clicked.connect(self._on_export_markdown)
        btn_row.addWidget(self.btn_md)

        self.btn_docx = QPushButton("导出Word")
        self.btn_docx.setObjectName("btnSecondary")
        self.btn_docx.clicked.connect(self._on_export_docx)
        btn_row.addWidget(self.btn_docx)

        self.btn_pdf = QPushButton("导出PDF")
        self.btn_pdf.setObjectName("btnSecondary")
        self.btn_pdf.clicked.connect(self._on_export_pdf)
        btn_row.addWidget(self.btn_pdf)

        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("btnGhost")
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

        if self.list_sections.count():
            self.list_sections.setCurrentRow(0)

    def _build_section_list(self) -> QWidget:
        self.list_sections = QListWidget(self)
        self.list_sections.setObjectName("reportSectionList")
        for section in self._report.ordered_sections():
            label = section.title or "（未命名章节）"
            if section.insufficient:
                label += "（证据不足）"
            if not section.source_papers:
                label += "（无来源）"
            self.list_sections.addItem(QListWidgetItem(label))
        self.list_sections.currentRowChanged.connect(self._on_section_changed)
        return self.list_sections

    def _build_content_tabs(self) -> QWidget:
        self.tabs = QTabWidget(self)
        self.browser_content = QTextBrowser(self)
        self.browser_content.setOpenExternalLinks(False)
        self.tabs.addTab(self.browser_content, "章节内容")

        self.browser_sources = QTextBrowser(self)
        self.browser_sources.setOpenExternalLinks(False)
        self.browser_sources.setHtml(self._sources_html())
        self.tabs.addTab(self.browser_sources, "来源论文附录")
        return self.tabs

    # ---------- 渲染 ----------

    def _meta_text(self) -> str:
        report = self._report
        parts = [f"论文数量：{report.paper_count} 篇"]
        if report.created_time:
            parts.append(f"生成时间：{report.created_time[:19]}")
        provider = report.provider or "（未标注）"
        if report.model:
            provider = f"{provider} / {report.model}"
        parts.append(f"Provider：{provider}")
        parts.append(f"Prompt 版本：{report.report_prompt_version or '-'}")
        profile_version = report.profile_version()
        parts.append(f"画像版本：{('p' + profile_version) if profile_version else '未设置'}")
        parts.append(
            "状态：" + _STATUS_DISPLAY.get(
                report.status, REPORT_STATUS_LABELS.get(report.status,
                                                       report.status)))
        return "  ｜  ".join(parts)

    def _notice_texts(self) -> list[str]:
        notices: list[str] = []
        if self._result is not None:
            if self._result.warning:
                notices.append(self._result.warning)
            if self._result.message and self._result.degraded_reason:
                notices.append(self._result.message)
            if self._result.from_cache:
                notices.append("该报告来自缓存（输入与版本未变化，未重复调用 AI）。")
            for problem in self._result.validation_problems:
                notices.append(f"来源校验提示：{problem}")
        if self._report.is_stale(self._current_profile_version,
                                 self._current_prompt_version):
            notices.append("该报告基于旧科研画像或旧 Prompt 版本，建议重新生成。")
        if self._library_ids:
            removed = self._report.missing_source_paper_ids(self._library_ids)
            if removed:
                notices.append(
                    f"有 {len(removed)} 篇来源论文已从论文库移除"
                    "（报告内容仍保留，可独立阅读）。")
        return notices

    def _papers_by_id(self) -> dict[str, ReportSource]:
        return {p.paper_id: p for p in self._report.papers if p.paper_id}

    def _section_sources_text(self, section) -> str:
        by_id = self._papers_by_id()
        labels = []
        for paper_id in section.source_papers:
            paper = by_id.get(paper_id)
            if paper is None:
                labels.append(f"[?{paper_id[:8]}]")
            else:
                labels.append(f"[P{paper.index}]")
        return " ".join(labels)

    def _on_section_changed(self, row: int) -> None:
        sections = self._report.ordered_sections()
        if row < 0 or row >= len(sections):
            self.browser_content.clear()
            return
        section = sections[row]
        lines = [section.content or "（无正文）", ""]
        sources = self._section_sources_text(section)
        lines.append(f"来源：{sources or '（无）'}")
        if section.insufficient:
            lines.append("说明：本节标注为证据不足（未调用 AI 或证据不充分）。")
        self.browser_content.setPlainText("\n".join(lines))

    def _sources_html(self) -> str:
        papers = self._report.papers
        if not papers:
            return "<p>本报告没有来源论文快照。</p>"
        rows = ["<h3>来源论文附录</h3>", "<table cellspacing='0' cellpadding='4'>"]
        for paper in papers:
            authors = "、".join(paper.authors[:3]) or "-"
            year = paper.year or "-"
            meta_bits = [b for b in (paper.source, paper.document_type,
                                     paper.direction_match_level,
                                     paper.reading_priority,
                                     paper.doi) if b]
            rows.append(
                f"<tr><td><b>[P{paper.index}]</b></td>"
                f"<td>{_escape(paper.title)}<br/>"
                f"<span style='color:#666'>{_escape(authors)}，{_escape(year)}"
                f"{'，' + _escape('，'.join(meta_bits)) if meta_bits else ''}"
                f"</span></td></tr>")
        rows.append("</table>")
        return "".join(rows)

    # ---------- 测试辅助 ----------

    def section_sources_text(self, index: int) -> str:
        """第 index 个章节的来源标注文本（供测试断言）。"""
        sections = self._report.ordered_sections()
        if index < 0 or index >= len(sections):
            return ""
        return self._section_sources_text(sections[index])

    # ---------- 导出 ----------

    def _pick_save_path(self, filter_text: str, default_ext: str) -> str | None:
        """弹出文件保存对话框，返回用户选择的路径（取消返回 None）。"""
        title = f"保存报告 ({filter_text})"
        path, _ = QFileDialog.getSaveFileName(
            self, title, "", filter_text)
        if not path:
            return None
        if not path.lower().endswith("." + default_ext.lower()):
            path += "." + default_ext
        return path

    def _show_export_result(self, success: bool, path: str = "",
                            error: str = "") -> None:
        """导出完成后弹出提示。"""
        if success:
            QMessageBox.information(
                self, "导出成功",
                f"报告已导出到：\n{path}")
        else:
            QMessageBox.critical(
                self, "导出失败",
                f"导出报告时发生错误：\n{error}")

    def _on_export_markdown(self) -> None:
        path = self._pick_save_path("Markdown (*.md)", "md")
        if path is None:
            return
        try:
            actual = export_markdown(self._report, path)
            self._show_export_result(True, actual)
        except ImportError as exc:
            self._show_export_result(False, error=str(exc))
        except Exception as exc:
            self._show_export_result(False, error=f"{type(exc).__name__}: {exc}")

    def _on_export_docx(self) -> None:
        path = self._pick_save_path("Word (*.docx)", "docx")
        if path is None:
            return
        try:
            actual = export_docx(self._report, path)
            self._show_export_result(True, actual)
        except ImportError as exc:
            self._show_export_result(False, error=str(exc))
        except Exception as exc:
            self._show_export_result(False, error=f"{type(exc).__name__}: {exc}")

    def _on_export_pdf(self) -> None:
        path = self._pick_save_path("PDF (*.pdf)", "pdf")
        if path is None:
            return
        try:
            actual = export_pdf(self._report, path)
            self._show_export_result(True, actual)
        except ImportError as exc:
            self._show_export_result(False, error=str(exc))
        except Exception as exc:
            self._show_export_result(False, error=f"{type(exc).__name__}: {exc}")


def _escape(text: str) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))
