"""AI 增强分析设置对话框（v0.18 Phase 2.8-A / 2.8-B）。

普通用户一键配置：服务类型 / API 地址 / 模型 / API Key（隐藏显示）/
测试连接（后台）/ 保存配置 / 清除 Key。

- 配置与凭据经 APIConfigManager：keyring 优先，DPAPI 加密文件降级，
  均不可用仅本次临时使用（memory-only）——任何路径不落明文；
- 已保存凭据不回显到输入框，仅显示“已保存凭据”状态；
- 测试连接在独立 QThread 执行，不阻塞主线程；
- 日志与界面均不输出真实 Key。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from tju_info_retrieval.services.api_config_manager import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    SERVICE_OPTIONS,
    APIConfigManager,
)
from tju_info_retrieval.services.api_connection_test import test_connection
from tju_info_retrieval.services.ai_provider_registry import (
    default_api_key_env,
    default_base_url,
    default_model,
    normalize_provider,
)
from tju_info_retrieval.version import (
    CAPABILITIES,
    PRODUCT_NAME,
    RELEASE_LABEL,
)


class _ConnectionTester(QObject):
    """后台连接测试执行体（QThread 内运行）。"""

    finished_ok = Signal(dict)
    finished_err = Signal(dict)

    @Slot()
    def run(self, provider_cfg: dict) -> None:
        result = test_connection(provider_cfg=provider_cfg)
        if result.get("ok"):
            self.finished_ok.emit(result)
        else:
            self.finished_err.emit(result)


class SettingsDialog(QDialog):
    """AI 增强分析设置。"""

    def __init__(self, manager: APIConfigManager | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._manager = manager or APIConfigManager()
        self._test_thread: QThread | None = None
        self._test_worker: _ConnectionTester | None = None

        self.setWindowTitle("AI增强分析设置")
        self.resize(560, 460)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)

        title = QLabel("AI增强分析设置")
        title.setObjectName("cardSectionTitle")
        outer.addWidget(title)

        # v0.18 Phase 2.9-A：Tab 结构（AI 服务配置 / 科研画像）
        from PySide6.QtWidgets import QTabWidget

        self.tabs = QTabWidget(self)
        outer.addWidget(self.tabs, 1)

        # ---- Tab 1：AI 服务配置（既有内容） ----
        tab_ai = QWidget(self)
        layout = QVBoxLayout(tab_ai)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        # 服务类型
        layout.addWidget(QLabel("服务类型"))
        self.combo_service = QComboBox(tab_ai)
        for label, value in SERVICE_OPTIONS:
            self.combo_service.addItem(label, value)
        self.combo_service.currentIndexChanged.connect(self._on_service_changed)
        layout.addWidget(self.combo_service)

        # API 地址
        layout.addWidget(QLabel("API地址"))
        self.input_base_url = QLineEdit(tab_ai)
        self.input_base_url.setPlaceholderText(DEFAULT_BASE_URL)
        layout.addWidget(self.input_base_url)

        # 模型
        layout.addWidget(QLabel("模型"))
        self.input_model = QLineEdit(tab_ai)
        self.input_model.setPlaceholderText(DEFAULT_MODEL)
        layout.addWidget(self.input_model)

        # API Key（隐藏）
        layout.addWidget(QLabel("API Key"))
        key_row = QHBoxLayout()
        self.input_api_key = QLineEdit(tab_ai)
        self.input_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_api_key.setPlaceholderText("输入 API Key（将安全保存）")
        key_row.addWidget(self.input_api_key, 1)
        self.btn_toggle_key = QPushButton("显示", tab_ai)
        self.btn_toggle_key.setObjectName("btnGhost")
        self.btn_toggle_key.setCheckable(True)
        self.btn_toggle_key.clicked.connect(self._toggle_key_visible)
        key_row.addWidget(self.btn_toggle_key)
        layout.addLayout(key_row)

        line = QFrame(tab_ai)
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("summaryDivider")
        layout.addWidget(line)

        # 状态提示
        self.label_status = QLabel("", tab_ai)
        self.label_status.setObjectName("statusText")
        self.label_status.setWordWrap(True)
        layout.addWidget(self.label_status)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.btn_test = QPushButton("测试连接", tab_ai)
        self.btn_test.setObjectName("btnGhost")
        self.btn_test.clicked.connect(self._on_test)
        action_row.addWidget(self.btn_test)
        self.btn_save = QPushButton("保存", tab_ai)
        self.btn_save.setObjectName("btnSearchPrimary")
        self.btn_save.clicked.connect(self._on_save)
        action_row.addWidget(self.btn_save)
        self.btn_clear = QPushButton("清除凭据", tab_ai)
        self.btn_clear.setObjectName("btnGhost")
        self.btn_clear.clicked.connect(self._on_clear_key)
        action_row.addWidget(self.btn_clear)
        layout.addLayout(action_row)
        layout.addStretch(1)

        # ---- Tab 2：科研画像（2.9-A） ----
        tab_profile = QWidget(self)
        pl = QVBoxLayout(tab_profile)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(8)

        p_title = QLabel("我的科研方向")
        p_title.setObjectName("cardSectionTitle")
        pl.addWidget(p_title)

        # v0.18 Phase 2.9-B：新用户画像为空（不预置任何具体方向），
        # 提示完善画像的收益。
        self.label_profile_hint = QLabel(
            "完善科研画像后，可获得个性化方向匹配和阅读优先级建议。", tab_profile)
        self.label_profile_hint.setObjectName("summaryMeta")
        self.label_profile_hint.setWordWrap(True)
        pl.addWidget(self.label_profile_hint)

        pl.addWidget(QLabel("研究领域"))
        self.input_research_area = QLineEdit(tab_profile)
        self.input_research_area.setPlaceholderText("例如：太赫兹")
        pl.addWidget(self.input_research_area)

        pl.addWidget(QLabel("研究阶段"))
        self.combo_stage = QComboBox(tab_profile)
        from tju_info_retrieval.models.research_profile import (
            RESEARCH_STAGE_LABELS,
            RESEARCH_STAGES,
        )

        for stage in RESEARCH_STAGES:
            self.combo_stage.addItem(RESEARCH_STAGE_LABELS[stage], stage)
        pl.addWidget(self.combo_stage)

        pl.addWidget(QLabel("主要方向（逗号分隔）"))
        self.input_sub_direction = QLineEdit(tab_profile)
        self.input_sub_direction.setPlaceholderText("例如：THz-ISAC, THz sensing")
        pl.addWidget(self.input_sub_direction)

        pl.addWidget(QLabel("关注关键词（逗号分隔）"))
        self.input_keywords = QLineEdit(tab_profile)
        self.input_keywords.setPlaceholderText("例如：terahertz, THz, ISAC")
        pl.addWidget(self.input_keywords)

        pl.addWidget(QLabel("暂不关注（逗号分隔）"))
        self.input_excluded = QLineEdit(tab_profile)
        self.input_excluded.setPlaceholderText("例如：医学诊断")
        pl.addWidget(self.input_excluded)

        pl.addStretch(1)
        profile_btn_row = QHBoxLayout()
        profile_btn_row.addStretch(1)
        self.btn_profile_save = QPushButton("保存", tab_profile)
        self.btn_profile_save.setObjectName("btnSearchPrimary")
        self.btn_profile_save.clicked.connect(self._on_save_profile)
        profile_btn_row.addWidget(self.btn_profile_save)
        self.btn_profile_reset = QPushButton("恢复默认", tab_profile)
        self.btn_profile_reset.setObjectName("btnGhost")
        self.btn_profile_reset.clicked.connect(self._on_reset_profile)
        profile_btn_row.addWidget(self.btn_profile_reset)
        pl.addLayout(profile_btn_row)

        self.tabs.addTab(tab_ai, "AI 服务")
        self.tabs.addTab(tab_profile, "科研画像")

        # ---- Tab 3：版本 / 关于（v0.18 Phase 2.10-B） ----
        tab_version = QWidget(self)
        vl = QVBoxLayout(tab_version)

        label_product = QLabel(PRODUCT_NAME, tab_version)
        label_product.setObjectName("cardSectionTitle")
        vl.addWidget(label_product)

        self.label_version = QLabel(f"版本：{RELEASE_LABEL}", tab_version)
        vl.addWidget(self.label_version)

        label_caps_title = QLabel("能力列表", tab_version)
        label_caps_title.setStyleSheet("font-weight: 600; margin-top: 6px;")
        vl.addWidget(label_caps_title)

        self.label_capabilities = QLabel(
            "\n".join(f"· {c}" for c in CAPABILITIES), tab_version)
        self.label_capabilities.setWordWrap(True)
        vl.addWidget(self.label_capabilities)
        vl.addStretch(1)

        self.tabs.addTab(tab_version, "版本 / 关于")

        # 全局按钮行（关闭）
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_close = QPushButton("关闭", self)
        self.btn_close.setObjectName("btnGhost")
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)
        outer.addLayout(btn_row)

        self._load_current()
        self._load_profile()

    # ---------- 加载当前配置 ----------
    def _load_current(self) -> None:
        cfg = self._manager.load_config()
        provider = str(cfg.get("provider") or "")
        idx = 0
        for i, (_label, value) in enumerate(SERVICE_OPTIONS):
            if value == provider:
                idx = i
                break
        self.combo_service.setCurrentIndex(idx)
        self.input_base_url.setText(str(cfg.get("base_url") or DEFAULT_BASE_URL))
        self.input_model.setText(str(cfg.get("model") or DEFAULT_MODEL))
        # 安全（2.8-B）：不回填真实 Key 到输入框；已存凭据仅显示状态。
        has_key = bool(self._manager.get_api_key())
        if has_key:
            self.input_api_key.setPlaceholderText("已保存凭据（输入可覆盖）")
        if self._manager.is_configured():
            backend = str(cfg.get("credential_backend") or
                          self._manager.credential_backend)
            backend_label = {"keyring": "Windows 凭据管理器",
                             "dpapi": "DPAPI 加密文件",
                             "memory": "本次临时使用"}.get(backend, backend)
            self.label_status.setText(
                f"AI增强分析已配置（凭据存储：{backend_label}）。")
        elif has_key:
            self.label_status.setText(
                "已保存凭据，但配置未启用/不完整；填写并保存后即可使用。")
        else:
            self.label_status.setText("当前未配置。填写 API Key 并保存后即可使用。")

    def _on_service_changed(self, _index: int) -> None:
        """选择服务商后自动填充 base_url / model（custom 留空手动填写）。"""
        service = normalize_provider(self.combo_service.currentData())
        base = default_base_url(service)
        model = default_model(service)
        self.input_base_url.setText(base)
        self.input_model.setText(model)
        self.input_base_url.setPlaceholderText(base or DEFAULT_BASE_URL)
        self.input_model.setPlaceholderText(model or DEFAULT_MODEL)

    def _toggle_key_visible(self, checked: bool) -> None:
        self.input_api_key.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        self.btn_toggle_key.setText("隐藏" if checked else "显示")

    # ---------- 测试连接（后台） ----------
    def _on_test(self) -> None:
        if self._test_thread is not None and self._test_thread.isRunning():
            return  # single-flight
        cfg = self._provider_cfg_from_form(require_key=True)
        if cfg is None:
            return
        self.btn_test.setEnabled(False)
        self.label_status.setText("正在测试连接...")
        self._test_thread = QThread(self)
        self._test_worker = _ConnectionTester()
        self._test_worker.moveToThread(self._test_thread)
        self._test_thread.started.connect(
            lambda: self._test_worker.run(cfg))
        self._test_worker.finished_ok.connect(self._on_test_done)
        self._test_worker.finished_err.connect(self._on_test_done)
        self._test_worker.finished_ok.connect(self._test_thread.quit)
        self._test_worker.finished_err.connect(self._test_thread.quit)
        self._test_thread.finished.connect(self._test_thread.deleteLater)
        self._test_thread.start()

    def _on_test_done(self, result: dict) -> None:
        self.btn_test.setEnabled(True)
        if result.get("ok"):
            self.label_status.setText(
                f"连接成功\n模型: {result.get('model') or ''}\n"
                f"耗时: {result.get('latency_s')}s")
        else:
            label = result.get("label", "连接失败")
            detail = result.get("detail", "")
            self.label_status.setText(f"{label}：{detail}")

    def _provider_cfg_from_form(self, require_key: bool) -> dict | None:
        """由表单生成 provider 配置 dict（key 来自表单或已存凭据）。"""
        api_key = self.input_api_key.text().strip()
        if not api_key:
            api_key = self._manager.get_api_key()
        if require_key and not api_key:
            QMessageBox.information(
                self, "提示", "请先输入 API Key。")
            return None
        provider = normalize_provider(self.combo_service.currentData())
        return {
            "enabled": True,
            "provider": provider,
            "base_url": self.input_base_url.text().strip()
            or default_base_url(provider),
            "model": self.input_model.text().strip()
            or default_model(provider),
            "api_key_env": default_api_key_env(provider),
            "api_key": api_key,
        }

    # ---------- 保存 ----------
    def _on_save(self) -> None:
        api_key = self.input_api_key.text().strip()
        if api_key:
            backend = self._manager.save_api_key(api_key)
        else:
            backend = self._manager.credential_backend
        provider = normalize_provider(self.combo_service.currentData())
        self._manager.save_config({
            "enabled": True,
            "provider": provider,
            "base_url": self.input_base_url.text().strip()
            or default_base_url(provider),
            "model": self.input_model.text().strip()
            or default_model(provider),
            "api_key_env": default_api_key_env(provider),
        })
        self.input_api_key.clear()
        if backend == "memory":
            self.label_status.setText(
                "配置已保存。当前系统无法安全保存 API Key，本次仅临时使用。")
        else:
            self.label_status.setText("AI增强分析已配置。")

    # ---------- 清除 Key ----------
    def _on_clear_key(self) -> None:
        self._manager.clear_api_key()
        self.input_api_key.clear()
        self.label_status.setText("API Key 已清除。")
        QMessageBox.information(
            self, "提示", "API Key 已清除。需要时请重新填写并保存。")

    # ---------- 科研画像（v0.18 Phase 2.9-A） ----------
    def _load_profile(self) -> None:
        from tju_info_retrieval.services.research_profile_store import (
            ResearchProfileStore,
        )

        self._profile_store = ResearchProfileStore()
        profile = self._profile_store.load()
        self.input_research_area.setText(profile.research_area)
        idx = self.combo_stage.findData(profile.research_stage)
        self.combo_stage.setCurrentIndex(idx if idx >= 0 else 0)
        self.input_sub_direction.setText(", ".join(profile.sub_direction))
        self.input_keywords.setText(", ".join(profile.keywords))
        self.input_excluded.setText(", ".join(profile.excluded_topics))
        # 2.9-B：空画像提示（新用户首次进入）
        if not profile.is_configured():
            self.label_profile_hint.setText(
                "完善科研画像后，可获得个性化方向匹配和阅读优先级建议。")

    def _collect_profile(self):
        from tju_info_retrieval.models.research_profile import default_profile

        # 以已存画像为基底：保留 profile_version，save() 才会单调递增，
        # 否则第二次修改仍是同一版本 → 缓存 key 不变 → 旧 AI 分析不复算。
        profile = self._profile_store.load() or default_profile()
        # 2.9-B：不做任何方向兜底（禁止预置"太赫兹"等具体方向；
        # 留空表示用户尚未配置画像）。
        profile.research_area = self.input_research_area.text().strip()

        def split_csv(text: str) -> list[str]:
            return [t.strip() for t in text.split(",") if t.strip()]

        profile.sub_direction = split_csv(self.input_sub_direction.text())
        profile.keywords = split_csv(self.input_keywords.text())
        profile.excluded_topics = split_csv(self.input_excluded.text())
        profile.research_stage = (
            self.combo_stage.currentData() or profile.research_stage)
        return profile

    def _on_save_profile(self) -> None:
        profile = self._collect_profile()
        self._profile_store.save(profile)
        if profile.is_configured():
            self.label_profile_hint.setText(
                f"科研画像已保存（版本 p{profile.profile_version}）。"
                "增强分析将按该画像给出个性化方向匹配与阅读优先级。")
        else:
            self.label_profile_hint.setText(
                "科研画像已保存（当前为空）。"
                "完善科研画像后，可获得个性化方向匹配和阅读优先级建议。")

    def _on_reset_profile(self) -> None:
        profile = self._profile_store.reset_to_default()
        self._load_profile()
        self.label_profile_hint.setText(
            f"科研画像已清空为默认（版本 p{profile.profile_version}）。"
            "完善科研画像后，可获得个性化方向匹配和阅读优先级建议。")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if self._test_thread is not None and self._test_thread.isRunning():
            self._test_thread.quit()
            self._test_thread.wait(2000)
        super().closeEvent(event)