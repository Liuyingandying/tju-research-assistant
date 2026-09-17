"""多源结果融合：去重（DOI → URL → 标题标准化）后 rank 重排。"""
from __future__ import annotations

import re
from typing import Iterable

from tju_info_retrieval.models.result import SearchResult


class ResultMerger:
    """将多个来源的 SearchResult 融合为统一结果列表。"""

    @staticmethod
    def merge(results: Iterable[SearchResult]) -> list[SearchResult]:
        seen_dois: set[str] = set()
        seen_urls: set[str] = set()
        seen_titles: set[tuple] = set()
        merged: list[SearchResult] = []

        for r in results:
            # 1) DOI 去重
            if r.doi and r.doi in seen_dois:
                continue
            # 2) URL 去重
            if r.detail_url and r.detail_url in seen_urls:
                continue
            # 3) 类型感知标题去重（v0.17 Phase 2.1）：
            #    键 = (artifact_type, 归一化标题)，跨类型同名不互相吞并；
            #    论文/论文 行为与旧版完全一致（旧数据等价 paper）。
            title_key = r.dedup_title_key()
            if title_key in seen_titles:
                continue

            # 记录并通过
            if r.doi:
                seen_dois.add(r.doi)
            if r.detail_url:
                seen_urls.add(r.detail_url)
            seen_titles.add(title_key)
            merged.append(r)

        # rank 重新生成
        for i, r in enumerate(merged, 1):
            r.rank = i
        return merged

    @staticmethod
    def _normalize_title(title: str) -> str:
        """标题标准化：去空白、去标点、小写。

        中文标题：去空白和标点后保留原字符（大小写对中文无意义）。
        英文标题：转小写。
        """
        if not title:
            return ""
        # 去空白
        s = re.sub(r"[\s\u3000]+", "", title)
        # 去常见标点
        s = re.sub(r"[，。、；：\u201c\u201d\u2018\u2019\u300a\u300b\u300c\u300d\u300e\u300f/\u2014\u2013.,;:!?'\"()\[\]{}<>/]", "", s)
        return s.lower()