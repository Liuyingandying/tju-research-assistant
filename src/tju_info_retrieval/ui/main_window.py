"""信息自动检索整理系统 · 主窗口。

v0.9 现代学术风呈现层：校徽 Header + 检索卡片 + 状态条 + 结果卡片 + 校训页脚。
样式统一由 ``ui/theme.py`` 的 QSS 与矢量图标承载；业务回调、线程模型、
信号契约与 widget 属性名保持不变（tests/test_gui.py 的公开表面）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tju_info_retrieval.models.query import (
    MAX_RESULT_COUNT,
    MIN_RESULT_COUNT,
    QueryRequest,
    RankingMode,
)
from tju_info_retrieval.models.ranking import RankingExplanation
from tju_info_retrieval.services import export as export_service
from tju_info_retrieval.services import report_generator
from tju_info_retrieval.services.artifact_routing import resolve_effective_sources
from tju_info_retrieval.services.expansion_direction import resolve_expansion_behavior
from tju_info_retrieval.browser.session import STORAGE_STATE_PATH
from tju_info_retrieval.ui.detail_manager import DetailManager
from tju_info_retrieval.ui.evidence_worker import PaperEvidenceWorker
from tju_info_retrieval.ui import theme
from tju_info_retrieval.services.offline_summary import summarize_offline
from tju_info_retrieval.services.summary_service import (
    MODE_BASIC,
    MODE_ENHANCED,
    SummaryService,
)
from tju_info_retrieval.ui.explanation_dialog import ExplanationDialog
from tju_info_retrieval.ui.report_worker import ReportWorker
from tju_info_retrieval.ui.research_report_dialog import ResearchReportDialog
from tju_info_retrieval.ui.summary_dialog import SummaryDialog
from tju_info_retrieval.ui.summary_worker import SummaryWorker
from tju_info_retrieval.ui.worker import BrowserWorker
from tju_info_retrieval.demo_data import load_demo_results
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services import test_report
from tju_info_retrieval.services.library_store import LibraryStore
from tju_info_retrieval.services.result_merger import ResultMerger
from tju_info_retrieval.services.ranking import RankingService
from tju_info_retrieval.sources.ieee import ieee_original_url
from tju_info_retrieval.version import APP_NAME, FEATURES, SUPPORTED_SOURCES, VERSION

RESULT_COLUMNS = ["标题", "作者", "来源", "年份", "类型", "数据库"]

_EXPANSION_OPTIONS = ["综合", "理论", "应用", "综述"]
# 语言筛选控制“检索结果语言范围”（非用户输入语言）：
# zh=中文文献（CNKI/万方）；en=英文文献（IEEE Xplore）；bilingual=全部。
# 严格匹配不支持 IEEE Xplore 来源（英文库作者单位元数据严重缺失，
# strict 会把无单位数据的论文整批误删），最终来源含 IEEE 时在 _on_search 提交前拦截。
_LANGUAGE_OPTIONS = ["中文", "英文", "中英双语"]
_LANGUAGE_MODES = {"中文": "zh", "英文": "en", "中英双语": "bilingual"}
_LANGUAGE_ALLOWED = {
    "zh": ("CNKI", "万方"),
    "en": ("IEEE Xplore",),
    "bilingual": ("CNKI", "万方", "IEEE Xplore"),
}
_LANGUAGE_TOOLTIPS = {
    "zh": "中文文献模式：仅检索 CNKI / 万方",
    "en": "英文文献模式：仅检索 IEEE Xplore（暂不支持严格匹配）",
    "bilingual": "中英双语模式：检索全部数据来源",
}


class _ResponsiveActionBar(QWidget):
    """底部操作按钮的自适应容器（v0.17 Phase 2.6-PKG-B Hotfix）。

    宽度足够时所有按钮排成一行（均分 stretch，保持原观感）；宽度不足时
    自动切成两行（5+5 按原顺序），保证任意 DPI/窗口宽度下按钮文字完整、
    不裁切、不溢出。用滞回阈值避免临界宽度下布局抖动。
    """

    _SPLIT_AT = 5        # 两行模式：第一行按钮数
    _HYSTERESIS = 8      # 进入/退出两行的滞回（像素）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: list[QPushButton] = []
        self._two_rows = False
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(4)
        self._row_a = QHBoxLayout()
        self._row_a.setSpacing(10)
        self._row_b = QHBoxLayout()
        self._row_b.setSpacing(10)
        self._box.addLayout(self._row_a)
        self._box.addLayout(self._row_b)

    def add_button(self, btn: QPushButton) -> None:
        self._buttons.append(btn)
        self._row_a.addWidget(btn, 1)

    def _required_single_width(self) -> int:
        """单行所需最小宽：基于每个按钮 minimumSizeHint（QPushButton 的
        sizeHint 含额外留白，用它判据会把可容纳的单行误判为两行；
        stretch=1 均分下布局会保证每个按钮 ≥ 其 minimumSizeHint）。"""
        if not self._buttons:
            return 0
        spacing = self._row_a.spacing()
        return (
            sum(btn.minimumSizeHint().width() for btn in self._buttons)
            + spacing * (len(self._buttons) - 1)
        )

    def _relayout(self, two_rows: bool) -> None:
        if two_rows == self._two_rows:
            return
        self._two_rows = two_rows
        # takeAt 摘除全部按钮（widget 保持可见，仅改变所在行布局），
        # 再按单/双行重新分配。不调用 hide()——由所属行布局的 empty 自然
        # 不占空间，避免隐藏/显示引发的重入与堆损坏风险。
        while self._row_a.count():
            self._row_a.takeAt(0)
        while self._row_b.count():
            self._row_b.takeAt(0)
        if two_rows:
            for btn in self._buttons[: self._SPLIT_AT]:
                self._row_a.addWidget(btn, 1)
            for btn in self._buttons[self._SPLIT_AT:]:
                self._row_b.addWidget(btn, 1)
        else:
            for btn in self._buttons:
                self._row_a.addWidget(btn, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().resizeEvent(event)
        required = self._required_single_width()
        width = self.width()
        if self._two_rows:
            # 两行 → 只有宽度足够充裕才回单行，避免临界抖动。
            if width >= required + self._HYSTERESIS:
                self._relayout(False)
        else:
            if width < required - self._HYSTERESIS:
                self._relayout(True)


class MainWindow(QMainWindow):
    """Standalone MVP 主界面。

    请求信号定义在主窗口（主线程），连接到 worker 的槽（worker 线程），
    Qt 自动以 QueuedConnection 跨线程投递，Playwright 操作不阻塞 GUI。
    """

    check_auth_requested = Signal()
    search_requested = Signal(object)
    open_detail_requested = Signal(str)
    open_wanfang_detail_requested = Signal(str)
    shutdown_requested = Signal()
    # v0.17 Phase 2.4-C-B：CNKI 论文单条 on-demand 摘要获取（后台 worker）
    evidence_requested = Signal(str, str)
    # v0.18 Phase 2.7-B/2.9-A：增强分析后台执行（result, direction, profile）
    enhanced_requested = Signal(object, str, object)
    # 科研调研报告（v0.18 Phase 2.9-C-E）：论文 id 列表 + 标题提示
    report_requested = Signal(list, str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("信息自动检索整理系统")
        # v0.17 Phase 2.6-PKG-B：默认尺寸随屏幕工作区自适应（Qt 标准 API，无新依赖）。
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        if avail is not None and avail.width() > 0 and avail.height() > 0:
            self.resize(
                min(1180, avail.width() - 40),
                min(800, avail.height() - 40),
            )
        else:
            self.resize(1180, 800)
        # v0.17 Phase 2.6-PKG-B Hotfix：最小尺寸须容纳 1366×768@125% 的
        # 逻辑工作区（约 1093×582）。旧 1100×656 在 125% 下必然横向/纵向
        # 溢出。宽度不再依赖 10 按钮单行全宽（自适应两行兜底）；高度
        # 对应固定内容的最小高（表格随之压缩但不为 0）。
        self.setMinimumSize(920, 560)
        self.setStyleSheet(theme.THEME_QSS)
        window_icon = theme.paint_icon("shield")
        window_icon.addPixmap(theme.emblem_pixmap(64))
        self.setWindowIcon(window_icon)

        self._results: list[dict] = []
        self._searching = False
        self._last_query_info: dict | None = None
        self._library_store = LibraryStore()
        # v0.18 Phase 2.9-B：本次会话内已完成的分析结果（内存缓存），
        # 收藏时"只保存已有分析"（不触发 API）由此读取。
        self._basic_summary_cache: dict[str, object] = {}
        self._enhanced_summary_cache: dict[str, object] = {}
        # 论文库"重新AI分析"目标记录 id（完成后回写该记录）
        self._pending_library_record_id: str | None = None
        self._library_window = None
        # v0.18 Phase 2.10-B：科研展示首页（Dashboard）
        self._dashboard_dialog = None
        # v0.18 Phase 2.9-C-E：调研报告
        self._report_busy = False
        self._report_service = None      # 懒构建（UI 不直接调用 LLM Provider）
        self._report_progress = None
        # v0.18 Phase 2.7-B：SummaryService 门面（basic=Offline 全兼容；
        # enhanced=OpenAI-compatible，默认配置关闭 → 未配置降级）。
        # v0.18 Phase 2.8-A：配置经 APIConfigManager（keyring 优先）；
        # 保存配置后刷新 SummaryService 的增强 Provider。
        from tju_info_retrieval.services.api_config_manager import (
            APIConfigManager,
        )

        self._api_config = APIConfigManager()
        # v0.18 Phase 2.9-A：科研画像（本机单用户，默认创建）
        from tju_info_retrieval.services.research_profile_store import (
            ResearchProfileStore,
        )

        self._profile_store = ResearchProfileStore()
        self._research_profile = self._profile_store.load()
        self._summary_service = SummaryService(
            config=self._api_config.provider_config())

        self._build_ui()
        self._build_menu()
        self._start_worker()
        self._connect_worker()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("central")
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 4, 12, 4)
        root.setSpacing(4)

        root.addWidget(self._build_header(central))
        root.addWidget(self._build_search_block(central), 0)
        root.addWidget(self._build_status_strip(central))
        root.addWidget(self._build_results_card(central), 1)
        root.addWidget(self._build_bottom_bar(central), 0)
        root.addWidget(self._build_footer(central))

        self.setCentralWidget(central)
        self.btn_favorite.setEnabled(False)
        self.btn_detail.setEnabled(False)
        self.btn_explanation.setEnabled(False)
        self.btn_summary.setEnabled(False)
        self.btn_ieee_original.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_report.setEnabled(False)
        self._apply_language_mode()

    # ---------- 菜单（v0.18 Phase 2.10-B：首页入口） ----------

    def _build_menu(self) -> None:
        """新增"开始"菜单承载首页入口（只增不改：不影响既有按钮布局）。"""
        from PySide6.QtGui import QAction

        menu = self.menuBar().addMenu("开始")

        self.action_dashboard = QAction("首页概览", self)
        self.action_dashboard.setStatusTip("查看科研画像、统计与最近活动")
        self.action_dashboard.triggered.connect(self._on_open_dashboard)
        menu.addAction(self.action_dashboard)

        self.action_dashboard_demo = QAction("演示模式（首页）", self)
        self.action_dashboard_demo.setStatusTip(
            "用只读演示数据展示完整链路（不写入任何数据）")
        self.action_dashboard_demo.triggered.connect(
            lambda: self._on_open_dashboard(demo_mode=True))
        menu.addAction(self.action_dashboard_demo)

    # ---------- 语言筛选 ----------
    @property
    def language_mode(self) -> str:
        """当前语言模式：zh / en / bilingual（默认 bilingual）。"""
        btn = self.lang_group.checkedButton()
        return _LANGUAGE_MODES[btn.text()] if btn is not None else "bilingual"

    def _apply_language_mode(self, *_args) -> None:
        """按语言模式启用/禁用数据来源复选框。

        只切换 Enabled、不改勾选状态：被禁用来源的既有勾选保留，
        切回允许的模式时自动恢复（GUI 层来源过滤见 _on_search）。
        """
        allowed = _LANGUAGE_ALLOWED[self.language_mode]
        for chk, name in (
            (self.chk_cnki, "CNKI"),
            (self.chk_wanfang, "万方"),
            (self.chk_ieee, "IEEE Xplore"),
        ):
            enabled = name in allowed
            chk.setEnabled(enabled)
            chk.setToolTip("" if enabled else "当前语言模式下不可用")

    def _build_header(self, central: QWidget) -> QFrame:
        """校徽 + 中英文标题的学术风 Header。"""
        header = QFrame(central)
        header.setObjectName("appHeader")
        # v0.17 Phase 2.6-PKG-B Hotfix：固定高 48（徽标 40+边距），小屏让垂直空间。
        header.setFixedHeight(48)
        box = QHBoxLayout(header)
        box.setContentsMargins(14, 2, 14, 2)
        box.setSpacing(10)

        emblem = QLabel(header)
        emblem.setPixmap(theme.emblem_pixmap(40))
        emblem.setToolTip("天津大学 · 实事求是")
        box.addWidget(emblem, 0, Qt.AlignmentFlag.AlignVCenter)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.label_app_title = QLabel(APP_NAME, header)
        self.label_app_title.setObjectName("appTitle")
        self.label_app_subtitle = QLabel(
            "Tianjin University Literature Retrieval Platform", header
        )
        self.label_app_subtitle.setObjectName("appSubtitle")
        titles.addWidget(self.label_app_title)
        titles.addWidget(self.label_app_subtitle)
        box.addLayout(titles)
        box.setAlignment(titles, Qt.AlignmentFlag.AlignVCenter)
        box.addStretch(1)
        return header

    def _form_label(self, text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("formLabel")
        return label

    def _build_search_block(self, central: QWidget) -> QWidget:
        """“检索条件”页签 + 检索卡片。"""
        block = QWidget(central)
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        tab = QFrame(block)
        tab.setObjectName("sectionTab")
        tab.setFixedHeight(26)
        tab_box = QHBoxLayout(tab)
        tab_box.setContentsMargins(12, 0, 12, 0)
        tab_box.setSpacing(8)
        tab_icon = QLabel(tab)
        tab_icon.setPixmap(theme.pixmap_painted("search", theme.COLORS["primary"], 16))
        tab_text = QLabel("检索条件", tab)
        tab_text.setObjectName("sectionTabText")
        tab_box.addWidget(tab_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        tab_box.addWidget(tab_text, 0, Qt.AlignmentFlag.AlignVCenter)
        tab_box.addStretch(1)
        layout.addWidget(tab, 0, Qt.AlignmentFlag.AlignLeft)

        card = QFrame(block)
        card.setObjectName("searchCard")
        grid = QGridLayout(card)
        # v0.17 Phase 2.6-PKG-B：垂直瘦身，让 1366×768 下结果表格恢复高度。
        # Hotfix：移除大 columnMinimumWidth——旧 240/200 强制整卡最小宽至
        # 1300+，1366@125% 逻辑宽 1093 下必然横向溢出。列比例仅由
        # stretch 决定（label 列不伸、输入列伸），窄逻辑宽下输入框自然变窄，
        # 绝不重叠/消失。
        grid.setContentsMargins(16, 4, 16, 6)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 5)
        grid.setColumnStretch(2, 0)
        grid.setColumnStretch(3, 3)
        grid.setColumnMinimumWidth(1, 0)
        grid.setColumnMinimumWidth(3, 0)

        # 行 1：研究方向 / 扩展方向
        grid.addWidget(self._form_label("研究方向", card), 0, 0)
        self.input_direction = QLineEdit(card)
        self.input_direction.setPlaceholderText("例如：太赫兹")
        grid.addWidget(self.input_direction, 0, 1)
        grid.addWidget(self._form_label("扩展方向", card), 0, 2)
        self.combo_expansion = QComboBox(card)
        self.combo_expansion.addItems(_EXPANSION_OPTIONS)
        self.combo_expansion.setToolTip(
            "扩展方向：理论/应用/综述会附加对应限定词（综合=不附加限定）。\n"
            "实际发送给 CNKI 的检索串会在状态栏显示。"
        )
        grid.addWidget(self.combo_expansion, 0, 3)

        # 行 2：人员姓名 / 人员单位（v0.13 开放输入，作为作者条件传递）
        grid.addWidget(self._form_label("人员姓名", card), 1, 0)
        self.input_author_name = QLineEdit(card)
        self.input_author_name.setPlaceholderText("作者姓名（可选）")
        grid.addWidget(self.input_author_name, 1, 1)
        grid.addWidget(self._form_label("人员单位", card), 1, 2)
        affil_box = QWidget(card)
        affil_layout = QHBoxLayout(affil_box)
        affil_layout.setContentsMargins(0, 0, 0, 0)
        affil_layout.setSpacing(6)
        self.input_author_affiliation = QLineEdit(affil_box)
        self.input_author_affiliation.setPlaceholderText("作者单位（可选）")
        # v0.17 Phase 2.6-PKG-B Hotfix：输入框与模式下拉水平并排、由 layout
        # 自然决定高度，只给最小高与纵向策略——100/125/150% DPI 下不与
        # 模式下拉重叠，也不固定整行高度。
        self.input_author_affiliation.setMinimumHeight(30)
        self.input_author_affiliation.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        affil_layout.addWidget(self.input_author_affiliation, 1)
        # v0.13 Phase 6：单位筛选模式（soft=智能匹配 / strict=严格匹配）
        self.combo_affiliation_mode = QComboBox(affil_box)
        self.combo_affiliation_mode.addItem("智能匹配", "soft")
        self.combo_affiliation_mode.addItem("严格匹配", "strict")
        self.combo_affiliation_mode.setMinimumHeight(30)
        self.combo_affiliation_mode.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )
        self.combo_affiliation_mode.setToolTip(
            "智能匹配：有单位数据时按单位过滤，无单位数据时保留。\n"
            "严格匹配：只保留单位确认匹配的论文，无单位数据将被剔除。"
        )
        affil_layout.addWidget(self.combo_affiliation_mode, 0)
        grid.addWidget(affil_box, 1, 3)

        # 行 3：成果类型（v0.17 Phase 2.2-D；与数据库来源正交，默认仅论文——
        # 默认行为与旧版完全一致，不触碰来源/语言区）
        grid.addWidget(self._form_label("成果类型", card), 2, 0)
        artifact_widget = QWidget(card)
        artifact_layout = QHBoxLayout(artifact_widget)
        artifact_layout.setContentsMargins(0, 0, 0, 0)
        artifact_layout.setSpacing(10)
        self.chk_artifact_paper = QCheckBox("论文", artifact_widget)
        self.chk_artifact_paper.setObjectName("artifactCheck")
        self.chk_artifact_paper.setChecked(True)
        self.chk_artifact_paper.setToolTip(
            "论文：按所选数据库检索期刊/会议论文（CNKI / 万方 / IEEE Xplore）"
        )
        artifact_layout.addWidget(self.chk_artifact_paper)
        self.chk_artifact_patent = QCheckBox("专利", artifact_widget)
        self.chk_artifact_patent.setObjectName("artifactCheck")
        self.chk_artifact_patent.setToolTip(
            "专利：目前仅支持万方数据源（万方专利）；高引用排序主要依据论文被引信息，"
            "无被引数据的成果按现有综合规则降级排序"
        )
        artifact_layout.addWidget(self.chk_artifact_patent)
        # v0.17 Phase 2.3-C：新闻成果类型（来源固定为天津大学新闻网，不依赖
        # 数据库复选框；英文语言模式下不可执行，提交前有明确提示）
        self.chk_artifact_news = QCheckBox("新闻", artifact_widget)
        self.chk_artifact_news.setObjectName("artifactCheck")
        self.chk_artifact_news.setToolTip(
            "新闻：以天津大学新闻网为检索入口，覆盖天津大学科研新闻及站内转载的"
            "部分中央媒体报道（中文；英文模式下不可用）"
        )
        artifact_layout.addWidget(self.chk_artifact_news)
        self.label_news_source = QLabel("新闻来源：天津大学新闻网", artifact_widget)
        self.label_news_source.setObjectName("formHint")
        self.label_news_source.setVisible(False)
        artifact_layout.addWidget(self.label_news_source)
        self.chk_artifact_news.toggled.connect(self.label_news_source.setVisible)
        artifact_layout.addStretch(1)
        grid.addWidget(artifact_widget, 2, 1)

        # 行 4：数据来源（卡片式复选框）/ 语言筛选（下一阶段开放）
        grid.addWidget(self._form_label("数据来源", card), 3, 0)
        src_widget = QWidget(card)
        src_layout = QHBoxLayout(src_widget)
        src_layout.setContentsMargins(0, 0, 0, 0)
        src_layout.setSpacing(10)
        self.chk_cnki = QCheckBox("CNKI", src_widget)
        self.chk_cnki.setObjectName("sourceCheck")
        self.chk_cnki.setChecked(True)
        self.chk_cnki.setIcon(theme.paint_icon("database", "#5b7fb9", 14))
        src_layout.addWidget(self.chk_cnki)
        self.chk_wanfang = QCheckBox("万方", src_widget)
        self.chk_wanfang.setObjectName("sourceCheck")
        self.chk_wanfang.setIcon(theme.paint_icon("database", "#5b7fb9", 14))
        src_layout.addWidget(self.chk_wanfang)
        self.chk_ieee = QCheckBox("IEEE Xplore", src_widget)
        self.chk_ieee.setObjectName("sourceCheck")
        self.chk_ieee.setIcon(theme.paint_icon("database", "#5b7fb9", 14))
        src_layout.addWidget(self.chk_ieee)
        for label in ("其他资源（暂未实现）",):
            cb = QCheckBox(label, src_widget)
            cb.setEnabled(False)
            src_layout.addWidget(cb)
        src_layout.addStretch(1)
        grid.addWidget(src_widget, 3, 1)

        grid.addWidget(self._form_label("语言筛选", card), 3, 2)
        lang_widget = QWidget(card)
        lang_layout = QHBoxLayout(lang_widget)
        lang_layout.setContentsMargins(0, 0, 0, 0)
        lang_layout.setSpacing(8)
        # v0.9.1：语言筛选正式接入。三个互斥可勾选按钮，默认中英双语；
        # 切换时联动数据来源可用性（勾选状态保留，切回自动恢复）。
        self.lang_group = QButtonGroup(self)
        self.lang_group.setExclusive(True)
        self.lang_buttons: dict[str, QToolButton] = {}
        for option in _LANGUAGE_OPTIONS:
            mode = _LANGUAGE_MODES[option]
            btn = QToolButton(lang_widget)
            btn.setObjectName("langButton")
            btn.setText(option)
            btn.setCheckable(True)
            btn.setToolTip(_LANGUAGE_TOOLTIPS[mode])
            self.lang_group.addButton(btn)
            self.lang_buttons[mode] = btn
            lang_layout.addWidget(btn)
        self.lang_buttons["bilingual"].setChecked(True)
        self.lang_group.buttonClicked.connect(self._apply_language_mode)
        lang_layout.addStretch(1)
        grid.addWidget(lang_widget, 3, 3)

        # 行 5：结果数量 / 排序方式
        grid.addWidget(self._form_label("结果数量", card), 4, 0)
        # v0.13：QSpinBox 自由数量（属性名保留 combo_count，test_gui.py 公开表面）
        self.combo_count = QSpinBox(card)
        self.combo_count.setRange(MIN_RESULT_COUNT, MAX_RESULT_COUNT)
        self.combo_count.setValue(20)
        self.combo_count.setToolTip(f"范围 {MIN_RESULT_COUNT}-{MAX_RESULT_COUNT}")
        grid.addWidget(self.combo_count, 4, 1)
        grid.addWidget(self._form_label("排序方式", card), 4, 2)
        self.combo_rank = QComboBox(card)
        self.combo_rank.addItem("综合推荐", RankingMode.COMPREHENSIVE.value)
        self.combo_rank.addItem("最新发表", RankingMode.RECENT.value)
        self.combo_rank.addItem("高引用", RankingMode.CITATION.value)
        self.combo_rank.setToolTip(
            "综合推荐：标题相关性 + 年份 + 引用影响力综合评分。\n"
            "最新发表：按发表年份从新到旧。\n"
            "高引用：按各数据库分别优先提取高被引论文，并保持来源尽量均衡。"
        )
        grid.addWidget(self.combo_rank, 4, 3)

        # 行 5：起止年月（v0.13，格式 YYYY.MM，空值=不限）
        grid.addWidget(self._form_label("开始年月", card), 5, 0)
        self.start_date_input = QLineEdit(card)
        self.start_date_input.setPlaceholderText("如 2023.01")
        grid.addWidget(self.start_date_input, 5, 1)
        grid.addWidget(self._form_label("结束年月", card), 5, 2)
        self.end_date_input = QLineEdit(card)
        self.end_date_input.setPlaceholderText("如 2025.12")
        grid.addWidget(self.end_date_input, 5, 3)

        # 行 6：主操作按钮
        self.btn_check_auth = QPushButton("检查授权状态", card)
        self.btn_check_auth.setObjectName("btnSecondary")
        self.btn_check_auth.setIcon(theme.paint_icon("shield"))
        self.btn_check_auth.setMinimumHeight(32)
        self.btn_check_auth.setCursor(Qt.CursorShape.PointingHandCursor)
        grid.addWidget(self.btn_check_auth, 6, 0, 1, 2)
        self.btn_search = QPushButton("开始检索", card)
        self.btn_search.setObjectName("btnSearchPrimary")
        self.btn_search.setIcon(theme.paint_icon("search", "#ffffff"))
        self.btn_search.setMinimumHeight(32)
        self.btn_search.setCursor(Qt.CursorShape.PointingHandCursor)
        grid.addWidget(self.btn_search, 6, 2, 1, 2)

        layout.addWidget(card)
        return block

    def _build_status_strip(self, central: QWidget) -> QFrame:
        strip = QFrame(central)
        strip.setObjectName("statusStrip")
        box = QHBoxLayout(strip)
        box.setContentsMargins(12, 4, 12, 4)
        box.setSpacing(8)
        self.status_icon = QLabel(strip)
        self.status_icon.setPixmap(theme.pixmap_painted("shield", theme.COLORS["primary"], 16))
        box.addWidget(self.status_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label_status = QLabel("状态：未检查", strip)
        self.label_status.setObjectName("statusText")
        box.addWidget(self.label_status, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addStretch(1)
        return strip

    def _build_results_card(self, central: QWidget) -> QFrame:
        card = QFrame(central)
        card.setObjectName("resultsCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 6, 12, 8)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        head_icon = QLabel(card)
        head_icon.setPixmap(theme.pixmap_painted("document", theme.COLORS["primary"], 17))
        head_title = QLabel("检索结果", card)
        head_title.setObjectName("cardSectionTitle")
        head.addWidget(head_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(head_title, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        # v0.18 Phase 2.7-D：整理模式切换固定在结果区域右上角
        # （默认基础整理；增强模式为可选能力，不影响搜索流程）。
        self.combo_summary_mode = QComboBox(card)
        self.combo_summary_mode.addItem("基础整理", "basic")
        self.combo_summary_mode.addItem("AI增强分析", "enhanced")
        self.combo_summary_mode.setToolTip(
            "基础整理：自动关键信息整理（离线四字段，无 AI）。\n"
            "AI增强分析：AI 深度分析（未配置时提示不可用）。"
        )
        head.addWidget(self.combo_summary_mode, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)

        self.table = QTableWidget(0, len(RESULT_COLUMNS), card)
        self.table.setObjectName("resultTable")
        self.table.setHorizontalHeaderLabels(RESULT_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(RESULT_COLUMNS)):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeToContents
            )
        layout.addWidget(self.table, 1)

        # 空态插画（覆盖在表体上，保留表头；行数 > 0 时隐藏）
        self._empty_overlay = QWidget(self.table.viewport())
        self._empty_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        overlay_layout = QVBoxLayout(self._empty_overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.addStretch(1)
        empty_icon = QLabel(self._empty_overlay)
        empty_icon.setPixmap(theme.empty_state_pixmap(110))
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_text = QLabel("请设置检索条件并开始检索", self._empty_overlay)
        empty_text.setObjectName("emptyCaption")
        empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addWidget(empty_icon)
        overlay_layout.addWidget(empty_text)
        overlay_layout.addStretch(1)
        self._empty_overlay.setVisible(True)
        self.table.viewport().installEventFilter(self)
        return card

    def _build_bottom_bar(self, central: QWidget) -> _ResponsiveActionBar:
        bar = _ResponsiveActionBar(central)
        buttons = (
            ("btn_favorite", "收藏当前论文", "shield"),
            ("btn_detail", "打开全文页面", "external"),
            ("btn_explanation", "查看推荐理由", "info"),
            ("btn_summary", "关键信息整理", "document"),
            ("btn_ieee_original", "访问IEEE原始站", "external"),
            ("btn_export", "导出结果", "download"),
            ("btn_report", "生成报告", "document"),
            ("btn_library", "我的论文库", "database"),
            ("btn_demo", "加载演示数据", "database"),
            ("btn_settings", "设置", "settings"),
            ("btn_about", "关于", "info"),
        )
        for attr, text, icon in buttons:
            btn = QPushButton(text, central)
            btn.setObjectName("btnGhost")
            btn.setIcon(theme.paint_icon(icon))
            btn.setMinimumHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            setattr(self, attr, btn)
            bar.add_button(btn)

        self.btn_detail.setToolTip("在受控 Edge 中打开来源站点提供的文献全文/落地页面")
        self.btn_explanation.setToolTip("查看当前选中论文的评分拆解与推荐理由")
        self.btn_summary.setToolTip(
            "根据当前可获取的摘要、正文及元数据，自动整理研究内容/核心技术/"
            "主要成果/应用价值（离线抽取，非 AI 生成）")
        self.btn_ieee_original.setToolTip(
            "在受控 Edge 中打开 IEEE 原始站论文页面（不经校园代理，"
            "代理页白屏时的稳定入口；全文阅读仍需机构订阅）"
        )
        self.btn_favorite.setToolTip("将当前选中的论文保存到本地论文库")
        self.btn_report.setToolTip("根据当前结果与检索条件生成 Markdown 报告")
        self.btn_library.setToolTip("打开本地论文库，查看和管理已收藏的论文")
        self.btn_demo.setToolTip(
            "加载内置示例结果（无需网络/授权），用于演示去重、排序、报告与导出"
        )
        self._action_bar = bar
        return bar

    def _build_footer(self, central: QWidget) -> QFrame:
        footer = QFrame(central)
        footer.setObjectName("footerBand")
        # v0.17 Phase 2.6-PKG-B Hotfix：固定高 26，为 125% DPI 小屏让垂直空间。
        footer.setFixedHeight(26)
        box = QHBoxLayout(footer)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(18)
        box.addStretch(1)
        motto_left = QLabel("实事求是", footer)
        motto_left.setObjectName("footerMotto")
        seal = QLabel(footer)
        seal.setPixmap(theme.emblem_pixmap(22, ring="#cfdcf3"))
        motto_right = QLabel("严谨治学", footer)
        motto_right.setObjectName("footerMotto")
        box.addWidget(motto_left, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addWidget(seal, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addWidget(motto_right, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addStretch(1)
        return footer

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt 命名)
        if obj is self.table.viewport() and event.type() == QEvent.Type.Resize:
            self._empty_overlay.resize(self.table.viewport().size())
        return super().eventFilter(obj, event)

    def _start_worker(self) -> None:
        self._thread = QThread(self)
        self._worker = BrowserWorker()
        self._worker.moveToThread(self._thread)
        self._thread.start()
        # v0.13 多窗口：详情打开由 DetailManager 接管（独立线程+独立会话）
        self._detail_manager = DetailManager(
            storage_state_path=STORAGE_STATE_PATH, parent=self
        )
        # v0.17 Phase 2.4-C-B：单条摘要后台获取 worker（独立线程；
        # 不改 SearchWorker 生命周期；single-flight 由 _on_summarize 保证）
        self._evidence_busy = False
        self._pending_summary_row: dict | None = None
        self._evidence_thread = QThread(self)
        self._evidence_worker = PaperEvidenceWorker()
        self._evidence_worker.moveToThread(self._evidence_thread)
        self._evidence_thread.start()
        self.evidence_requested.connect(self._evidence_worker.fetch_abstract)
        self._evidence_worker.abstract_ready.connect(self._on_evidence_ready)
        self._evidence_worker.evidence_failed.connect(self._on_evidence_failed)
        self._evidence_worker.finished.connect(self._on_evidence_finished)
        # v0.18 Phase 2.7-B：增强分析后台 worker（真实 LLM 可能 5-30s，
        # 禁止阻塞主线程；未配置时 UI 层直接提示，不启动线程）。
        self._summary_busy = False
        self._summary_thread = QThread(self)
        self._summary_worker = SummaryWorker(self._summary_service)
        self._summary_worker.moveToThread(self._summary_thread)
        self._summary_thread.start()
        self.enhanced_requested.connect(self._summary_worker.run_enhanced)
        self._summary_worker.succeeded.connect(self._on_enhanced_ready)
        self._summary_worker.failed.connect(self._on_enhanced_failed)
        self._summary_worker.finished.connect(self._on_enhanced_finished)

        # v0.18 Phase 2.9-C-E：调研报告后台 worker（LLM 可能数十秒）
        self._report_thread = QThread(self)
        self._report_worker = ReportWorker()
        self._report_worker.moveToThread(self._report_thread)
        self._report_thread.start()
        self.report_requested.connect(self._report_worker.run_report)
        self._report_worker.progress.connect(self._on_report_progress)
        self._report_worker.succeeded.connect(self._on_report_succeeded)
        self._report_worker.failed.connect(self._on_report_failed)
        self._report_worker.finished.connect(self._on_report_finished)

    def _connect_worker(self) -> None:
        # 按钮点击 → 处理函数
        self.btn_check_auth.clicked.connect(self._on_check_auth)
        self.btn_search.clicked.connect(self._on_search)
        self.btn_favorite.clicked.connect(self._on_favorite)
        self.btn_detail.clicked.connect(self._on_open_detail)
        self.btn_explanation.clicked.connect(self._on_explanation)
        self.btn_summary.clicked.connect(self._on_summarize)
        self.btn_ieee_original.clicked.connect(self._on_open_ieee_original)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_report.clicked.connect(self._on_generate_report)
        self.btn_library.clicked.connect(self._on_open_library_window)
        self.btn_about.clicked.connect(self._on_about)
        self.btn_demo.clicked.connect(self._on_load_demo)
        # v0.18 Phase 2.8-A：AI 设置入口（关于附近）
        self.btn_settings.clicked.connect(self._on_settings)
        # 请求（主线程 → worker 线程）
        self.check_auth_requested.connect(self._worker.check_auth)
        self.search_requested.connect(self._worker.search)
        self.shutdown_requested.connect(self._worker.shutdown)
        # 详情打开迁移到 DetailManager（v0.13 多窗口：每个详情页独立线程+会话）
        self.open_detail_requested.connect(self._detail_manager.open_detail)
        # v0.13 B′：万方无 detail_url 时按标题按需解析
        self.open_wanfang_detail_requested.connect(
            self._detail_manager.open_detail_by_title
        )
        # 结果（worker 线程 → 主线程）
        self._worker.status_changed.connect(self._set_status)
        self._worker.auth_result.connect(self._on_auth_result)
        self._worker.search_done.connect(self._on_search_done)
        self._worker.search_failed.connect(self._on_search_failed)
        self._worker.error.connect(self._on_worker_error)
        # 详情管理器信号 → 原 UI 槽（接口不变）
        self._detail_manager.detail_opened.connect(self._on_detail_opened)
        self._detail_manager.error.connect(self._set_status)
        self._detail_manager.status.connect(self._set_status)

    # ---------- 动作 ----------
    def _on_check_auth(self) -> None:
        self._set_status("正在检查天津大学授权...")
        self.btn_check_auth.setEnabled(False)
        self.check_auth_requested.emit()

    def _on_search(self) -> None:
        if self._searching:
            return
        # v0.17 Phase 1：研究方向 OR 人员姓名（至少一项）即可发起检索；
        # 仅人员单位不能作为主检索条件（人员单位只随作者/主题条件传递）。
        direction = self.input_direction.text().strip()
        person = self.input_author_name.text().strip()
        if not direction and not person:
            QMessageBox.warning(self, "提示", "请输入研究方向或人员姓名，至少填写一项。")
            return
        # v0.17 Phase 2.2-D hotfix（Phase 2.3-C 扩展至新闻）：成果类型全未
        # 勾选 → 明确阻止。不得自动勾论文 / 回退 paper / 调 SearchService
        # （人工 GUI 验收 Case B）。
        if (not self.chk_artifact_paper.isChecked()
                and not self.chk_artifact_patent.isChecked()
                and not self.chk_artifact_news.isChecked()):
            QMessageBox.warning(self, "提示", "请至少选择一种成果类型。")
            return
        # 语言筛选在构造请求前过滤来源（GUI 层；不改 Adapter/SearchService）。
        # 被禁用来源即使勾选保留，也不会发出请求。
        selected_databases = []
        if self.chk_cnki.isChecked() and self.chk_cnki.isEnabled():
            selected_databases.append("CNKI")
        if self.chk_wanfang.isChecked() and self.chk_wanfang.isEnabled():
            selected_databases.append("万方")
        if self.chk_ieee.isChecked() and self.chk_ieee.isEnabled():
            selected_databases.append("IEEE Xplore")
        # v0.17 Phase 2.3-C：成果类型 × 数据库 → 内部 effective sources。
        # 成果类型与数据库来源正交；"万方专利"/"天津大学新闻网"是内部
        # source 名，不作为数据库复选框出现（source ≠ artifact_type）。
        selected_artifacts = []
        if self.chk_artifact_paper.isChecked():
            selected_artifacts.append("paper")
        if self.chk_artifact_patent.isChecked():
            selected_artifacts.append("patent")
        news_requested = self.chk_artifact_news.isChecked()
        # 语言适用性（GUI 层过滤，resolver 保持纯映射）：
        # 新闻为中文站点，英文模式下不可执行（基于最终 effective sources 判定）。
        news_language_blocked = news_requested and self.language_mode == "en"
        if news_requested and not news_language_blocked:
            selected_artifacts.append("news")
        sources = resolve_effective_sources(selected_databases, selected_artifacts)
        # 无有效来源保护（阻止原因逐条列出，单一原因文案与既有提示一致）：
        # - 仅专利（未选论文/新闻或新闻被语言排除）且无专利可用数据库 → 阻止；
        # - 仅新闻 + 英文 → 阻止并提示切换语言；
        # - 其它无有效来源组合 → 通用阻止。
        paper_lost = ("paper" in selected_artifacts
                      and not any(s in sources for s in ("CNKI", "万方", "IEEE Xplore")))
        patent_lost = "patent" in selected_artifacts and "万方专利" not in sources
        if not sources:
            reasons = []
            if news_language_blocked:
                reasons.append(
                    "天津大学新闻网当前仅支持中文新闻检索，请切换为中文或中英双语。")
            if patent_lost:
                reasons.append("当前专利检索支持 CNKI 和万方，请至少勾选其中一个数据源。")
            if paper_lost:
                reasons.append("未选择可用的论文数据源。")
            QMessageBox.warning(
                self, "提示",
                "\n".join(reasons) if reasons else "请至少选择一个可执行的数据来源。",
            )
            return
        # 部分支持：非阻断状态提示（不阻止其它已有效的成果类型）
        notices = []
        filter_mode = self.combo_affiliation_mode.currentData() or "soft"
        if patent_lost:
            if "天津大学新闻网" in sources:
                notices.append("当前所选来源不支持专利检索，本次将仅检索新闻。")
            else:
                notices.append("当前所选来源中没有支持专利检索的数据库，将仅检索论文。")
        if paper_lost:
            if "天津大学新闻网" in sources:
                notices.append("未选择可用的论文数据源，本次将仅检索新闻。")
        if news_language_blocked:
            notices.append("当前语言模式不支持中文新闻，本次将不检索新闻。")
        # v0.17 Phase 2.5-B：扩展方向 applicability 非阻塞提示（合并为一句；
        # GENERAL / pure person 无提示；不 block 搜索）。
        if direction:
            _applied, _fallback, exp_hint = resolve_expansion_behavior(
                selected_artifacts, sources,
                self.combo_expansion.currentText(), has_topic=True)
            if exp_hint:
                notices.append(f"扩展方向：{exp_hint}")
        if notices:
            self._set_status("提示：" + "；".join(notices))
        # 严格匹配 × 新闻：新闻无人员单位元数据（Phase 2.2-A：strict 缺失删除）。
        # 全新闻来源 + 单位条件 + strict → 必然 0 条，提交前明确阻止；
        # 混合来源 → 非阻断提示，不擅自更改用户的 strict 设置。
        news_only = sources == ["天津大学新闻网"]
        affiliation = self.input_author_affiliation.text().strip()
        if (filter_mode == "strict" and affiliation and news_only
                and "天津大学新闻网" in sources):
            QMessageBox.warning(
                self, "提示",
                "新闻结果暂无人员单位元数据，严格单位匹配将无法保留新闻结果。"
                "请切换为智能匹配或清空人员单位。",
            )
            return
        if (filter_mode == "strict" and affiliation
                and "天津大学新闻网" in sources):
            self._set_status("提示：严格单位匹配可能排除新闻结果。")
        # 严格匹配 × IEEE Xplore 一律拦截：英文库作者单位元数据严重缺失，
        # strict 会把无单位数据的论文整批误删。判定基于 resolve 后的
        # effective sources（而非 UI 上 IEEE checkbox 是否勾选）。
        if filter_mode == "strict" and "IEEE Xplore" in sources:
            if self.language_mode == "en":
                message = "英文检索暂不支持严格匹配，请切换为智能匹配。"
            else:
                message = "IEEE Xplore 暂不支持严格匹配：请切换为智能匹配，或取消勾选 IEEE Xplore。"
            QMessageBox.warning(self, "提示", message)
            return
        query = QueryRequest(
            research_direction=direction,
            expansion_direction=self.combo_expansion.currentText(),
            databases=sources,
            result_count=self.combo_count.value(),
            sources=sources,
            ranking_mode=self.combo_rank.currentData() or RankingMode.COMPREHENSIVE.value,
            # v0.13 高级检索条件（人员姓名/单位即作者条件）
            author_name=self.input_author_name.text().strip(),
            author_affiliation=self.input_author_affiliation.text().strip(),
            start_date=self.start_date_input.text().strip(),
            end_date=self.end_date_input.text().strip(),
            affiliation_filter_mode=filter_mode,
        )
        try:
            query.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            return
        self._searching = True
        self._last_query_info = {
            "research_direction": direction,
            "expansion_direction": query.expansion_direction,
            "sources": list(sources),
        }
        self.btn_search.setEnabled(False)
        self.btn_check_auth.setEnabled(False)
        self.btn_detail.setEnabled(False)
        self.btn_explanation.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_report.setEnabled(False)
        self.table.setRowCount(0)
        self._results = []
        self._update_empty_state()
        self.search_requested.emit(query)

    def _on_favorite(self) -> None:
        """收藏当前选中的论文。

        v0.18 Phase 2.9-B：
        - 身份去重（DOI → URL → 标题+作者+年份 → 标题）：同一篇论文再次收藏
          更新已有记录并提示"论文库记录已更新"，不新增第二条；
        - 只写入"当前已有"的分析结果（基础整理 / AI 增强分析），
          收藏流程本身不发起任何 LLM 请求（API calls = 0）。
        """
        sel = self.table.currentRow()
        if sel < 0 or sel >= len(self._results):
            QMessageBox.information(self, "提示", "请先在表格中选择一条结果。")
            return
        data = self._results[sel]
        title = data.get("title") or ""
        if not title:
            QMessageBox.information(self, "提示", "该结果没有可用的标题。")
            return
        try:
            from tju_info_retrieval.models.library import LibraryRecord

            record = LibraryRecord.from_search_result(data)
            # 只保存已有分析（不触发 API）
            basic = self._basic_summary_cache.get(self._analysis_key(data))
            if basic is not None:
                record.set_basic_summary(basic)
            enhanced = self._enhanced_summary_cache.get(self._analysis_key(data))
            if enhanced is not None:
                self._apply_analysis_meta(record, enhanced)

            saved, was_update = self._library_store.upsert_favorite(record)
            if was_update:
                self._set_status(f"论文库记录已更新：{saved.title}")
            else:
                self._set_status(f"已收藏：{saved.title}")
        except Exception as exc:
            QMessageBox.critical(self, "收藏失败", f"收藏失败：{exc}")

    # ---------- 分析结果 → 论文库同步（v0.18 Phase 2.9-B） ----------

    @staticmethod
    def _analysis_key(data: dict) -> str:
        """内存分析缓存键：优先 detail_url，退化到标题。"""
        return str(data.get("detail_url") or data.get("title") or "").strip()

    def _apply_analysis_meta(self, record, summary) -> None:
        """把当前 provider / model / prompt_version / profile_version 写入记录。

        用于判断论文库中的分析是否基于旧画像或旧 Prompt（过期提示）。
        """
        from tju_info_retrieval.services.prompts.enhanced_summary_prompt import (
            ENHANCED_PROMPT_VERSION,
        )

        model = ""
        try:
            model = str(self._api_config.load_config().get("model") or "")
        except Exception:  # noqa: BLE001 - 元信息缺失不影响保存
            model = ""
        profile = self._research_profile
        profile_version = ""
        try:
            if profile is not None and profile.is_configured():
                profile_version = f"p{int(profile.profile_version)}"
        except Exception:  # noqa: BLE001
            profile_version = ""
        record.set_enhanced_summary(
            summary,
            provider=str(getattr(summary, "provider", "") or ""),
            model=model,
            prompt_version=ENHANCED_PROMPT_VERSION,
            profile_version=profile_version,
        )

    def _sync_basic_to_library(self, result, summary) -> None:
        """基础整理完成后回写论文库（仅当该论文已收藏）。"""
        try:
            self._basic_summary_cache[self._analysis_key(
                result.to_dict() if hasattr(result, "to_dict") else {})] = summary
            record = self._library_record_for(result)
            if record is None:
                return
            self._library_store.update(record.id, basic_summary=summary)
        except Exception:  # noqa: BLE001 - 论文库同步失败不影响整理展示
            pass

    def _sync_enhanced_to_library(self, result, summary) -> None:
        """AI 增强分析完成后回写论文库（仅当该论文已收藏；不新增记录）。"""
        try:
            from tju_info_retrieval.models.library import LibraryRecord

            key = self._analysis_key(
                result.to_dict() if hasattr(result, "to_dict") else {})
            self._enhanced_summary_cache[key] = summary
            record = self._library_record_for(result)
            if record is None:
                return
            holder = LibraryRecord()
            self._apply_analysis_meta(holder, summary)
            self._library_store.update(
                record.id,
                enhanced_summary=summary,
                enhanced_provider=holder.enhanced_provider,
                enhanced_model=holder.enhanced_model,
                enhanced_prompt_version=holder.enhanced_prompt_version,
                enhanced_profile_version=holder.enhanced_profile_version,
            )
        except Exception:  # noqa: BLE001
            pass

    def _library_record_for(self, result):
        """按业务身份查找论文库中的对应记录（未收藏 → None）。"""
        from tju_info_retrieval.models.library import LibraryRecord

        try:
            probe = LibraryRecord.from_search_result_obj(result)
            return self._library_store.find_by_identity(probe)
        except Exception:  # noqa: BLE001
            return None

    def library_store(self) -> LibraryStore:
        """论文库存储（论文库窗口与重新分析流程共用）。"""
        return self._library_store

    def current_profile_version(self) -> str:
        """当前画像版本标签（空画像 → ""）。"""
        profile = self._research_profile
        try:
            if profile is not None and profile.is_configured():
                return f"p{int(profile.profile_version)}"
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _on_open_detail(self) -> None:
        sel = self.table.currentRow()
        if sel < 0 or sel >= len(self._results):
            QMessageBox.information(self, "提示", "请先在表格中选择一条结果。")
            return
        url = self._results[sel].get("detail_url")
        if not url:
            # v0.13 B′ 万方按需解析：万方结果页无 href，detail_url 缺失时
            # 按标题在受控浏览器中定位真实详情页（CNKI/IEEE 不走此分支）
            row = self._results[sel]
            title = (row.get("title") or "").strip()
            if row.get("database") == "万方" and title:
                self.open_wanfang_detail_requested.emit(title)
                return
            QMessageBox.information(self, "提示", "该结果没有可用的全文页面链接。")
            return
        self._set_status("正在打开全文页面...")
        self.open_detail_requested.emit(url)

    def _on_open_dashboard(self, demo_mode: bool = False) -> None:
        """打开科研展示首页（v0.18 Phase 2.10-B；只读，不写数据）。"""
        from tju_info_retrieval.services.research_report_store import (
            ResearchReportStore,
        )
        from tju_info_retrieval.ui.dashboard_widget import DashboardDialog

        dialog = DashboardDialog(
            library_store=self._library_store,
            report_store=ResearchReportStore(),
            profile_store=self._profile_store,
            demo_mode=demo_mode,
            parent=self,
        )
        dialog.search_requested.connect(self._on_dashboard_search)
        dialog.library_requested.connect(self._on_open_library_window)
        dialog.report_requested.connect(self._on_dashboard_report)
        self._dashboard_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_dashboard_search(self) -> None:
        """首页"搜索论文"：聚焦检索输入（回到既有主流程）。"""
        self.input_direction.setFocus()
        self._set_status("请输入研究方向或人员姓名，点击【开始检索】。")

    def _on_dashboard_report(self) -> None:
        """首页"生成报告"：打开论文库（报告在库内勾选后生成）。"""
        self._on_open_library_window()
        self._set_status("请在论文库中勾选论文后点击【生成调研报告】。")

    def _on_open_library_window(self) -> None:
        """打开我的论文库窗口（v0.9.3 Phase 2；2.9-B 接入分析持久化）。"""
        from tju_info_retrieval.ui.library_window import LibraryWindow

        win = LibraryWindow(self._library_store, parent=self)
        win.open_detail_requested.connect(self.open_detail_requested)
        win.reanalyze_basic_requested.connect(self._reanalyze_library_basic)
        win.reanalyze_enhanced_requested.connect(self._reanalyze_library_enhanced)
        win.generate_report_requested.connect(self._generate_library_report)
        win.set_profile_version(self.current_profile_version())
        win.set_ai_configured(self._summary_service.enhanced_configured)
        self._library_window = win
        win.show()
        win.raise_()
        win.activateWindow()

    # ---------- 科研调研报告（v0.18 Phase 2.9-C-E） ----------

    def _build_report_service(self):
        """构建 ReportService（真实 LLM 可用时注入 LLMReportProvider）。

        UI 只负责"构建 Provider 并交给 Service"；调用链在 worker 线程里
        经 ReportService → ReportProvider 完成，UI 不直接调用 LLM。
        """
        from tju_info_retrieval.services.report_service import ReportService

        provider = None
        try:
            from tju_info_retrieval.services.llm_provider import (
                OpenAICompatibleProvider,
            )
            from tju_info_retrieval.services.llm_report_provider import (
                LLMReportProvider,
            )

            cfg = self._api_config.provider_config()
            if cfg.get("enabled"):
                provider = LLMReportProvider(
                    llm_client=OpenAICompatibleProvider(config=cfg))
        except Exception:  # noqa: BLE001 - 构建失败 → 降级为基础报告
            provider = None
        return ReportService(
            provider=provider,
            library_store=self._library_store,
            profile_store=self._profile_store,
        )

    def _generate_library_report(self, paper_ids) -> None:
        """论文库"生成调研报告"：数量校验 → 后台 worker（不阻塞 GUI）。"""
        ids = [str(p) for p in (paper_ids or []) if str(p).strip()]
        if not ids:
            QMessageBox.information(self, "提示", "请先勾选要纳入报告的论文。")
            return
        if self._report_busy:
            return  # single-flight
        if self._report_service is None:
            self._report_service = self._build_report_service()
        self._report_worker.set_service(self._report_service)
        self._report_busy = True
        self._set_status(f"正在生成调研报告（{len(ids)} 篇）…")
        self._show_report_progress(len(ids))
        self.report_requested.emit(ids, "")

    def _show_report_progress(self, total: int) -> None:
        from PySide6.QtWidgets import QProgressDialog

        self._close_report_progress()
        dialog = QProgressDialog("正在生成调研报告…", "", 0, max(total, 1), self)
        dialog.setWindowTitle("调研报告")
        dialog.setCancelButton(None)          # 不提供取消：Service 内部不可中断
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setValue(0)
        dialog.show()
        self._report_progress = dialog

    def _close_report_progress(self) -> None:
        dialog = getattr(self, "_report_progress", None)
        if dialog is not None:
            try:
                dialog.close()
                dialog.deleteLater()
            except RuntimeError:  # pragma: no cover - 已销毁
                pass
            self._report_progress = None

    def _on_report_progress(self, current: int, total: int, stage: str) -> None:
        dialog = getattr(self, "_report_progress", None)
        if dialog is None:
            return
        try:
            dialog.setLabelText(f"正在生成调研报告…（{stage}）")
            if total:
                dialog.setMaximum(max(total, 1))
            dialog.setValue(min(current, max(total, 1)))
        except RuntimeError:  # pragma: no cover
            self._report_progress = None

    def _on_report_succeeded(self, result_dict: dict) -> None:
        """报告生成成功（或降级）：打开报告查看窗口；不显示 traceback。"""
        from tju_info_retrieval.services.report_service import (
            ReportGenerationResult,
        )

        self._close_report_progress()
        result = ReportGenerationResult.from_dict(result_dict or {})
        report = result.report
        if report is None:
            self._set_status("调研报告生成失败。")
            QMessageBox.information(
                self, "提示", result.message or "调研报告生成失败。")
            return

        if result.degraded_reason:
            self._set_status(result.message or "已生成基础报告（未调用 AI）。")
        elif result.from_cache:
            self._set_status("已从缓存读取调研报告。")
        else:
            self._set_status(
                f"调研报告已生成（{report.paper_count} 篇）。")

        try:
            library_ids = [r.id for r in self._library_store.load_all()]
        except Exception:  # noqa: BLE001
            library_ids = []
        dialog = ResearchReportDialog(
            report, self, result=result,
            current_profile_version=self.current_profile_version(),
            current_prompt_version=self._current_report_prompt_version(),
            library_paper_ids=library_ids)
        dialog.exec()

    def _on_report_failed(self, message: str) -> None:
        """兜底失败（正常路径由 Service 降级处理）：可读文案，无 traceback。"""
        self._close_report_progress()
        self._set_status(f"调研报告生成失败：{message}")
        QMessageBox.information(self, "提示", message or "调研报告生成失败。")

    def _on_report_finished(self) -> None:
        self._close_report_progress()
        self._report_busy = False

    @staticmethod
    def _current_report_prompt_version() -> str:
        try:
            from tju_info_retrieval.services.prompts.research_report_prompt import (
                REPORT_PROMPT_VERSION,
            )

            return REPORT_PROMPT_VERSION
        except Exception:  # noqa: BLE001
            return ""

    def _refresh_library_window(self) -> None:
        """刷新已打开的论文库窗口（重新分析完成后）。"""
        win = self._library_window
        if win is None:
            return
        try:
            win.set_profile_version(self.current_profile_version())
            win.set_ai_configured(self._summary_service.enhanced_configured)
            win.refresh_records()
        except Exception:  # noqa: BLE001 - 窗口可能已销毁
            self._library_window = None

    @staticmethod
    def _result_from_library(record):
        """由论文库记录重建 SearchResult（重新分析用；Evidence 取自记录）。"""
        from tju_info_retrieval.models.result import SearchResult

        return SearchResult(
            rank=0,
            title=record.title,
            authors=list(record.authors),
            source=record.source,
            year=record.year,
            detail_url=record.detail_url,
            document_type=None,
            database=record.database or "CNKI",
            abstract=record.abstract,
            doi=record.doi,
            venue=record.venue,
            artifact_type=record.artifact_type or "paper",
            artifact_metadata=dict(record.metadata),
        )

    def _reanalyze_library_basic(self, record) -> None:
        """论文库「重新基础整理」：离线重算并回写（不调用任何 API）。"""
        if not (record.abstract or "").strip():
            QMessageBox.information(
                self, "提示", "该记录没有摘要等可用证据，无法重新整理。")
            return
        result = self._result_from_library(record)
        summary = self._summary_service.summarize(
            MODE_BASIC, result,
            research_profile=self._research_profile)
        self._library_store.update(record.id, basic_summary=summary)
        self._refresh_library_window()
        self._set_status("基础整理已更新。")

    def _reanalyze_library_enhanced(self, record) -> None:
        """论文库「重新AI分析」：用户主动触发 → SummaryWorker（后台线程）。

        使用当前 ResearchProfile / 当前 Prompt 版本 / 当前 Provider；
        Evidence / profile / prompt / provider 均未变时由 EnhancedCache 命中，
        不重复消耗 API。
        """
        if not (record.abstract or "").strip():
            QMessageBox.information(
                self, "提示", "该记录没有摘要等可用证据，无法重新分析。")
            return
        if not self._summary_service.enhanced_configured:
            self._prompt_enhanced_not_configured()
            return
        if self._summary_busy:
            return  # single-flight：分析进行中忽略再次点击
        result = self._result_from_library(record)
        self._summary_busy = True
        self._pending_enhanced_result = result
        self._pending_library_record_id = record.id
        self.btn_summary.setEnabled(False)
        self._set_status("正在重新分析...")
        direction = (self._last_query_info or {}).get("research_direction", "")
        self.enhanced_requested.emit(result, direction, self._research_profile)

    def _on_open_ieee_original(self) -> None:
        """经既有 open_detail 链路打开 IEEE 原始站论文页（v0.9.2 双入口）。

        仅做 URL 重建：detail_url → document id → 原始站 URL，
        复用 open_detail_requested 信号，不新增浏览器逻辑。
        """
        sel = self.table.currentRow()
        if sel < 0 or sel >= len(self._results):
            QMessageBox.information(self, "提示", "请先在表格中选择一条结果。")
            return
        url = ieee_original_url(self._results[sel].get("detail_url"))
        if not url:
            QMessageBox.information(self, "提示", "该结果没有可用的IEEE原始站链接。")
            return
        self._set_status("正在打开IEEE原始站页面...")
        self.open_detail_requested.emit(url)

    def _on_export(self) -> None:
        if not self._results:
            QMessageBox.information(self, "提示", "当前没有可导出的结果。")
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出结果",
            "cnki_results.json",
            "JSON (*.json);;CSV (*.csv);;Markdown (*.md)",
        )
        if not path:
            return
        # 以所选过滤器为准，其次按扩展名推断；缺失扩展名时自动补全
        fmt = "json"
        if "CSV" in selected_filter:
            fmt = "csv"
        elif "Markdown" in selected_filter:
            fmt = "md"
        lower = path.lower()
        if lower.endswith(".csv"):
            fmt = "csv"
        elif lower.endswith(".md"):
            fmt = "md"
        elif lower.endswith(".json"):
            fmt = "json"
        if fmt == "csv" and not lower.endswith(".csv"):
            path += ".csv"
        elif fmt == "md" and not lower.endswith(".md"):
            path += ".md"
        elif fmt == "json" and not lower.endswith(".json"):
            path += ".json"
        try:
            if fmt == "csv":
                export_service.export_csv(self._results, path)
            elif fmt == "md":
                export_service.export_markdown(self._results, path)
            else:
                export_service.export_json(self._results, path)
            self._set_status(f"已导出：{path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", f"导出失败：{exc}")

    # ---------- worker 结果回调 ----------
    def _on_auth_result(self, state: str) -> None:
        self.btn_check_auth.setEnabled(True)
        if state == "logged_in":
            self._set_status("天津大学授权正常")
        elif state == "login_page":
            self._set_status("需要重新登录天津大学图书馆（请在受控浏览器中完成官方登录后再次检查）")
        else:
            self._set_status("无法确定天津大学授权状态")

    def _on_search_done(self, results: list) -> None:
        self._searching = False
        self.btn_search.setEnabled(True)
        self.btn_check_auth.setEnabled(True)
        self.btn_favorite.setEnabled(True)
        self.btn_detail.setEnabled(True)
        self.btn_explanation.setEnabled(True)
        self.btn_summary.setEnabled(True)
        self.btn_ieee_original.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.btn_report.setEnabled(True)
        self._results = results
        self._populate_table(results)
        self._set_status(f"检索完成：{len(results)} 条")

    def _on_search_failed(self, message: str) -> None:
        self._searching = False
        self.btn_search.setEnabled(True)
        self.btn_check_auth.setEnabled(True)
        self.btn_favorite.setEnabled(False)
        self.btn_detail.setEnabled(False)
        self.btn_explanation.setEnabled(False)
        self.btn_summary.setEnabled(False)
        self.btn_ieee_original.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_report.setEnabled(False)
        self._set_status(f"检索失败：{message}")
        QMessageBox.warning(self, "检索失败", message)

    def _on_worker_error(self, message: str) -> None:
        self.btn_check_auth.setEnabled(True)
        self._set_status(message)
        QMessageBox.critical(self, "错误", message)

    def _on_detail_opened(self, url: str) -> None:
        self._set_status("论文全文页面已在浏览器中打开")

    def _on_generate_report(self) -> None:
        if not self._results:
            QMessageBox.information(self, "提示", "当前没有可生成报告的结果。")
            return
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "生成报告",
            "检索报告.md",
            "Markdown (*.md)",
        )
        if not path:
            return
        if not path.lower().endswith(".md"):
            path += ".md"
        try:
            explanations = [self._explanation_for(r) for r in self._results]
            content = report_generator.generate_markdown(
                self._results, self._last_query_info, explanations
            )
            report_generator.write_markdown(content, path)
            self._set_status(f"报告已生成：{path}")
        except Exception as exc:
            QMessageBox.critical(self, "生成失败", f"报告生成失败：{exc}")

    def _explanation_for(self, result: dict) -> RankingExplanation:
        """为单条结果计算评分拆解（与 RankingService 排序同一套逻辑）。

        评分分项只依赖单条结果与检索词，与候选池/排序无关，
        因此按结果逐条计算即可得到与检索时一致的拆解。
        """
        query = QueryRequest(
            research_direction=(self._last_query_info or {}).get("research_direction", ""),
        )
        _, explanations = RankingService.rank_with_explanation(
            [SearchResult.from_dict(result)], query,
        )
        return explanations[0]

    def _on_summarize(self) -> None:
        """关键信息整理（单条用户触发；basic/enhanced 双模式路由）。

        v0.17 Phase 2.4-C-B 路由（保持）：
        - paper 且 database=CNKI/IEEE 且无摘要 → 后台单条 on-demand 摘要获取
          （SEARCH 阶段零详情访问；仅此处触发）；
        - 其余（含已有摘要的 paper / 非 paper）→ 直接按当前模式整理。
        v0.18 Phase 2.7-A：
        - 模式由 combo_summary_mode 决定（默认 basic，行为与 v0.17 完全一致）；
        - enhanced 未配置 Provider 时显示固定提示，不崩溃。
        """
        if self._evidence_busy:
            return  # single-flight：fetch 进行中忽略再次点击
        sel = self.table.currentRow()
        if sel < 0 or sel >= len(self._results):
            QMessageBox.information(self, "提示", "请先在表格中选择一条结果。")
            return
        row = self._results[sel]
        result = SearchResult.from_dict(row)
        artifact_type = getattr(result, "artifact_type", "paper") or "paper"
        is_fetchable_paper = (
            artifact_type == "paper"
            and result.database in ("CNKI", "IEEE Xplore"))
        if is_fetchable_paper and not (result.abstract or "").strip():
            detail_url = (result.detail_url or "").strip()
            if not detail_url.startswith("http"):
                QMessageBox.information(
                    self, "提示", "当前论文缺少可用详情链接，无法获取摘要。")
                return
            # 后台获取（单条）；成功后写回该行 abstract 再整理
            self._evidence_busy = True
            self._pending_summary_row = row
            self.btn_summary.setEnabled(False)
            self._set_status("正在获取论文摘要...")
            self.evidence_requested.emit(result.database, detail_url)
            return
        self._show_summary_for(result)

    def _summary_mode(self) -> str:
        """当前整理模式（basic 默认；未知值回退 basic）。"""
        data = self.combo_summary_mode.currentData()
        return data if data in (MODE_BASIC, MODE_ENHANCED) else MODE_BASIC

    def _show_summary_for(self, result: SearchResult) -> None:
        """按当前模式整理并展示。

        basic：同步离线整理（与 v0.17 完全一致，速度不变）；
        enhanced：后台线程执行（LLM 可能 5-30s），期间按钮 loading；
          未配置时直接展示降级提示，不启动线程、不阻塞搜索。
        """
        mode = self._summary_mode()
        direction = (self._last_query_info or {}).get("research_direction", "")
        if mode == MODE_ENHANCED:
            if not self._summary_service.enhanced_configured:
                # v0.18 Phase 2.8-A：未配置 → 提示并引导打开设置（不崩溃）
                self._prompt_enhanced_not_configured()
                return
            if self._summary_busy:
                return  # single-flight：增强分析进行中忽略再次点击
            self._summary_busy = True
            self._pending_enhanced_result = result
            self.btn_summary.setEnabled(False)
            self._set_status("正在增强分析...")
            self.enhanced_requested.emit(result, direction,
                                         self._research_profile)
            return
        summary = self._summary_service.summarize(
            mode, result, user_research_direction=direction,
            research_profile=self._research_profile)
        # v0.18 Phase 2.9-B：基础整理完成后回写论文库（若已收藏）
        self._sync_basic_to_library(result, summary)
        dialog = SummaryDialog(result, summary, self, mode=mode)
        dialog.exec()

    def _prompt_enhanced_not_configured(self) -> None:
        """增强模式未配置：提示 + 提供“打开设置”入口（不崩溃）。"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("AI增强分析")
        box.setText("AI增强分析尚未配置，请前往设置完成配置。")
        open_settings = box.addButton("打开设置", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_settings:
            self._on_settings()

    def _on_enhanced_ready(self, summary_dict: dict) -> None:
        """增强分析成功：展示 EnhancedSummary（论文库重分析走另一分支）。"""
        from tju_info_retrieval.models.enhanced_summary import EnhancedSummary

        result = self._pending_enhanced_result
        if result is None:
            self._set_status("增强分析完成（当前列表已变更）")
            self._pending_library_record_id = None
            return
        summary = EnhancedSummary.from_dict(summary_dict)

        record_id = self._pending_library_record_id
        self._pending_library_record_id = None
        if record_id:
            # 论文库"重新AI分析"：降级结果不算成功（§三十五 保留原结果）
            if not summary.ai_generated:
                self._set_status("重新分析失败，已保留原结果。")
                QMessageBox.information(
                    self, "提示", "重新分析失败，已保留原结果。")
                return
            self._write_library_analysis(record_id, result, summary)
            self._refresh_library_window()
            self._set_status("重新分析完成，论文库记录已更新。")
            return

        # v0.18 Phase 2.9-B：结果区增强分析完成后回写论文库（若已收藏）
        self._sync_enhanced_to_library(result, summary)
        dialog = SummaryDialog(result, summary, self, mode=MODE_ENHANCED)
        dialog.exec()
        self._pending_enhanced_result = None

    def _on_enhanced_failed(self, message: str) -> None:
        """增强分析失败：用户提示，不崩溃；论文库记录保留原结果。"""
        self._pending_enhanced_result = None
        was_library = self._pending_library_record_id is not None
        self._pending_library_record_id = None
        if was_library:
            self._set_status("重新分析失败，已保留原结果。")
            QMessageBox.information(
                self, "提示", "重新分析失败，已保留原结果。")
            return
        self._set_status(f"增强分析失败：{message}")
        QMessageBox.information(self, "提示", message)

    def _write_library_analysis(self, record_id: str, result, summary) -> None:
        """把 AI 分析结果写入指定论文库记录（含 provider/model/版本元信息）。"""
        from tju_info_retrieval.models.library import LibraryRecord

        holder = LibraryRecord()
        self._apply_analysis_meta(holder, summary)
        self._library_store.update(
            record_id,
            enhanced_summary=summary,
            enhanced_provider=holder.enhanced_provider,
            enhanced_model=holder.enhanced_model,
            enhanced_prompt_version=holder.enhanced_prompt_version,
            enhanced_profile_version=holder.enhanced_profile_version,
        )

    def _on_enhanced_finished(self) -> None:
        self._summary_busy = False
        self.btn_summary.setEnabled(True)

    def _on_evidence_ready(self, abstract: str) -> None:
        """摘要获取成功：写回内存行 → 按当前模式整理 → 展示。"""
        row = self._pending_summary_row
        self._pending_summary_row = None
        if row is not None and abstract:
            row["abstract"] = abstract  # 内存更新（正式字段；不写持久层）
        if row is not None and row in self._results:
            result = SearchResult.from_dict(row)
            self._show_summary_for(result)
        else:
            self._set_status("摘要已获取（当前列表已变更，未展示整理结果）")

    def _on_evidence_failed(self, message: str) -> None:
        """摘要获取失败：用户提示，不做任何整理展示。"""
        self._pending_summary_row = None
        self._set_status(f"摘要获取失败：{message}")
        QMessageBox.information(self, "提示", message)

    def _on_evidence_finished(self) -> None:
        self._evidence_busy = False
        self.btn_summary.setEnabled(True)

    def _on_explanation(self) -> None:
        """查看当前选中论文的推荐理由（v0.12 Phase 2）。"""
        sel = self.table.currentRow()
        if sel < 0 or sel >= len(self._results):
            QMessageBox.information(self, "提示", "请先在表格中选择一条结果。")
            return
        result = self._results[sel]
        explanation = self._explanation_for(result)
        dialog = ExplanationDialog(result, explanation, self)
        dialog.exec()

    def _on_about(self) -> None:
        features = "\n".join(f"- {f}" for f in FEATURES)
        sources = "、".join(SUPPORTED_SOURCES)
        status_line = test_report.current_test_status()
        QMessageBox.about(
            self,
            f"关于 {APP_NAME}",
            f"<b>{APP_NAME}</b> v{VERSION}<br><br>"
            f"<b>支持数据源</b><br>{sources}<br><br>"
            f"<b>功能</b><br>{features.replace(chr(10), '<br>')}<br><br>"
            f"<b>测试状态</b><br>{status_line}",
        )

    def _refresh_summary_service(self) -> None:
        """按 APIConfigManager 最新配置重建增强 Provider（basic 不变）。"""
        from tju_info_retrieval.services.summary_service import SummaryService

        self._summary_service = SummaryService(
            config=self._api_config.provider_config())
        # 后台 worker 复用同一 service 引用（替换内部 provider）
        self._summary_worker._service = self._summary_service

    def _on_settings(self) -> None:
        """打开 AI 增强分析设置；保存后刷新增强 Provider。"""
        from tju_info_retrieval.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(manager=self._api_config, parent=self)
        dialog.exec()
        self._refresh_summary_service()
        if self._api_config.is_configured():
            self._set_status("AI 增强分析已配置，可在结果区切换 AI增强分析 使用。")
        else:
            self._set_status("AI 增强分析未配置（可随时在“设置”中完成配置）")

    def _on_load_demo(self) -> None:
        demo = load_demo_results()
        raw = demo["results"]
        merged = ResultMerger.merge([SearchResult.from_dict(r) for r in raw])
        ranked = RankingService.rank(
            merged,
            QueryRequest(research_direction=demo["query_info"].get("research_direction", "")),
        )
        self._results = [r.to_dict() for r in ranked]
        self._last_query_info = dict(demo["query_info"])
        self._populate_table(self._results)
        self.btn_detail.setEnabled(True)
        self.btn_explanation.setEnabled(True)
        self.btn_summary.setEnabled(True)
        self.btn_ieee_original.setEnabled(True)
        self.btn_favorite.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.btn_report.setEnabled(True)
        self._set_status(
            f"演示数据已加载：原始 {len(raw)} 条，去重排序后 {len(self._results)} 条"
            "（未发起真实检索）"
        )

    # ---------- 辅助 ----------
    def _populate_table(self, results: list[dict]) -> None:
        from tju_info_retrieval.models.artifact import ARTIFACT_LABELS

        self.table.setRowCount(len(results))
        for r, row in enumerate(results):
            self.table.setItem(r, 0, QTableWidgetItem(row.get("title") or ""))
            self.table.setItem(r, 1, QTableWidgetItem("；".join(row.get("authors") or [])))
            self.table.setItem(r, 2, QTableWidgetItem(row.get("source") or ""))
            self.table.setItem(r, 3, QTableWidgetItem(row.get("year") or ""))
            # v0.17 Phase 2.1：多类型展示——专利/新闻在类型列显示成果类型
            # 标签（如"专利"/"新闻"），论文保持原 document_type（旧数据不变）
            artifact_type = row.get("artifact_type") or ""
            type_text = ARTIFACT_LABELS.get(artifact_type)
            self.table.setItem(
                r, 4,
                QTableWidgetItem(
                    type_text if type_text and artifact_type != "paper"
                    else (row.get("document_type") or "")
                ),
            )
            self.table.setItem(r, 5, QTableWidgetItem(row.get("database") or ""))
        self._update_empty_state()

    def _update_empty_state(self) -> None:
        """空态插画随行数切换：0 行显示，>0 行隐藏。"""
        self._empty_overlay.setVisible(self.table.rowCount() == 0)

    def _status_color(self, text: str) -> str:
        if any(k in text for k in ("失败", "错误", "无法", "需要")):
            return theme.COLORS["red"]
        if any(k in text for k in ("正常", "完成", "成功", "已")):
            return theme.COLORS["green"]
        return theme.COLORS["primary"]

    def _set_status(self, text: str) -> None:
        self.label_status.setText(f"状态：{text}")
        color = self._status_color(text)
        self.status_icon.setPixmap(theme.pixmap_painted("shield", color, 17))
        self.label_status.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 600;"
        )

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        # v0.13 生命周期修复：先请求等待中的 worker slot 提前返回
        # （登录等待循环检查该标志并同栈释放会话），再走既有退出信号。
        # worker 线程内所有会话由所属 slot 的 finally 同栈释放，
        # shutdown slot 仅兜底，不触碰跨 slot 残留 Playwright 对象。
        self._worker.request_stop()
        self.shutdown_requested.emit()
        self._detail_manager.shutdown_all()
        self._thread.quit()
        self._thread.wait(5000)
        # v0.17 2.4-C-B：摘要 worker 线程清理（避免 QThread destroyed while running）
        self._evidence_thread.quit()
        self._evidence_thread.wait(3000)
        # v0.18 2.7-B：增强分析 worker 线程清理（请求取消后退出）
        self._summary_worker.request_cancel()
        self._summary_thread.quit()
        self._summary_thread.wait(3000)
        # v0.18 Phase 2.9-C-E：调研报告 worker 线程清理（安全停止）
        self._close_report_progress()
        self._report_thread.quit()
        self._report_thread.wait(3000)
        super().closeEvent(event)
