"""推荐理由对话框（v0.12 Phase 2）。

展示单条检索结果的评分拆解与推荐理由。
数据由 RankingService.rank_with_explanation() 生成（与排序同一套逻辑），
本对话框只负责渲染，不重复计算评分。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tju_info_retrieval.models.ranking import RankingExplanation

# 评分拆解行：显示名 → RankingExplanation 字段
_BREAKDOWN_ROWS = (
    ("相关性", "relevance_score"),
    ("年份", "year_score"),
    ("引用", "citation_score"),
    ("来源", "database_score"),
)


class ExplanationDialog(QDialog):
    """推荐理由展示窗口：标题 + 综合评分 + 评分拆解 + reasons 列表。"""

    def __init__(
        self,
        result: dict,
        explanation: RankingExplanation,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("推荐理由")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        self.title_label = QLabel(result.get("title") or "（无标题）", self)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        meta_parts = [
            str(part)
            for part in (result.get("database"), result.get("source"), result.get("year"))
            if part
        ]
        if meta_parts:
            self.meta_label = QLabel(" · ".join(meta_parts), self)
            layout.addWidget(self.meta_label)

        self.total_label = QLabel(f"综合评分：{explanation.total_score:.1f} 分", self)
        layout.addWidget(self.total_label)

        form = QFormLayout()
        self.score_labels: dict[str, QLabel] = {}
        for label, attr in _BREAKDOWN_ROWS:
            row = QLabel(f"{getattr(explanation, attr):.1f} 分", self)
            self.score_labels[label] = row
            form.addRow(QLabel(label, self), row)
        layout.addLayout(form)

        self.reasons_list = QListWidget(self)
        for reason in explanation.reasons:
            self.reasons_list.addItem(reason)
        layout.addWidget(self.reasons_list)

        close_btn = QPushButton("关闭", self)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
