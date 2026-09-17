"""数据源注册表。

管理可用 SourceAdapter 的注册与按名称创建。SearchService 通过它
根据 QueryRequest.sources 动态构造对应 Adapter，不再硬编码单一数据源。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tju_info_retrieval.sources.base import SourceAdapter


class AdapterRegistry:
    """数据源注册表（线程安全 = 只读注册后不修改）。"""

    def __init__(self) -> None:
        self._adapters: dict[str, type] = {}

    def register(self, name: str, adapter_cls: type) -> None:
        if name in self._adapters:
            raise ValueError(f"数据源已注册: {name}")
        self._adapters[name] = adapter_cls

    def get(self, name: str) -> type:
        if name not in self._adapters:
            raise ValueError(f"未知数据源: {name}（可用: {list(self._adapters)}）")
        return self._adapters[name]

    def available_sources(self) -> list[str]:
        return list(self._adapters.keys())

    def create(self, name: str, session) -> SourceAdapter:
        cls = self.get(name)
        return cls(session)