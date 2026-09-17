"""科研展示首页 / Dashboard（v0.18 Phase 2.10-B Phase B）。

目的：让导师 / 同学第一次打开软件时，30 秒内理解系统价值。
一屏呈现 5 段内容：

1. 系统名称（TJU Research Assistant）
2. 科研画像摘要（研究方向 / 研究阶段）
3. 统计（论文库数量 / AI 分析数量 / 报告数量）
4. 最近活动（最近收藏论文 / 最近生成报告）
5. 快捷入口（搜索论文 / 打开论文库 / 生成报告）

设计约束：

- **独立模块**：不修改 MainWindow 既有布局与卡片；
- **只读**：只调用既有 store 的只读 API，不写任何文件；
- **不新增业务调用链**：快捷入口仅发出 Qt 信号，由 MainWindow 接回
  既有槽（搜索聚焦 / 打开论文库）；
- **演示模式**：``demo_mode=True`` 时改用 ``DemoDataProvider``（只读演示数据），
  用于展示"论文 → AI 分析 → 报告"完整链路。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from tju_info_retrieval.models.research_profile import (
    RESEARCH_STAGE_LABELS,
)
from tju_info_retrieval.services.demo_provider import DemoDataProvider
from tju_info_retrieval.ui import theme
from tju_info_retrieval.version import PRODUCT_NAME, RELEASE_LABEL

# 最近活动展示条数上限
_RECENT_LIMIT = 5

# 卡片本地样式（不改共享 theme.py，避免影响既有界面）
_CARD_QSS = (
    "QFrame#dashCard { background: #ffffff; border: 1px solid #dfe6f0;"
    " border-radius: 10px; }"
)
_TITLE_QSS = "color: #1A5CB0; font-size: 22px; font-weight: 700;"
_SUBTITLE_QSS = "color: #5a6b82; font-size: 12px;"
_CARD_TITLE_QSS = "color: #1b2a41; font-size: 13px; font-weight: 600;"
_STAT_VALUE_QSS = "color: #1A5CB0; font-size: 26px; font-weight: 700;"
_STAT_LABEL_QSS = "color: #5a6b82; font-size: 12px;"
_MUTED_QSS = "color: #8a97a8; font-size: 12px;"


class DashboardWidget(QWidget):
    """科研展示首页内容（可嵌入，也可由 DashboardDialog 独立承载）。"""

    search_requested = Signal()
    library_requested = Signal()
    report_requested = Signal()

    def __init__(
        self,
        library_store=None,
        report_store=None,
        profile_store=None,
        demo_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._demo_mode = bool(demo_mode)
        self._library_store = library_store
        self._report_store = report_store
        self._profile_store = profile_store
        self._snapshot_data: dict = {}
        self._setup_ui()
        self.refresh()

    # ---------- 对外只读访问（供测试/调用方） ----------

    @property
    def demo_mode(self) -> bool:
        return self._demo_mode

    def snapshot(self) -> dict:
        """最近一次渲染所用的聚合数据（副本）。"""
        return dict(self._snapshot_data)

    def stat_values(self) -> dict:
        return dict(self._snapshot_data.get("stats") or {})

    def recent_paper_titles(self) -> list[str]:
        return [p.get("title", "")
                for p in (self._snapshot_data.get("recent_papers") or [])]

    def recent_report_titles(self) -> list[str]:
        return [r.get("title", "")
                for r in (self._snapshot_data.get("recent_reports") or [])]

    # ---------- UI ----------

    def _setup_ui(self) -> None:
        self.setObjectName("dashboardRoot")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget(scroll)
        content.setObjectName("dashboardContent")
        content.setStyleSheet(_CARD_QSS)
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        root.addWidget(self._build_header())
        root.addWidget(self._build_profile_card())
        root.addWidget(self._build_stats_card())
        root.addWidget(self._build_recent_card())
        root.addWidget(self._build_quick_actions())
        root.addStretch(1)

    def _build_header(self) -> QWidget:
        header = QWidget(self)
        box = QVBoxLayout(header)
        box.setContentsMargins(4, 0, 4, 0)
        box.setSpacing(2)

        self.label_system_name = QLabel(PRODUCT_NAME, header)
        self.label_system_name.setStyleSheet(_TITLE_QSS)
        box.addWidget(self.label_system_name)

        label_tagline = QLabel(
            "天津大学 · 多源论文检索与 AI 调研助手", header)
        label_tagline.setStyleSheet(_SUBTITLE_QSS)
        box.addWidget(label_tagline)

        label_version = QLabel(f"发布版本：{RELEASE_LABEL}", header)
        label_version.setStyleSheet(_MUTED_QSS)
        box.addWidget(label_version)
        return header

    def _build_profile_card(self) -> QFrame:
        card, layout = self._make_card("科研画像")
        self.label_profile_area = QLabel("", card)
        self.label_profile_area.setWordWrap(True)
        self.label_profile_stage = QLabel("", card)
        self.label_profile_stage.setWordWrap(True)
        layout.addWidget(self.label_profile_area)
        layout.addWidget(self.label_profile_stage)
        return card

    def _build_stats_card(self) -> QFrame:
        card, layout = self._make_card("数据概览")
        grid = QGridLayout()
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(24)

        self.label_stat_papers = self._stat_cell(
            grid, 0, "论文库数量")
        self.label_stat_analyzed = self._stat_cell(
            grid, 1, "AI 分析数量")
        self.label_stat_reports = self._stat_cell(
            grid, 2, "报告数量")
        layout.addLayout(grid)
        return card

    def _stat_cell(self, grid: QGridLayout, col: int, caption: str) -> QLabel:
        value = QLabel("0", self)
        value.setStyleSheet(_STAT_VALUE_QSS)
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(value, 0, col)
        label = QLabel(caption, self)
        label.setStyleSheet(_STAT_LABEL_QSS)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(label, 1, col)
        grid.setColumnStretch(col, 1)
        return value

    def _build_recent_card(self) -> QFrame:
        card, layout = self._make_card("最近活动")

        papers_title = QLabel("最近收藏论文", card)
        papers_title.setStyleSheet(_CARD_TITLE_QSS)
        layout.addWidget(papers_title)
        self.list_recent_papers = QListWidget(card)
        self.list_recent_papers.setMaximumHeight(110)
        layout.addWidget(self.list_recent_papers)

        reports_title = QLabel("最近生成报告", card)
        reports_title.setStyleSheet(_CARD_TITLE_QSS)
        layout.addWidget(reports_title)
        self.list_recent_reports = QListWidget(card)
        self.list_recent_reports.setMaximumHeight(110)
        layout.addWidget(self.list_recent_reports)
        return card

    def _build_quick_actions(self) -> QFrame:
        card, layout = self._make_card("快捷入口")
        row = QHBoxLayout()
        row.setSpacing(10)

        self.btn_search = QPushButton("搜索论文", card)
        self.btn_search.setObjectName("btnSecondary")
        self.btn_search.setMinimumHeight(34)
        self.btn_search.clicked.connect(self.search_requested.emit)
        row.addWidget(self.btn_search)

        self.btn_library = QPushButton("打开论文库", card)
        self.btn_library.setObjectName("btnSecondary")
        self.btn_library.setMinimumHeight(34)
        self.btn_library.clicked.connect(self.library_requested.emit)
        row.addWidget(self.btn_library)

        self.btn_report = QPushButton("生成报告", card)
        self.btn_report.setObjectName("btnSecondary")
        self.btn_report.setMinimumHeight(34)
        self.btn_report.clicked.connect(self.report_requested.emit)
        row.addWidget(self.btn_report)
        row.addStretch(1)
        layout.addLayout(row)
        return card

    @staticmethod
    def _make_card(title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("dashCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        heading = QLabel(title, card)
        heading.setStyleSheet(_CARD_TITLE_QSS)
        layout.addWidget(heading)
        return card, layout

    # ---------- 数据加载 ----------

    def refresh(self) -> None:
        """重新读取数据并渲染（只读）。"""
        snapshot = self._demo_snapshot() if self._demo_mode \
            else self._live_snapshot()
        self._snapshot_data = snapshot
        self._render(snapshot)

    def _demo_snapshot(self) -> dict:
        return DemoDataProvider.snapshot()

    def _live_snapshot(self) -> dict:
        """从真实 store 读取（全部只读；任何异常安全降级为空数据）。"""
        profile: dict = {}
        stats = {"paper_count": 0, "analyzed_count": 0, "report_count": 0}
        recent_papers: list[dict] = []
        recent_reports: list[dict] = []

        try:
            library = self._library_store or self._default_library_store()
            lib_stats = library.stats()
            stats["paper_count"] = int(lib_stats.get("total", 0))
            stats["analyzed_count"] = int(lib_stats.get("analyzed", 0))
            records = library.load_all()
            records = sorted(
                records, key=lambda r: getattr(r, "saved_at", "") or "",
                reverse=True)
            recent_papers = [
                {
                    "title": getattr(r, "title", "") or "",
                    "authors": list(getattr(r, "authors", []) or []),
                    "year": getattr(r, "year", "") or "",
                    "source": getattr(r, "source", "") or "",
                    "saved_at": getattr(r, "saved_at", "") or "",
                }
                for r in records[:_RECENT_LIMIT]
            ]
        except Exception:  # noqa: BLE001 - 首页不因数据异常而崩溃
            pass

        try:
            reports = self._report_store or self._default_report_store()
            summaries = reports.list_summaries()
            stats["report_count"] = len(summaries)
            recent_reports = [
                {
                    "title": s.get("title", "") or "",
                    "created_time": s.get("created_time", "") or "",
                    "paper_count": int(s.get("paper_count", 0) or 0),
                }
                for s in summaries[:_RECENT_LIMIT]
            ]
        except Exception:  # noqa: BLE001
            pass

        try:
            store = self._profile_store or self._default_profile_store()
            profile_obj = store.load()
            profile = {
                "research_area": getattr(profile_obj, "research_area", "") or "",
                "research_stage": getattr(
                    profile_obj, "research_stage", "") or "",
            }
        except Exception:  # noqa: BLE001
            pass

        return {
            "profile": profile,
            "stats": stats,
            "recent_papers": recent_papers,
            "recent_reports": recent_reports,
        }

    @staticmethod
    def _default_library_store():
        from tju_info_retrieval.services.library_store import LibraryStore
        return LibraryStore()

    @staticmethod
    def _default_report_store():
        from tju_info_retrieval.services.research_report_store import (
            ResearchReportStore,
        )
        return ResearchReportStore()

    @staticmethod
    def _default_profile_store():
        from tju_info_retrieval.services.research_profile_store import (
            ResearchProfileStore,
        )
        return ResearchProfileStore()

    # ---------- 渲染 ----------

    def _render(self, snapshot: dict) -> None:
        profile = snapshot.get("profile") or {}
        area = str(profile.get("research_area") or "").strip()
        stage_key = str(profile.get("research_stage") or "").strip()
        stage = RESEARCH_STAGE_LABELS.get(stage_key, stage_key)

        if area:
            self.label_profile_area.setText(f"研究方向：{area}")
        else:
            self.label_profile_area.setText(
                "研究方向：尚未设置（可在「设置 → 科研画像」中填写）")
        if stage:
            self.label_profile_stage.setText(f"研究阶段：{stage}")
        else:
            self.label_profile_stage.setText("研究阶段：尚未设置")

        stats = snapshot.get("stats") or {}
        self.label_stat_papers.setText(str(stats.get("paper_count", 0)))
        self.label_stat_analyzed.setText(str(stats.get("analyzed_count", 0)))
        self.label_stat_reports.setText(str(stats.get("report_count", 0)))

        self._fill_list(
            self.list_recent_papers, snapshot.get("recent_papers") or [],
            empty_text="暂无收藏论文（在检索结果中点击「收藏当前论文」）")
        self._fill_list(
            self.list_recent_reports, snapshot.get("recent_reports") or [],
            empty_text="暂无调研报告（在论文库中勾选论文后生成）")

    @staticmethod
    def _fill_list(widget: QListWidget, items: list[dict],
                   empty_text: str) -> None:
        widget.clear()
        if not items:
            widget.addItem(empty_text)
            return
        for item in items[:_RECENT_LIMIT]:
            title = str(item.get("title") or "（无标题）")
            tail = ""
            if "year" in item or "source" in item:
                bits = [b for b in (str(item.get("year") or ""),
                                    str(item.get("source") or "")) if b]
                tail = " · ".join(bits)
            elif "created_time" in item:
                created = str(item.get("created_time") or "")[:10]
                count = item.get("paper_count")
                bits = [b for b in (created,
                                    f"{count} 篇" if count else "") if b]
                tail = " · ".join(bits)
            widget.addItem(f"{title}    {tail}".rstrip())


class DashboardDialog(QDialog):
    """首页独立窗口（应用主题 QSS；快捷入口信号向外转发）。"""

    search_requested = Signal()
    library_requested = Signal()
    report_requested = Signal()

    def __init__(
        self,
        library_store=None,
        report_store=None,
        profile_store=None,
        demo_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"首页 · {PRODUCT_NAME}")
        self.resize(900, 640)
        self.setMinimumSize(620, 460)
        self.setStyleSheet(theme.THEME_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)

        self.widget = DashboardWidget(
            library_store=library_store,
            report_store=report_store,
            profile_store=profile_store,
            demo_mode=demo_mode,
            parent=self,
        )
        layout.addWidget(self.widget, 1)

        # 快捷入口：先关闭本窗口，再向外转发（避免模态/焦点滞留）
        self.widget.search_requested.connect(
            lambda: self._forward(self.search_requested))
        self.widget.library_requested.connect(
            lambda: self._forward(self.library_requested))
        self.widget.report_requested.connect(
            lambda: self._forward(self.report_requested))

    @property
    def demo_mode(self) -> bool:
        return self.widget.demo_mode

    def stat_values(self) -> dict:
        return self.widget.stat_values()

    def _forward(self, signal) -> None:
        self.accept()
        signal.emit()


__all__ = ["DashboardWidget", "DashboardDialog"]
