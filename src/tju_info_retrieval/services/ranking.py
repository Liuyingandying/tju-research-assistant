"""轻量结果排序服务（无 AI / 向量 / embedding）。

评分优先级：标题关键词匹配（主）→ 年份新旧（次）→ 引用影响力（轻量）→ 数据库权重（最轻）。

v0.11 Phase 3 排序模式（RankingMode）：
- COMPREHENSIVE 综合推荐（默认）：完整评分。
- RECENT 最新发表：优先年份，综合分次之。
- CITATION 高引用：优先引用影响力，综合分次之。

v0.11 Phase 3 年份筛选（year_range）：
- 0=不限；5=近5年；10=近10年。
- 过滤在评分前执行；年份缺失/无法解析的结果在启用筛选时被排除（不做猜测）。

v0.12 Phase 1 可解释排序：
- rank_with_explanation() 返回与 rank() 相同的排序结果 + 每条结果的
  RankingExplanation 评分拆解（与 rank() 共用同一套评分/排序管线，不复制公式）。
- reasons 按实际评分生成事实性说明；无贡献的分项不生成原因。
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Iterable, NamedTuple

from tju_info_retrieval.models.query import QueryRequest, RankingMode
from tju_info_retrieval.models.ranking import RankingExplanation
from tju_info_retrieval.models.result import SearchResult


class _ScoredEntry(NamedTuple):
    """单条结果的评分拆解（排序与解释共用的内部结构）。"""

    relevance: float
    year_component: float
    citation: float
    database: float
    total: float
    year_value: int
    index: int
    result: SearchResult


class RankingService:
    """对融合后的 SearchResult 排序并重新生成 rank。"""

    TITLE_MATCH_SCORE = 60.0
    MAX_YEAR_SCORE = 25.0
    CITATION_WEIGHT = 10.0  # log10(citation+1) 最大约 10 分（当 citation≈9999999）
    DATABASE_WEIGHTS: dict[str, float] = {
        "IEEE Xplore": 5.0,
        "CNKI": 3.0,
        "万方": 2.0,
    }

    @classmethod
    def rank(
        cls,
        results: Iterable[SearchResult],
        query: QueryRequest,
    ) -> list[SearchResult]:
        entries = cls._rank_entries(results, query)
        ranked = [entry.result for entry in entries]
        for i, result in enumerate(ranked, 1):
            result.rank = i
        return ranked

    @classmethod
    def rank_with_explanation(
        cls,
        results: Iterable[SearchResult],
        query: QueryRequest,
    ) -> tuple[list[SearchResult], list[RankingExplanation]]:
        """与 rank() 同一排序结果，并为每条结果附评分拆解。"""
        entries = cls._rank_entries(results, query)
        ranked: list[SearchResult] = []
        explanations: list[RankingExplanation] = []
        for i, entry in enumerate(entries, 1):
            entry.result.rank = i
            ranked.append(entry.result)
            explanations.append(cls._build_explanation(entry))
        return ranked, explanations

    # ---------- 内部共享管线（rank / rank_with_explanation 共用） ----------

    @classmethod
    def _rank_entries(
        cls,
        results: Iterable[SearchResult],
        query: QueryRequest,
    ) -> list[_ScoredEntry]:
        items = list(results)
        if not items:
            return []

        items = cls._apply_year_filter(items, query)
        if not items:
            return []

        terms = cls._query_terms(query.research_direction)
        mode = getattr(query, "ranking_mode", RankingMode.COMPREHENSIVE.value)
        entries = [cls._entry(result, terms, index) for index, result in enumerate(items)]

        if mode == RankingMode.RECENT.value:
            # 最新发表：年份优先，综合分次之，原始顺序兜底。
            entries.sort(key=lambda e: (-e.year_value, -e.total, e.index))
        elif mode == RankingMode.CITATION.value:
            # 高引用（v0.16 Phase B 语义收紧）：引用优先，综合分次之，
            # 原始顺序兜底。citation_count=None（未知）恒排所有"已知
            # citation"（含 0=明确零引用）之后，不与 0 等价。
            entries.sort(
                key=lambda e: (
                    1 if e.result.citation_count is None else 0,
                    -e.citation,
                    -e.total,
                    e.index,
                )
            )
        else:
            # 综合推荐（默认）：总分降序，年份降序，原始顺序兜底。
            entries.sort(key=lambda e: (-e.total, -e.year_value, e.index))
        return entries

    @classmethod
    def _entry(
        cls,
        result: SearchResult,
        terms: list[str],
        index: int,
    ) -> _ScoredEntry:
        relevance = cls._relevance_component(result, terms)
        year_component = cls._year_component(result)
        citation = cls._citation_score(result.citation_count)
        database = cls._database_component(result)
        total = relevance + year_component + citation + database
        return _ScoredEntry(
            relevance=relevance,
            year_component=year_component,
            citation=citation,
            database=database,
            total=total,
            year_value=cls._year_value(result.year),
            index=index,
            result=result,
        )

    # ---------- 评分分项（总分 = 各分项之和，加总顺序即原 _score 顺序） ----------

    @classmethod
    def _relevance_component(cls, result: SearchResult, terms: list[str]) -> float:
        title = cls._normalize(result.title)
        if not terms:
            return 0.0
        per_term = cls.TITLE_MATCH_SCORE / len(terms)
        score = 0.0
        for term in terms:
            if cls._normalize(term) in title:
                score += per_term
        return score

    @classmethod
    def _year_component(cls, result: SearchResult) -> float:
        year = cls._year_value(result.year)
        if year >= 2000:
            return min(max(year - 2000, 0), cls.MAX_YEAR_SCORE)
        return 0.0

    @classmethod
    def _database_component(cls, result: SearchResult) -> float:
        return cls.DATABASE_WEIGHTS.get(result.database, 0.0)

    @classmethod
    def _score(cls, result: SearchResult, terms: list[str]) -> float:
        """综合评分（各分项之和，与 _entry.total 一致；保留供内部引用）。"""
        return cls._entry(result, terms, 0).total

    @classmethod
    def _apply_year_filter(
        cls,
        items: list[SearchResult],
        query: QueryRequest,
    ) -> list[SearchResult]:
        """按 year_range 过滤（0=不过滤）。

        启用筛选时，年份缺失或无法解析的结果被排除。
        """
        year_range = getattr(query, "year_range", 0) or 0
        if year_range <= 0:
            return items
        threshold = datetime.now().year - year_range
        return [r for r in items if cls._year_value(r.year) >= threshold]

    @staticmethod
    def _citation_score(citation_count: int | None) -> float:
        """基于引用量的对数评分。

        公式：log10(citation_count + 1) * CITATION_WEIGHT
        - citation_count=None（未知）→ 不加分
        - citation_count=0 → 0 分
        - citation_count=9 → 10 分
        - citation_count=99 → 约 13.01 分
        """
        if citation_count is None:
            return 0.0
        if not isinstance(citation_count, int):
            return 0.0
        return math.log10(citation_count + 1) * RankingService.CITATION_WEIGHT

    # ---------- 解释生成（仅基于实际评分分项，不虚构原因） ----------

    @classmethod
    def _build_explanation(cls, entry: _ScoredEntry) -> RankingExplanation:
        result = entry.result
        reasons: list[str] = []

        if entry.relevance > 0:
            if entry.relevance >= cls.TITLE_MATCH_SCORE - 1e-9:
                reasons.append("标题高度匹配检索词")
            else:
                reasons.append("标题部分匹配检索词")

        if entry.year_component > 0:
            year = entry.year_value
            current_year = datetime.now().year
            if year >= current_year - 5:
                reasons.append("发表于近5年")
            elif year >= current_year - 10:
                reasons.append("发表于近10年")
            else:
                reasons.append(f"发表于{year}年")

        if entry.citation > 0:
            count = result.citation_count
            if count >= 100:
                reasons.append(f"引用量较高（被引{count}次）")
            else:
                reasons.append(f"被引{count}次")

        if entry.database > 0:
            reasons.append(f"{result.database}来源")

        return RankingExplanation(
            total_score=entry.total,
            relevance_score=entry.relevance,
            year_score=entry.year_component,
            citation_score=entry.citation,
            database_score=entry.database,
            reasons=reasons,
        )

    # ---------- 基础工具 ----------

    @staticmethod
    def _query_terms(direction: str) -> list[str]:
        return [term for term in re.split(r"\s+", (direction or "").strip()) if term]

    @staticmethod
    def _normalize(text: str | None) -> str:
        if not text:
            return ""
        return re.sub(r"[\s\u3000]+", "", text).lower()

    @staticmethod
    def _year_value(year: str | None) -> int:
        if not year:
            return 0
        match = re.search(r"\b((?:19|20)\d{2})\b", str(year))
        return int(match.group(1)) if match else 0
