"""结构化关键信息整理结果模型（v0.17 Phase 2.4-B-OFFLINE）。

四字段最小闭环；source_basis 记录各字段证据来源类型
（summary / abstract / detail_content / metadata），供可信性追溯。
"""
from __future__ import annotations

from dataclasses import dataclass, field

SUMMARY_FIELDS = (
    "research_content",
    "core_technology",
    "main_results",
    "application_value",
)

SUMMARY_FIELD_LABELS = {
    "research_content": "研究内容",
    "core_technology": "核心技术",
    "main_results": "主要成果",
    "application_value": "应用价值",
}


@dataclass
class StructuredSummary:
    """离线/未来 provider 共用的结构化整理结果。"""

    research_content: str = ""
    core_technology: str = ""
    main_results: str = ""
    application_value: str = ""
    # 字段 → 证据来源类型（summary/abstract/detail_content/metadata）
    source_basis: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "research_content": self.research_content,
            "core_technology": self.core_technology,
            "main_results": self.main_results,
            "application_value": self.application_value,
            "source_basis": dict(self.source_basis),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StructuredSummary":
        data = data or {}
        return cls(
            research_content=str(data.get("research_content") or ""),
            core_technology=str(data.get("core_technology") or ""),
            main_results=str(data.get("main_results") or ""),
            application_value=str(data.get("application_value") or ""),
            source_basis=dict(data.get("source_basis") or {}),
        )

    def items(self) -> list[tuple[str, str, str]]:
        """[(key, 中文标签, 值)]，按固定字段顺序。"""
        return [
            (key, SUMMARY_FIELD_LABELS[key], getattr(self, key))
            for key in SUMMARY_FIELDS
        ]