"""轻量数据源适配器接口。"""
from __future__ import annotations

from tju_info_retrieval.models.result import SearchResult


class SearchError(Exception):
    """数据源检索失败。"""


class SourceAdapter:
    """数据源适配器基类。"""

    database_name = "unknown"

    def search(
        self,
        keyword: str,
        count: int = 10,
        query_context=None,
    ) -> list[SearchResult]:
        """执行检索。

        ``query_context`` 可选（QueryRequest 或任意含高级字段的对象），
        供支持高级条件的适配器（如 CNKI）读取 author_name /
        author_affiliation / start_date / end_date；不支持时可忽略。
        旧调用 ``search(keyword, count)`` 保持有效。
        """
        raise NotImplementedError
