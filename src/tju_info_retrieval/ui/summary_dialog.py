"""关键信息整理结果对话框（v0.18 Phase 2.7-A 双模式）。

basic 模式：展示 StructuredSummary 四字段 + 来源（与 v0.17 完全一致）；
enhanced 模式：展示 EnhancedSummary 六解释字段 + AI 标识 + 免责声明。

两模式字段分区展示，禁止混合；未配置增强 Provider 时展示固定提示文案。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.models.summary import StructuredSummary

DISCLAIMER_TEXT = (
    "以上内容由系统根据当前可获取的摘要、正文及元数据自动整理，"
    "请结合原始资料核对。"
)
ENHANCED_DISCLAIMER_TEXT = (
    "以上增强分析由 AI 辅助生成（Provider: %s），仅供参考，"
    "请结合原文与基础整理结果核对，不构成权威结论。"
)
ENHANCED_NOT_CONFIGURED_TEXT = "增强分析服务未配置"


class SummaryDialog(QDialog):
    """关键信息整理展示（basic 四字段 / enhanced 六字段）。"""

    def __init__(self, result: SearchResult, summary,
                 parent=None, mode: str = "basic") -> None:
        super().__init__(parent)
        self._mode = mode
        self.setWindowTitle(
            "AI深度分析" if mode == "enhanced" else "自动关键信息整理")
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(8)

        title = QLabel(result.title or "（无标题）")
        title.setObjectName("summaryTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        meta_parts = []
        artifact_type = getattr(result, "artifact_type", "paper") or "paper"
        meta_parts.append(f"成果类型：{artifact_type}")
        media = (result.artifact_metadata or {}).get("media")
        if media:
            meta_parts.append(f"媒体：{media}")
        if result.source:
            meta_parts.append(f"来源：{result.source}")
        if mode == "enhanced":
            provider = getattr(summary, "provider", "") or ""
            meta_parts.append(f"Provider：{provider or '未配置'}")
            # v0.18 Phase 2.8-C：文献类型自适应显示
            doc_type = str(getattr(summary, "document_type", "") or "")
            if doc_type:
                from tju_info_retrieval.models.enhanced_summary import (
                    DOCUMENT_TYPE_LABELS,
                )

                type_label = DOCUMENT_TYPE_LABELS.get(doc_type, doc_type)
                meta_parts.append(f"文献类型：{type_label}")
            # v0.18 Phase 2.9-A：方向匹配等级（不显示完整 Profile）
            match_level = str(
                getattr(summary, "direction_match_level", "") or "")
            if match_level:
                from tju_info_retrieval.models.research_profile import (
                    DIRECTION_MATCH_LABELS,
                )

                match_label = DIRECTION_MATCH_LABELS.get(
                    match_level, match_level)
                meta_parts.append(f"方向匹配：{match_label}")
        meta = QLabel("  |  ".join(meta_parts))
        meta.setObjectName("summaryMeta")
        layout.addWidget(meta)

        line = QFrame(self)
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("summaryDivider")
        layout.addWidget(line)

        if mode == "enhanced":
            self._render_enhanced(layout, summary)
        else:
            self._render_basic(layout, summary)

        layout.addStretch(1)
        disclaimer = QLabel(
            ENHANCED_DISCLAIMER_TEXT % (getattr(summary, "provider", "") or "未知")
            if mode == "enhanced" and getattr(summary, "ai_generated", False)
            else DISCLAIMER_TEXT
        )
        disclaimer.setObjectName("summaryDisclaimer")
        disclaimer.setWordWrap(True)
        layout.addWidget(disclaimer)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close = QPushButton("关闭")
        close.setObjectName("btnSecondary")
        close.clicked.connect(self.accept)
        btn_row.addWidget(close)
        layout.addLayout(btn_row)

    def _render_basic(self, layout: QVBoxLayout, summary: StructuredSummary) -> None:
        """基础模式：四字段 + 来源（与 v0.17 一致）。"""
        for key, label, value in summary.items():
            field_label = QLabel(f"{label}")
            field_label.setObjectName("summaryFieldLabel")
            layout.addWidget(field_label)
            value_label = QLabel(value)
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            value_label.setObjectName("summaryFieldValue")
            layout.addWidget(value_label)

    def _render_enhanced(self, layout: QVBoxLayout, summary) -> None:
        """增强模式：六解释字段（label 按文献类型自适应；未配置显示固定提示）。"""
        # v0.18 Phase 2.8-C：按 document_type 自适应字段标题
        # （review：技术路线→技术脉络、创新点→综述贡献；
        #   perspective：论证结构/核心观点），数据字段复用。
        doc_type = str(getattr(summary, "document_type", "") or "")
        from tju_info_retrieval.models.enhanced_summary import FIELD_LABELS_BY_TYPE

        type_labels = FIELD_LABELS_BY_TYPE.get(doc_type, {})
        for key, label, value in summary.items():
            field_label = QLabel(type_labels.get(key, label))
            field_label.setObjectName("summaryFieldLabel")
            layout.addWidget(field_label)
            value_label = QLabel(value)
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            value_label.setObjectName("summaryFieldValue")
            layout.addWidget(value_label)