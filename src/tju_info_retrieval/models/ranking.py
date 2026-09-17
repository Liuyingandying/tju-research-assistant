"""可解释排序数据模型 RankingExplanation（v0.12 Phase 1）。

由 RankingService.rank_with_explanation() 生成，描述单条结果的评分拆解。
不影响 SearchResult：解释数据独立于结果模型，仅通过返回值组合传递。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RankingExplanation:
    """单条检索结果的评分拆解。

    - total_score = relevance_score + year_score + citation_score + database_score
      （与 RankingService._score 同一套逻辑）。
    - reasons 为按实际评分生成的事实性说明，无贡献的分项不生成原因；
      citation_count=None（未知）时不生成引用原因。
    """

    total_score: float
    relevance_score: float
    year_score: float
    citation_score: float
    database_score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_score": self.total_score,
            "relevance_score": self.relevance_score,
            "year_score": self.year_score,
            "citation_score": self.citation_score,
            "database_score": self.database_score,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RankingExplanation":
        return cls(
            total_score=float(data.get("total_score") or 0.0),
            relevance_score=float(data.get("relevance_score") or 0.0),
            year_score=float(data.get("year_score") or 0.0),
            citation_score=float(data.get("citation_score") or 0.0),
            database_score=float(data.get("database_score") or 0.0),
            reasons=list(data.get("reasons") or []),
        )
