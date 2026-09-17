"""检索编排服务。"""
from __future__ import annotations

from typing import Callable

from tju_info_retrieval.browser.session import BrowserSession
from tju_info_retrieval.models.metadata import MetadataRecord
from tju_info_retrieval.models.query import QueryRequest, RankingMode
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.adapter_registry import AdapterRegistry
from tju_info_retrieval.services.citation_selector import (
    CitationBalancedSelector,
    candidate_quota,
)
from tju_info_retrieval.services.filtering import FilterService
from tju_info_retrieval.services.ieee_metadata import IeeeMetadataFetcher
from tju_info_retrieval.services.metadata_pipeline import MetadataPipeline
from tju_info_retrieval.services.metadata_store import MetadataStore
from tju_info_retrieval.services.query_expansion import ExpandedQuery, QueryExpansionService
from tju_info_retrieval.services.query_builder import QueryBuilder
from tju_info_retrieval.services.query_translator import QueryTranslator
from tju_info_retrieval.services.ranking import RankingService
from tju_info_retrieval.services.result_merger import ResultMerger
from tju_info_retrieval.sources.cnki import CNKIAdapter
from tju_info_retrieval.sources.cnki_patent import CnkiPatentAdapter
from tju_info_retrieval.sources.ieee import IeeeAdapter
from tju_info_retrieval.sources.wanfang import WanfangAdapter
from tju_info_retrieval.sources.tju_news import TjuNewsAdapter
from tju_info_retrieval.sources.wanfang_patent import WanfangPatentAdapter


class AuthError(Exception):
    """天津大学授权异常。"""


