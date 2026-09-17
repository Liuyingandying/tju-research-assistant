"""v0.16 Phase B：高引用模式来源均衡 Top-K 选择器（纯逻辑，无浏览器依赖）。

目标（区别于全局 citation 排序）：result_count=N 时，按最终 sources 把
输出配额均分到各来源（复用 SearchService._allocate_counts，差值 ≤1），
各来源内部按被引降序取 Top quota；某来源缺额时从仍有剩余候选的来源
按 sources 顺序轮询回填，尽量满足 N。

排序语义（严格区分 None / 0）：
- citation_count 已知 → 按数值降序（0 = 明确零引用，参与降序）；
- citation_count = None（未知）→ 排在该来源所有"已知 citation"之后，
  绝不当作 0；
- 引用相同 → 现有综合 RankingService 总分作次级排序 → 原始顺序兜底；
- 整来源全部 None → 自动退化为按综合分排序，仍保留该来源 quota
  （不返回空、不引入网络行为）。

去重原则：上游 ResultMerger 已跨库去重，本选择器对已去重集合分组，
不为 quota 保留重复文献。
"""
from __future__ import annotations

from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.ranking import RankingService


def candidate_quota(output_quota: int, cap: int = 50) -> int:
    """CITATION 模式单源候选配额：max(3×output, output+10)，上限 cap。

    普通模式不使用本函数（保持既有 effective_candidate_count 行为，
    不增加网络请求）。
    """
    if output_quota <= 0:
        return 0
    return min(max(output_quota * 3, output_quota + 10), cap)


class CitationBalancedSelector:
    """来源均衡高引用选择：分组 → 组内排序 → Top quota → 缺额轮询回填。"""

    @classmethod
    def select(
        cls,
        results: list[SearchResult],
        query: QueryRequest,
        output_quotas: dict[str, int],
    ) -> list[SearchResult]:
        """从已去重/已过滤的 results 中选出来源均衡的最终集合。

        output_quotas：source → 输出配额（_allocate_counts 产物，含 0）；
        未出现在字典中的来源按 0 处理（不入选，避免越权扩源）。
        """
        target = sum(output_quotas.values())
        if target <= 0 or not results:
            return list(results)

        # 综合分（tie-break 用）：与 RankingService 共用同一评分管线
        terms = RankingService._query_terms(query.research_direction)
        totals: dict[int, float] = {}
        index: dict[int, int] = {}
        for i, r in enumerate(results):
            entry = RankingService._entry(r, terms, i)
            totals[id(r)] = entry.total
            index[id(r)] = i

        # 分组（保持 results 文档序）；不在配额表中的来源不参与
        groups: dict[str, list[SearchResult]] = {}
        for r in results:
            if r.database in output_quotas:
                groups.setdefault(r.database, []).append(r)

        selected: list[SearchResult] = []
        leftovers: dict[str, list[SearchResult]] = {}
        for source, quota in output_quotas.items():
            members = groups.get(source, [])
            ordered = sorted(
                members,
                key=lambda r: cls._sort_key(r, totals, index),
            )
            selected.extend(ordered[:quota])
            if len(ordered) > quota:
                leftovers[source] = ordered[quota:]

        # 缺额回填：按 sources 配额表顺序轮询，每次各补 1 条（确定性），
        # 直到补满 target 或候选耗尽。轮询只从"仍有剩余候选"的来源取。
        shortage = target - len(selected)
        if shortage > 0 and leftovers:
            while shortage > 0:
                progressed = False
                for source in output_quotas:
                    pool = leftovers.get(source)
                    if pool:
                        selected.append(pool.pop(0))
                        shortage -= 1
                        progressed = True
                        if shortage <= 0:
                            break
                if not progressed:
                    break
        return selected

    @staticmethod
    def _sort_key(
        result: SearchResult,
        totals: dict[int, float],
        index: dict[int, int],
    ) -> tuple:
        """组内排序键：(已知/未知, -被引, -综合分, 原始序)。

        已知 citation（含 0）进组 0 按 -被引；None 进组 1 恒排已知之后，
        组内按综合分（即"全 None 来源自动回退综合排序"的统一实现）。
        """
        cite = result.citation_count
        total = totals.get(id(result), 0.0)
        idx = index.get(id(result), 0)
        if cite is None:
            return (1, 0.0, -total, idx)
        return (0, -float(cite), -total, idx)
