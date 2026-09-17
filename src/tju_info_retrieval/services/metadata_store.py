"""二次增强元数据的内存存取（v0.13 Phase 3）。

Phase 3 最小实现：内存 map，不引入数据库。
为后续（CNKI 高级检索 / IEEE affiliation / 万方 detail 补全）提供
统一的 MetadataRecord 关联入口。
"""
from __future__ import annotations

from tju_info_retrieval.models.metadata import MetadataRecord


class MetadataStore:
    """MetadataRecord 的内存容器：result_id → MetadataRecord。"""

    def __init__(self) -> None:
        self._records: dict[str, MetadataRecord] = {}

    def put(self, record: MetadataRecord) -> None:
        """写入一条记录（按 result_id 覆盖）。"""
        if record.result_id:
            self._records[record.result_id] = record

    def get(self, result_id: str) -> MetadataRecord | None:
        """按 result_id 查询；不存在返回 None。"""
        return self._records.get(result_id)

    def as_map(self) -> dict[str, MetadataRecord]:
        """返回当前记录的副本，供 FilterService 批量读取。"""
        return dict(self._records)

    def __len__(self) -> int:
        return len(self._records)