class SearchService:
    """编排：检查授权 → 扩展检索词 → 根据数据源列表执行检索。"""

    def __init__(
        self,
        session: BrowserSession,
        registry: AdapterRegistry | None = None,
        metadata_store: MetadataStore | None = None,
        metadata_pipeline: MetadataPipeline | None = None,
    ) -> None:
        self._session = session
        self._registry = registry or self._default_registry()
        self._expansion = QueryExpansionService()
        self._translator = QueryTranslator()
        self._builder = QueryBuilder()
        self._filter = FilterService()
        # 注意：MetadataStore 定义了 __len__，空 store 为 falsy，
        # 必须用 is not None 判断，避免传入空 store 时被静默替换
        self._metadata_store = metadata_store if metadata_store is not None else MetadataStore()
        if metadata_pipeline is None:
            # 默认：IEEE 单位增强（仅 author_affiliation 条件存在时才会触发）
            metadata_pipeline = MetadataPipeline(
                fetchers={"IEEE Xplore": IeeeMetadataFetcher(session)},
                metadata_store=self._metadata_store,
            )
        self._metadata_pipeline = metadata_pipeline
        self.last_expanded: ExpandedQuery | None = None

    @staticmethod
    def _default_registry() -> AdapterRegistry:
        r = AdapterRegistry()
        r.register("CNKI", CNKIAdapter)
        # v0.17 Phase 2.2-F：CNKI 专利（独立 source/database 身份，
        # 与 CNKI paper 不混组；GUI 经 专利×CNKI routing 到达）
        r.register("CNKI专利", CnkiPatentAdapter)
        r.register("万方", WanfangAdapter)
        r.register("IEEE Xplore", IeeeAdapter)
        # v0.17 Phase 2.2-C：万方专利（多类型成果；GUI 入口待 Phase 2.2-D，
        # 此处仅提供 SearchService 可测试调用入口 —— sources=["万方专利"]）
        r.register("万方专利", WanfangPatentAdapter)
        # v0.17 Phase 2.3-B：天津大学新闻网（新闻 artifact；GUI 路由待
        # Phase 2.3-C，此处仅提供 sources=["天津大学新闻网"] 可测试入口）
        r.register("天津大学新闻网", TjuNewsAdapter)
        return r

    @staticmethod
    def _allocate_counts(total: int, n_sources: int) -> list[int]:
        """将候选总数均分到各数据源（v0.13 多源候选配额均衡）。

        - 单源（n_sources <= 1）→ [total]，行为与旧版完全一致。
        - 偶数精确均分；奇数各源相差 1，前置数据源多 1
          （与 sources 循环顺序一致：CNKI → 万方 → IEEE）。
        - total < n_sources 时尾部源配额为 0，调用方应跳过这些源，
          避免以 count=0 调用 Adapter（各 Adapter 结果为空会抛 SearchError）。
        """
        if n_sources <= 1:
            return [total]
        base, rem = divmod(total, n_sources)
        return [base + (1 if i < rem else 0) for i in range(n_sources)]

    def check_tju_auth(self) -> str:
        self._session.open_tju()
        return self._session.check_tju_auth()

    def search(
        self,
        query: QueryRequest,
        status: Callable[[str], None] | None = None,
    ) -> list[SearchResult]:
        def emit(message: str) -> None:
            if status:
                status(message)

        direction = query.research_direction.strip()
        person = (query.author_name or "").strip()
        # v0.17 Phase 1：研究方向 OR 人员姓名（至少一项）；仅人员单位仍无效
        if not direction and not person:
            raise ValueError("请输入研究方向或人员姓名，至少填写一项")

        sources = query.sources or ["CNKI"]
        if not sources:
            raise ValueError("请至少选择一个数据来源")

        emit("正在检查天津大学授权...")
        state = self.check_tju_auth()
        if state == "login_page":
            raise AuthError("天津大学登录状态失效，请在受控浏览器中完成登录后重试。")

        emit("正在打开 CNKI...")
        self._session.open_cnki()

        # v0.17 Phase 1：主题扩展链仅在研究方向非空时运行；
        # 纯人员检索（direction 为空）不生成任何 topic 检索串
        # （不产生 ""/"()"/"( OR )" 等退化表达式，adapter 收到空 keyword）。
        if direction:
            expanded = self._expansion.expand(direction, query.expansion_direction)
            self.last_expanded = expanded
            emit(
                f"检索词：{expanded.original} → {expanded.query_string}"
                f"（方向：{expanded.expansion_direction}）"
            )
            concepts = self._translator.translate_concepts(expanded, sources)
        else:
            self.last_expanded = None
            concepts = {}
            emit(f"作者检索：{person}")

        all_results: list[SearchResult] = []
        # v0.16 Phase B：高引用模式两段配额——
        # 输出配额 = _allocate_counts(result_count, n)（来源均衡的最终目标，
        # 差值 ≤1，前置源多 1）；候选配额 = candidate_quota(output)
        # （≥3 倍输出，为来源内被引排序留出裁剪余量，上限 50）。
        # 普通模式保持既有 effective_candidate_count 均分，行为不变。
        is_citation_mode = (
            getattr(query, "ranking_mode", "") == RankingMode.CITATION.value
        )
        output_quotas_list = self._allocate_counts(query.result_count, len(sources))
        if is_citation_mode:
            candidate_quotas = [candidate_quota(q) for q in output_quotas_list]
        else:
            candidate_quotas = self._allocate_counts(
                query.effective_candidate_count, len(sources)
            )
        # v0.13 多源候选配额均衡：候选总数均分到各源（奇数前置源多 1），
        # 替代旧版"每源各请求完整 N"导致的候选池超额与展示偏向。
        for source_name, quota in zip(sources, candidate_quotas):
            if quota <= 0:
                # N < 数据源数时尾部源配额为 0：跳过检索，
                # 避免 count=0 调用 Adapter 触发"结果为空"SearchError
                emit(f"{source_name} 配额为 0（结果数量少于数据源数），跳过检索")
                continue
            adapter_cls = self._registry.get(source_name)
            adapter = adapter_cls(self._session)
            emit(f"正在检索{source_name}（配额 {quota} 条）...")
            # 透传 query 作为 query_context：仅传递，不在本层编译 CNKI 条件。
            # 纯人员检索时 query_string 为空串（作者条件由各 Adapter 从
            # query_context.author_name 读取并自行构造源内检索语义）。
            query_string = (
                self._builder.build(source_name, concepts[source_name])
                if direction else ""
            )
            partial = adapter.search(
                query_string,
                quota,
                query_context=query,
            )
            # v0.13 Phase 8：来源检索已验证单位条件（如 CNKI 高级检索在服务端
            # 按 author_affiliation 过滤）→ 写入 source_verified 标记，
            # 供 FilterService strict 模式信任，避免结果无元数据被误删。
            # 注意：mock adapter 的任意属性访问会返回 Mock，需 isinstance 防御
            verified_ids = getattr(adapter, "source_verified_ids", None)
            if not isinstance(verified_ids, (set, list)):
                verified_ids = ()
            for result_id in verified_ids:
                if result_id:
                    self._metadata_store.put(
                        MetadataRecord(result_id=result_id, source_verified=True)
                    )
            all_results.extend(partial)

        merged = ResultMerger.merge(all_results)
        # 仅当 query.author_affiliation 非空时，MetadataPipeline 才触发详情增强
        metadata_map = self._metadata_pipeline.enrich(merged, query)
        filtered = self._filter.filter(merged, query, metadata_map=metadata_map)
        if is_citation_mode:
            # v0.16 Phase B：来源均衡高引用 Top-K（分组取 Top quota +
            # 缺额轮询回填）；仅 CITATION 模式进入，普通模式不经过该阶段
            output_quotas = dict(zip(sources, output_quotas_list))
            filtered = CitationBalancedSelector.select(
                filtered, query, output_quotas
            )
        ranked = RankingService.rank(filtered, query)
        truncated = ranked[:query.result_count]
        if (query.author_affiliation or "").strip():
            mode = getattr(query, "affiliation_filter_mode", "soft")
            stats = FilterService.affiliation_stats(merged, query, metadata_map)
            if mode == "strict":
                emit(
                    f"单位筛选：{query.author_affiliation.strip()}｜"
                    f"模式：严格匹配｜过滤前：{len(merged)}｜过滤后：{len(filtered)}"
                )
            else:
                emit(
                    f"单位智能匹配：已验证{stats['matched']}｜"
                    f"不匹配{stats['mismatch']}｜未知{stats['unknown']}"
                )
        elif len(filtered) != len(merged):
            emit(f"筛选后：{len(merged)} → {len(filtered)} 条")
        emit(f"检索完成：{len(truncated)} 条（候选 {len(ranked)}）")
        return truncated