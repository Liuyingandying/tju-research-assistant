"""按需元数据补全管线 MetadataPipeline（v0.13 Phase 4）。

打通 IeeeMetadataFetcher → MetadataStore → FilterService 的单位筛选闭环：
- 仅当 query.author_affiliation 非空时才触发详情增强（普通搜索零开销）；
- 遍历结果，对已注册增强 fetcher 的数据源（如 "IEEE Xplore"）按需补全；
- 先查 MetadataStore，已存在则不重复请求；
- 抓取失败/为空时静默降级：不写入，FilterService 走"缺失→保留"路径，
  不因元数据缺失而误删结果。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Protocol

from tju_info_retrieval.models.metadata import MetadataRecord
from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.metadata_store import MetadataStore

logger = logging.getLogger(__name__)


class AffiliationFetcher(Protocol):
    """详情页单位抓取器接口（IeeeMetadataFetcher 满足）。"""

    def fetch_affiliations(self, detail_url: str) -> list[str]: ...


class MetadataPipeline:
    """按需补全 SearchResult 的二次增强元数据（当前：作者单位）。"""

    def __init__(
        self,
        fetchers: dict[str, AffiliationFetcher] | None = None,
        metadata_store: MetadataStore | None = None,
    ) -> None:
        """``fetchers``：{database_name: fetcher}，fetcher 需提供
        fetch_affiliations(detail_url) -> list[str]。"""
        self._fetchers: dict[str, AffiliationFetcher] = dict(fetchers or {})
        # 注意：MetadataStore 定义了 __len__，空 store 为 falsy，
        # 必须用 is not None 判断，避免传入空 store 时被静默替换
        self._metadata_store = metadata_store if metadata_store is not None else MetadataStore()

    def enrich(
        self,
        results: Iterable[SearchResult],
        query: QueryRequest,
    ) -> dict[str, MetadataRecord]:
        """按需补全并返回 metadata_map（result_id → MetadataRecord）。

        无 author_affiliation 条件时直接返回已有元数据，不触发任何抓取。
        """
        if not (query.author_affiliation or "").strip():
            return self._metadata_store.as_map()
        for result in results:
            self._enrich_one(result)
        return self._metadata_store.as_map()

    def _enrich_one(self, result: SearchResult) -> None:
        fetcher = self._fetchers.get(result.database)
        if fetcher is None:
            return  # 该数据源未注册增强 fetcher
        result_id = result.detail_url or result.doi
        if not result_id:
            return  # 无法关联元数据
        if self._metadata_store.get(result_id) is not None:
            return  # 已有元数据，避免重复请求
        try:
            affiliations = fetcher.fetch_affiliations(result.detail_url)
        except Exception:  # noqa: BLE001 - 抓取失败静默降级，不阻断主流程
            logger.exception("元数据抓取失败 detail_url=%r", result.detail_url)
            return
        if not affiliations:
            logger.debug("元数据抓取为空，不写入（保留结果）: %r", result.detail_url)
            return
        self._metadata_store.put(
            MetadataRecord(
                result_id=result_id,
                affiliations=list(affiliations),
                fetched_at=datetime.now().isoformat(timespec="seconds"),
            )
        )
