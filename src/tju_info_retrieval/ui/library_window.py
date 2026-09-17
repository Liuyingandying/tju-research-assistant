"""我的论文库窗口（v0.18 Phase 2.9-B）。

从"收藏列表"升级为"个人科研阅读管理库"：

- 上方筛选：标题搜索 / 文献类型 / 方向匹配 / 阅读优先级 / 阅读状态 / 标签 / 排序；
- 中部表格：标题 作者 年份 文献类型 方向匹配 阅读优先级 阅读状态 标签；
- 下方详情 Tabs：[原始信息] [基础整理] [AI增强分析] [我的记录]；
- 分析过期提示（旧科研画像 / 旧 Prompt）与用户主动触发的[重新分析]；
- 只读展示既有分析；重新分析必须用户点击（禁止自动批量重算）。

布局采用"表格 + 详情 Tabs"（非三栏），保证 920×560 小屏可用：
表格与详情放入垂直 QSplitter，用户可自行分配高度。
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tju_info_retrieval.models.enhanced_summary import (
    DOCUMENT_TYPE_LABELS,
    FIELD_LABELS_BY_TYPE,
    READING_PRIORITY_LABELS,
    READING_PRIORITY_ORDER,
)
from tju_info_retrieval.models.library import (
    READING_STATUS_LABELS,
    READING_STATUSES,
    STATUS_UNREAD,
    LibraryRecord,
    normalize_tags,
)
from tju_info_retrieval.models.research_profile import DIRECTION_MATCH_LABELS
from tju_info_retrieval.models.summary import SUMMARY_FIELD_LABELS
from tju_info_retrieval.services.library_store import LibraryStore

TABLE_COLUMNS = ["标题", "作者", "年份", "文献类型", "方向匹配",
                 "阅读优先级", "阅读状态", "标签", "选择"]
COL_TITLE, COL_AUTHORS, COL_YEAR = 0, 1, 2
COL_DOC_TYPE, COL_MATCH, COL_PRIORITY, COL_STATUS, COL_TAGS = 3, 4, 5, 6, 7
COL_CHECK = 8   # 报告多选 checkbox（表尾，避免数据列索引漂移）

_MATCH_ORDER = {"strong": 5, "medium": 4, "weak": 3, "none": 2, "unknown": 1, "": 0}

# 报告数量约束（与 ReportService 保持一致）
REPORT_MIN_PAPERS_SOFT = 5
REPORT_MAX_PAPERS = 20


class _block_item_changed:
    """临时屏蔽 itemChanged（程序性勾选不触发规则校验）。"""

    def __init__(self, table) -> None:
        self._table = table

    def __enter__(self):
        try:
            self._table.blockSignals(True)
        except RuntimeError:  # pragma: no cover - 已销毁
            pass
        return self

    def __exit__(self, *exc):
        try:
            self._table.blockSignals(False)
        except RuntimeError:  # pragma: no cover
            pass
        return False

SORT_OPTIONS = [
    ("收藏时间（新在前）", "saved_desc"),
    ("年份（新在前）", "year_desc"),
    ("方向匹配（强在前）", "match_desc"),
    ("阅读优先级（高在前）", "priority_desc"),
]


class LibraryWindow(QDialog):
    """我的论文库：查看 / 筛选 / 管理收藏论文及其分析结果。"""

    open_detail_requested = Signal(str)
    # 用户主动触发的重新分析（携带 LibraryRecord）
    reanalyze_basic_requested = Signal(object)
    reanalyze_enhanced_requested = Signal(object)
    # 生成调研报告（携带所选论文 id 列表；只发 id，不直接调 LLM）
    generate_report_requested = Signal(list)

    def __init__(
        self,
        library_store: LibraryStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = library_store
        self._records: list[LibraryRecord] = []
        self._filtered: list[LibraryRecord] = []
        self._profile_version = ""
        self._ai_configured = False
        self._checked_ids: list[str] = []   # 报告多选（checkbox 行）
        self._setup_ui()
        self.refresh_records()

    # ---------- 外部状态注入 ----------

    def set_profile_version(self, version: str) -> None:
        """当前科研画像版本（空画像 → ""；用于过期判断）。"""
        self._profile_version = str(version or "")

    def set_ai_configured(self, configured: bool) -> None:
        self._ai_configured = bool(configured)

    # ---------- UI ----------

    def _setup_ui(self) -> None:
        self.setWindowTitle("我的论文库")
        self.resize(980, 620)
        # 920×560 小屏可用：只约束到合理下限，不阻止窗口缩小
        self.setMinimumSize(640, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        # 标题 + 本地统计（不调用 AI）
        title_box = QHBoxLayout()
        title_label = QLabel("我的论文库", self)
        title_label.setObjectName("cardSectionTitle")
        title_box.addWidget(title_label)
        self.label_stats = QLabel("", self)
        self.label_stats.setObjectName("summaryMeta")
        title_box.addSpacing(12)
        title_box.addWidget(self.label_stats)
        title_box.addStretch(1)
        root.addLayout(title_box)

        root.addWidget(self._build_filter_bar())

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self._build_table())
        splitter.addWidget(self._build_detail_tabs())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        root.addWidget(splitter, 1)

        root.addLayout(self._build_action_bar())

    def _build_filter_bar(self) -> QWidget:
        bar = QWidget(self)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.input_search = QLineEdit(bar)
        self.input_search.setPlaceholderText("搜索标题")
        self.input_search.setClearButtonEnabled(True)
        self.input_search.textChanged.connect(self._apply_filters)
        layout.addWidget(self.input_search, 2)

        self.combo_doc_type = self._make_filter_combo(
            bar, "文献类型", [("全部", "")] + [
                (label, key) for key, label in DOCUMENT_TYPE_LABELS.items()])
        layout.addWidget(self.combo_doc_type, 1)

        self.combo_match = self._make_filter_combo(
            bar, "方向匹配", [("全部", "")] + [
                (label, key) for key, label in DIRECTION_MATCH_LABELS.items()])
        layout.addWidget(self.combo_match, 1)

        self.combo_priority = self._make_filter_combo(
            bar, "阅读优先级", [("全部", "")] + [
                (READING_PRIORITY_LABELS[k], k)
                for k in ("deep_read", "priority_read", "skim", "defer")]
            + [("未分析", "__none__")])
        layout.addWidget(self.combo_priority, 1)

        self.combo_status = self._make_filter_combo(
            bar, "阅读状态", [("全部", "")] + [
                (READING_STATUS_LABELS[s], s) for s in READING_STATUSES])
        layout.addWidget(self.combo_status, 1)

        self.input_tag_filter = QLineEdit(bar)
        self.input_tag_filter.setPlaceholderText("标签筛选")
        self.input_tag_filter.setClearButtonEnabled(True)
        self.input_tag_filter.textChanged.connect(self._apply_filters)
        layout.addWidget(self.input_tag_filter, 1)

        self.combo_sort = QComboBox(bar)
        for label, key in SORT_OPTIONS:
            self.combo_sort.addItem(label, key)
        self.combo_sort.currentIndexChanged.connect(self._apply_filters)
        layout.addWidget(self.combo_sort, 1)

        # 报告多选：计数 + 全选/清空
        self.label_selected = QLabel("已选 0 篇", bar)
        self.label_selected.setObjectName("summaryMeta")
        layout.addWidget(self.label_selected)

        self.btn_select_all = QPushButton("全选筛选(≤20)", bar)
        self.btn_select_all.setObjectName("btnGhost")
        self.btn_select_all.setMinimumHeight(28)
        self.btn_select_all.clicked.connect(self._on_select_all)
        layout.addWidget(self.btn_select_all)

        self.btn_clear_selection = QPushButton("清空选择", bar)
        self.btn_clear_selection.setObjectName("btnGhost")
        self.btn_clear_selection.setMinimumHeight(28)
        self.btn_clear_selection.clicked.connect(self._on_clear_selection)
        layout.addWidget(self.btn_clear_selection)
        return bar

    def _make_filter_combo(self, parent, placeholder: str, items) -> QComboBox:
        combo = QComboBox(parent)
        combo.setToolTip(placeholder)
        for label, value in items:
            combo.addItem(f"{placeholder}：{label}" if value else placeholder,
                          value)
        combo.currentIndexChanged.connect(self._apply_filters)
        return combo

    def _build_table(self) -> QWidget:
        self.table = QTableWidget(0, len(TABLE_COLUMNS), self)
        self.table.setObjectName("libraryTable")
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_TITLE, QHeaderView.Stretch)
        for col in range(1, len(TABLE_COLUMNS)):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemChanged.connect(self._on_item_changed)
        return self.table

    def _build_detail_tabs(self) -> QWidget:
        self.detail_tabs = QTabWidget(self)
        self.detail_tabs.addTab(self._build_original_tab(), "原始信息")
        self.detail_tabs.addTab(self._build_basic_tab(), "基础整理")
        self.detail_tabs.addTab(self._build_ai_tab(), "AI增强分析")
        self.detail_tabs.addTab(self._build_user_tab(), "我的记录")
        return self.detail_tabs

    @staticmethod
    def _scrollable(parent: QWidget, layout: QVBoxLayout) -> QWidget:
        from PySide6.QtWidgets import QScrollArea

        inner = QWidget(parent)
        inner.setLayout(layout)
        area = QScrollArea(parent)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(inner)
        return area

    def _build_original_tab(self) -> QWidget:
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        self.label_original = QLabel("请选择一条记录。", self)
        self.label_original.setWordWrap(True)
        self.label_original.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.label_original)
        layout.addStretch(1)
        return self._scrollable(self, layout)

    def _build_basic_tab(self) -> QWidget:
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        self.basic_labels: dict[str, QLabel] = {}
        for key in ("research_content", "core_technology",
                    "main_results", "application_value"):
            head = QLabel(SUMMARY_FIELD_LABELS[key], self)
            head.setObjectName("summaryFieldLabel")
            layout.addWidget(head)
            value = QLabel("—", self)
            value.setWordWrap(True)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setObjectName("summaryFieldValue")
            layout.addWidget(value)
            self.basic_labels[key] = value
        self.label_basic_basis = QLabel("", self)
        self.label_basic_basis.setObjectName("summaryMeta")
        self.label_basic_basis.setWordWrap(True)
        layout.addWidget(self.label_basic_basis)
        layout.addStretch(1)
        return self._scrollable(self, layout)

    def _build_ai_tab(self) -> QWidget:
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        self.banner_ai = QLabel("", self)
        self.banner_ai.setObjectName("summaryMeta")
        self.banner_ai.setWordWrap(True)
        layout.addWidget(self.banner_ai)

        self.ai_meta = QLabel("", self)
        self.ai_meta.setObjectName("summaryMeta")
        self.ai_meta.setWordWrap(True)
        layout.addWidget(self.ai_meta)

        self.ai_labels: dict[str, QLabel] = {}
        self.ai_heads: dict[str, QLabel] = {}
        for key in ("research_background", "technical_route",
                    "innovation_points", "relation_to_user_direction",
                    "reading_recommendation", "limitations"):
            head = QLabel("", self)
            head.setObjectName("summaryFieldLabel")
            layout.addWidget(head)
            value = QLabel("—", self)
            value.setWordWrap(True)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setObjectName("summaryFieldValue")
            layout.addWidget(value)
            self.ai_heads[key] = head
            self.ai_labels[key] = value
        layout.addStretch(1)
        return self._scrollable(self, layout)

    def _build_user_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        layout.addWidget(QLabel("标签（逗号分隔，最多 20 个）", page))
        self.input_tags = QLineEdit(page)
        self.input_tags.setPlaceholderText("例如：THz-ISAC, 信道, 待组会")
        layout.addWidget(self.input_tags)

        layout.addWidget(QLabel("阅读状态", page))
        self.combo_user_status = QComboBox(page)
        for status in READING_STATUSES:
            self.combo_user_status.addItem(
                READING_STATUS_LABELS[status], status)
        layout.addWidget(self.combo_user_status)

        layout.addWidget(QLabel("我的笔记", page))
        self.text_note = QTextEdit(page)
        self.text_note.setPlaceholderText(
            "例如：周五组会重点看图3；这个模型可能用于沙尘 THz channel")
        self.text_note.setMinimumHeight(70)
        layout.addWidget(self.text_note, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_save_record = QPushButton("保存", page)
        self.btn_save_record.setObjectName("btnSearchPrimary")
        self.btn_save_record.clicked.connect(self._on_save_user_fields)
        row.addWidget(self.btn_save_record)
        layout.addLayout(row)
        return page

    def _build_action_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.btn_open = QPushButton("打开全文页面", self)
        self.btn_refresh = QPushButton("刷新", self)
        self.btn_edit_tags = QPushButton("编辑标签", self)
        self.btn_change_status = QPushButton("修改状态", self)
        self.btn_reanalyze_basic = QPushButton("重新基础整理", self)
        self.btn_reanalyze_ai = QPushButton("重新AI分析", self)
        self.btn_generate_report = QPushButton("生成调研报告", self)
        self.btn_delete = QPushButton("删除记录", self)

        actions = (
            (self.btn_open, self._on_open_selected, None),
            (self.btn_edit_tags, self._on_edit_tags, None),
            (self.btn_change_status, self._on_change_status, None),
            (self.btn_reanalyze_basic, self._on_reanalyze_basic, None),
            (self.btn_reanalyze_ai, self._on_reanalyze_ai, None),
            (self.btn_generate_report, self._on_generate_report,
             "btnSearchPrimary"),
            (self.btn_delete, self._on_delete_record, None),
            (self.btn_refresh, self.refresh_records, None),
        )
        for button, slot, object_name in actions:
            button.setObjectName(object_name or "btnGhost")
            button.setMinimumHeight(32)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(slot)
            bar.addWidget(button)
        bar.addStretch(1)
        return bar

    # ---------- 数据 ----------

    def refresh_records(self) -> None:
        """从 LibraryStore 重新加载并重绘（保持当前选中行）。"""
        current_id = self._selected_id()
        self._records = self._store.load_all()
        self._apply_filters()
        if current_id:
            self._select_by_id(current_id)

    def _apply_filters(self) -> None:
        # 视图变化 → 重置报告勾选（勾选作用于当前筛选结果）
        self._checked_ids = []
        keyword = self.input_search.text().strip().lower()
        doc_type = self.combo_doc_type.currentData() or ""
        match = self.combo_match.currentData() or ""
        priority = self.combo_priority.currentData()
        status = self.combo_status.currentData() or ""
        tag_keyword = self.input_tag_filter.text().strip().lower()

        def keep(rec: LibraryRecord) -> bool:
            if keyword and keyword not in (rec.title or "").lower():
                return False
            if doc_type and rec.document_type != doc_type:
                return False
            if match and rec.direction_match_level != match:
                return False
            if priority == "__none__":
                if rec.reading_priority:
                    return False
            elif priority and rec.reading_priority != priority:
                return False
            if status and rec.reading_status != status:
                return False
            if tag_keyword:
                if not any(tag_keyword in t.lower() for t in rec.tags):
                    return False
            return True

        self._filtered = [r for r in self._records if keep(r)]
        self._sort_records()
        self._populate_table()
        self._update_stats()

    def _sort_records(self) -> None:
        mode = self.combo_sort.currentData() or "saved_desc"
        if mode == "year_desc":
            self._filtered.sort(
                key=lambda r: str(r.year or ""), reverse=True)
        elif mode == "match_desc":
            self._filtered.sort(
                key=lambda r: _MATCH_ORDER.get(r.direction_match_level, 0),
                reverse=True)
        elif mode == "priority_desc":
            self._filtered.sort(
                key=lambda r: READING_PRIORITY_ORDER.get(r.reading_priority, 0),
                reverse=True)
        else:
            self._filtered.sort(key=lambda r: r.saved_at or "", reverse=True)

    def _update_stats(self) -> None:
        """本地统计（顶部信息行；不调用 AI）。"""
        stats = self._store.stats()
        self.label_stats.setText(
            f"共 {stats['total']} 篇 ｜ 强相关 {stats['strong']} ｜ "
            f"精读/重点 {stats['priority']} ｜ 已完成 {stats['finished']}"
            + (f" ｜ 当前筛选 {len(self._filtered)} 篇"
               if len(self._filtered) != stats["total"] else ""))

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._filtered))
        for i, rec in enumerate(self._filtered):
            self._set_cell(i, COL_TITLE, rec.title or "")
            self._set_cell(i, COL_AUTHORS, "；".join(rec.authors or []))
            self._set_cell(i, COL_YEAR, str(rec.year or "—"))
            self._set_cell(i, COL_DOC_TYPE, DOCUMENT_TYPE_LABELS.get(
                rec.document_type, "未分析" if not rec.has_enhanced_summary()
                else rec.document_type or "—"))
            self._set_cell(i, COL_MATCH, DIRECTION_MATCH_LABELS.get(
                rec.direction_match_level,
                "未分析" if not rec.has_enhanced_summary() else "—"))
            self._set_cell(i, COL_PRIORITY, READING_PRIORITY_LABELS.get(
                rec.reading_priority,
                "未分析" if not rec.has_enhanced_summary() else "未标注"))
            self._set_cell(i, COL_STATUS, READING_STATUS_LABELS.get(
                rec.reading_status, "未读"))
            self._set_cell(i, COL_TAGS, ",".join(rec.tags or []))
            self.table.item(i, 0).setData(Qt.ItemDataRole.UserRole, rec.id)
            self._set_check_item(i, rec.id)

    def _set_cell(self, row: int, col: int, text: str) -> None:
        self.table.setItem(row, col, QTableWidgetItem(text))

    def _set_check_item(self, row: int, record_id: str) -> None:
        """在“选择”列放置 checkbox 项（未勾选；勾选状态由 checked_ids 维护）。"""
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, record_id)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, COL_CHECK, item)

    def _selected_id(self) -> str:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._filtered):
            return ""
        return self._filtered[row].id

    def _select_by_id(self, record_id: str) -> None:
        for row, rec in enumerate(self._filtered):
            if rec.id == record_id:
                self.table.selectRow(row)
                return
        self._clear_detail()

    def _selected_record(self) -> LibraryRecord | None:
        record_id = self._selected_id()
        if not record_id:
            return None
        return next((r for r in self._filtered if r.id == record_id), None)

    # ---------- 详情渲染 ----------

    def _on_selection_changed(self) -> None:
        record = self._selected_record()
        if record is None:
            self._clear_detail()
            return
        self._render_original(record)
        self._render_basic(record)
        self._render_ai(record)
        self._render_user(record)

    # ---------- 报告多选（Phase 2.9-C-E） ----------

    def _on_item_changed(self, item) -> None:
        """checkbox 勾选/取消 → 维护 checked_ids + 更新 UI。"""
        if item is None or item.column() != COL_CHECK:
            return
        record_id = item.data(Qt.ItemDataRole.UserRole)
        if not record_id:
            return
        if item.checkState() == Qt.CheckState.Checked:
            if len(self._checked_ids) >= REPORT_MAX_PAPERS:
                # 超上限：禁止勾选并回退
                with _block_item_changed(self.table):
                    item.setCheckState(Qt.CheckState.Unchecked)
                QMessageBox.warning(
                    self, "提示",
                    f"最多选择 {REPORT_MAX_PAPERS} 篇论文生成报告，请先减少选择。")
                return
            if record_id not in self._checked_ids:
                self._checked_ids.append(record_id)
        else:
            if record_id in self._checked_ids:
                self._checked_ids.remove(record_id)
        self._update_selection_ui()

    def _update_selection_ui(self) -> None:
        count = len(self._checked_ids)
        self.label_selected.setText(f"已选 {count} 篇")
        self.btn_generate_report.setEnabled(1 <= count <= REPORT_MAX_PAPERS)

    def _on_select_all(self) -> None:
        """全选当前筛选行（最多 20 篇）。"""
        self._on_clear_selection()
        for row in range(self.table.rowCount()):
            if len(self._checked_ids) >= REPORT_MAX_PAPERS:
                break
            item = self.table.item(row, COL_CHECK)
            if item is not None:
                with _block_item_changed(self.table):
                    item.setCheckState(Qt.CheckState.Checked)
                record_id = item.data(Qt.ItemDataRole.UserRole)
                if record_id and record_id not in self._checked_ids:
                    self._checked_ids.append(record_id)
        self._update_selection_ui()

    def _on_clear_selection(self) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_CHECK)
            if item is not None:
                with _block_item_changed(self.table):
                    item.setCheckState(Qt.CheckState.Unchecked)
        self._checked_ids = []
        self._update_selection_ui()

    def checked_paper_ids(self) -> list[str]:
        """当前勾选的论文 id（按勾选顺序；供生成报告使用）。"""
        return list(self._checked_ids)

    def _on_generate_report(self) -> None:
        """用户点击“生成调研报告”：校验数量 → 发出请求（不直接调 LLM）。"""
        ids = self.checked_paper_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先勾选要纳入报告的论文。")
            return
        if len(ids) < REPORT_MIN_PAPERS_SOFT:
            answer = QMessageBox.question(
                self, "确认",
                f"当前仅勾选 {len(ids)} 篇，调研报告可能不完整，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.generate_report_requested.emit(ids)

    def _clear_detail(self) -> None:
        self.label_original.setText("请选择一条记录。")
        for label in self.basic_labels.values():
            label.setText("—")
        self.label_basic_basis.setText("")
        self.banner_ai.setText("")
        self.ai_meta.setText("")
        for head in self.ai_heads.values():
            head.setText("")
        for label in self.ai_labels.values():
            label.setText("—")
        self.input_tags.clear()
        self.text_note.clear()

    def _render_original(self, rec: LibraryRecord) -> None:
        lines = [
            f"标题：{rec.title or '（无标题）'}",
            f"作者：{'；'.join(rec.authors) or '—'}",
            f"来源：{rec.source or '—'}",
            f"数据库：{rec.database or '—'}",
            f"年份：{rec.year or '—'}",
            f"成果类型：{rec.artifact_type or 'paper'}",
        ]
        if rec.doi:
            lines.append(f"DOI：{rec.doi}")
        if rec.detail_url:
            lines.append(f"链接：{rec.detail_url}")
        lines.append("")
        lines.append("摘要：")
        lines.append((rec.abstract or "（无摘要）").strip())
        self.label_original.setText("\n".join(lines))

    def _render_basic(self, rec: LibraryRecord) -> None:
        data = rec.basic_summary or {}
        for key, label in self.basic_labels.items():
            label.setText(str(data.get(key) or "—") or "—")
        basis = data.get("source_basis") or {}
        if basis:
            text = "；".join(f"{SUMMARY_FIELD_LABELS.get(k, k)}←{v}"
                            for k, v in basis.items())
            self.label_basic_basis.setText(f"证据来源：{text}")
        else:
            self.label_basic_basis.setText(
                "" if data else "尚未进行基础整理。")

    def _render_ai(self, rec: LibraryRecord) -> None:
        if not rec.has_enhanced_summary():
            self.banner_ai.setText("")
            self.ai_meta.setText("尚未进行 AI 增强分析（可在主窗口分析后收藏，"
                                 "或点击下方「重新AI分析」）。")
            for label in self.ai_labels.values():
                label.setText("—")
            for key, head in self.ai_heads.items():
                head.setText(self._ai_head_text(key, ""))
            return

        data = rec.enhanced_summary or {}
        doc_type = rec.document_type
        for key, head in self.ai_heads.items():
            head.setText(self._ai_head_text(key, doc_type))
        for key, label in self.ai_labels.items():
            label.setText(str(data.get(key) or "—") or "—")

        meta_parts = []
        if doc_type:
            meta_parts.append(
                f"文献类型：{DOCUMENT_TYPE_LABELS.get(doc_type, doc_type)}")
        if rec.direction_match_level:
            meta_parts.append("方向匹配："
                              + DIRECTION_MATCH_LABELS.get(
                                  rec.direction_match_level,
                                  rec.direction_match_level))
        if rec.reading_priority:
            meta_parts.append("阅读优先级："
                              + READING_PRIORITY_LABELS.get(
                                  rec.reading_priority,
                                  rec.reading_priority))
        source = rec.analysis_source_label()
        if source:
            meta_parts.append(f"分析来源：{source}")
        if rec.enhanced_at:
            meta_parts.append(f"分析时间：{rec.enhanced_at[:19]}")
        self.ai_meta.setText(" ｜ ".join(meta_parts))

        # 过期提示（§十七/§十八/§三十六）：不自动重算，仅提示 + 重新分析
        banners = []
        if rec.is_profile_stale(self._profile_version):
            banners.append("该分析基于旧科研画像，建议重新分析。")
        if rec.is_prompt_stale(self._current_prompt_version()):
            banners.append("AI分析版本较旧，建议重新分析。")
        self.banner_ai.setText(" ｜ ".join(banners))
        if rec.document_type_reason:
            self.ai_meta.setText(
                self.ai_meta.text()
                + f"\n类型判断依据：{rec.document_type_reason}")

    @staticmethod
    def _ai_head_text(key: str, doc_type: str) -> str:
        from tju_info_retrieval.models.enhanced_summary import (
            ENHANCED_FIELD_LABELS,
        )

        return FIELD_LABELS_BY_TYPE.get(doc_type, {}).get(
            key, ENHANCED_FIELD_LABELS[key])

    @staticmethod
    def _current_prompt_version() -> str:
        from tju_info_retrieval.services.prompts.enhanced_summary_prompt import (
            ENHANCED_PROMPT_VERSION,
        )

        return ENHANCED_PROMPT_VERSION

    def _render_user(self, rec: LibraryRecord) -> None:
        self.input_tags.setText(", ".join(rec.tags or []))
        idx = self.combo_user_status.findData(rec.reading_status)
        self.combo_user_status.setCurrentIndex(idx if idx >= 0 else 0)
        self.text_note.setPlainText(rec.note or "")

    # ---------- 操作 ----------

    def _on_open_selected(self) -> None:
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "提示", "请先在表格中选择一条结果。")
            return
        if not record.detail_url:
            QMessageBox.information(
                self, "提示", "该记录没有可用的全文页面链接。")
            return
        self.open_detail_requested.emit(record.detail_url)

    def _on_edit_tags(self) -> None:
        """编辑标签（快速输入；标签归一与去重由模型层负责）。"""
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "提示", "请先在表格中选择一条结果。")
            return
        text, ok = QInputDialog.getText(
            self,
            "编辑标签",
            "标签（用逗号分隔，例如：THz,6G,重点阅读）：",
            text=",".join(record.tags),
        )
        if not ok:
            return
        parts = re.split(r"[,，、;；]+", text or "")
        self._store.update(record.id, tags=normalize_tags(parts))
        self.refresh_records()

    def _on_change_status(self) -> None:
        """修改阅读状态（用户实际阅读进度，与 AI 阅读优先级不同）。"""
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "提示", "请先在表格中选择一条结果。")
            return
        labels = [READING_STATUS_LABELS[s] for s in READING_STATUSES]
        current = READING_STATUS_LABELS.get(record.reading_status, labels[0])
        idx, ok = QInputDialog.getItem(
            self,
            "修改阅读状态",
            "请选择阅读状态：",
            labels,
            current=labels.index(current) if current in labels else 0,
            editable=False,
        )
        if not ok:
            return
        self._store.update(record.id, reading_status=READING_STATUSES[
            labels.index(idx)])
        self.refresh_records()

    def _on_save_user_fields(self) -> None:
        """保存标签 / 阅读状态 / 笔记（我的记录 Tab）。"""
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "提示", "请先在表格中选择一条结果。")
            return
        parts = re.split(r"[,，、;；]+", self.input_tags.text() or "")
        self._store.update(
            record.id,
            tags=normalize_tags(parts),
            reading_status=self.combo_user_status.currentData(),
            note=self.text_note.toPlainText(),
        )
        self.refresh_records()
        QMessageBox.information(self, "提示", "记录已保存。")

    def _on_reanalyze_basic(self) -> None:
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "提示", "请先在表格中选择一条结果。")
            return
        self.reanalyze_basic_requested.emit(record)

    def _on_reanalyze_ai(self) -> None:
        """重新 AI 分析：必须用户主动点击，交给主窗口 SummaryWorker。"""
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "提示", "请先在表格中选择一条结果。")
            return
        if not self._ai_configured:
            QMessageBox.information(
                self, "提示", "AI增强分析尚未配置，请先在设置中完成配置。")
            return
        self.reanalyze_enhanced_requested.emit(record)

    def _on_delete_record(self) -> None:
        """删除当前记录（二次确认；只删论文库记录）。"""
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "提示", "请先在表格中选择一条结果。")
            return
        answer = QMessageBox.question(
            self,
            "删除记录",
            f"确定要从论文库删除以下记录吗？\n\n{record.title}\n\n"
            "（只删除论文库记录，不影响其它数据）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._store.remove(record.id)
        self.refresh_records()
        self._clear_detail()
