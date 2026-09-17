"""报告生成上下文（v0.18 Phase 2.9-C-C-C）。

`ReportContext` 是 Provider 的**唯一输入**，与单篇分析的 `SummaryContext`
完全独立（不共用、不继承、不互相引用）。

设计约束：

1. **papers 必须是快照**：只接受 `ReportSource`（生成时快照），
   **禁止传入 `LibraryRecord` 实例**（论文库对象生命周期与报告无关，
   直接传实例会让报告随库变化而变，破坏"报告可独立阅读"）。
2. **不依赖论文库**：本模块不 import `models.library` / `LibraryStore`；
   快照转换由 service 层完成（`services/report_service.py`）。
3. **不依赖单篇分析模型**：不 import `EnhancedSummary`；
   既有分析文本以普通字符串/字典进入 `source_materials`。

Provider 可用它组装 Prompt（2.9-C-D），也可用于降级基础报告。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from tju_info_retrieval.models.research_report import ReportSource

# 非快照对象类型名黑名单：命中即拒绝（防止误传 ORM/库记录实例）
_FORBIDDEN_PAPER_TYPES = ("LibraryRecord", "SearchResult")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReportContext:
    """报告生成输入（论文快照集合 + 画像快照 + 生成方式）。"""

    papers: list[ReportSource] = field(default_factory=list)
    profile_snapshot: dict = field(default_factory=dict)
    generation_scope: dict = field(default_factory=dict)
    created_time: str = field(default_factory=_now_iso)
    # 每篇论文的**已存在**材料（论文库里已有的分析文本等；不含新调用结果）
    source_materials: dict = field(default_factory=dict)
    # 标题提示（用户输入或自动生成；Provider 可参考，不强制）
    title_hint: str = ""

    def __post_init__(self) -> None:
        self.validate_papers()

    # ---------- 校验 ----------

    def validate_papers(self) -> None:
        """确保 papers 全为 `ReportSource` 快照（拒绝库对象实例）。"""
        for index, paper in enumerate(self.papers or []):
            type_name = type(paper).__name__
            if type_name in _FORBIDDEN_PAPER_TYPES:
                raise ValueError(
                    f"ReportContext.papers[{index}] 不得传入 {type_name} 实例；"
                    "请先转换为 ReportSource 快照（见 "
                    "services/report_service.report_source_from_record）")
            if not isinstance(paper, ReportSource):
                raise ValueError(
                    f"ReportContext.papers[{index}] 必须是 ReportSource，"
                    f"实际为 {type_name}")

    # ---------- 便捷属性 ----------

    @property
    def paper_count(self) -> int:
        return len(self.papers or [])

    def paper_ids(self) -> list[str]:
        return [p.paper_id for p in self.papers if p.paper_id]

    def paper(self, paper_id: str) -> ReportSource | None:
        return next((p for p in self.papers if p.paper_id == paper_id), None)

    def known_paper_ids(self) -> set[str]:
        return {p.paper_id for p in self.papers if p.paper_id}

    def materials_for(self, paper_id: str) -> dict:
        return dict((self.source_materials or {}).get(paper_id) or {})

    def profile_version(self) -> str:
        value = (self.profile_snapshot or {}).get("profile_version")
        return "" if value in (None, "") else str(value)

    def profile_is_configured(self) -> bool:
        """画像是否已配置（领域/方向/关键词任一非空）。"""
        snapshot = self.profile_snapshot or {}
        area = str(snapshot.get("research_area") or "").strip()
        subs = [s for s in (snapshot.get("sub_direction") or []) if str(s).strip()]
        keys = [k for k in (snapshot.get("keywords") or []) if str(k).strip()]
        return bool(area or subs or keys)

    def profile_summary_text(self) -> str:
        """画像快照的可读文本（无画像时给固定措辞）。"""
        from tju_info_retrieval.models.research_profile import (
            RESEARCH_STAGE_LABELS,
            ResearchProfile,
        )

        if not self.profile_is_configured():
            return "尚未设置科研画像，无法进行个性化方向匹配。"
        try:
            return ResearchProfile.from_dict(self.profile_snapshot).context_summary()
        except Exception:  # noqa: BLE001 - 快照异常不阻塞报告
            return "尚未设置科研画像，无法进行个性化方向匹配。"

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return {
            "papers": [p.to_dict() for p in self.papers],
            "profile_snapshot": dict(self.profile_snapshot),
            "generation_scope": dict(self.generation_scope),
            "created_time": self.created_time,
            "source_materials": {k: dict(v or {})
                                 for k, v in (self.source_materials or {}).items()},
            "title_hint": self.title_hint,
        }

    @classmethod
    def from_dict(cls, data) -> "ReportContext":
        data = data if isinstance(data, dict) else {}
        papers_raw = data.get("papers")
        materials_raw = data.get("source_materials")
        return cls(
            papers=[ReportSource.from_dict(x)
                    for x in papers_raw if isinstance(x, dict)]
            if isinstance(papers_raw, list) else [],
            profile_snapshot=dict(data.get("profile_snapshot") or {})
            if isinstance(data.get("profile_snapshot"), dict) else {},
            generation_scope=dict(data.get("generation_scope") or {})
            if isinstance(data.get("generation_scope"), dict) else {},
            created_time=str(data.get("created_time") or ""),
            source_materials={str(k): dict(v)
                              for k, v in materials_raw.items()
                              if isinstance(v, dict)}
            if isinstance(materials_raw, dict) else {},
            title_hint=str(data.get("title_hint") or ""),
        )
