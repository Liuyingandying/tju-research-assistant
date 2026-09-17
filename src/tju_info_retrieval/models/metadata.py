"""二次增强元数据模型 MetadataRecord（v0.13 Phase 2.4 / Phase 8）。

affiliation 属于详情页二次增强数据，与原始检索结果解耦：
不修改 SearchResult，由上层调用方将 IeeeMetadataFetcher 返回的
list[str] 包装为 MetadataRecord 传递/持久化。

v0.13 Phase 8：source_verified 标记"来源检索已验证单位条件"
（如 CNKI 高级检索按 author_affiliation 在服务端过滤），
供 FilterService strict 模式信任来源验证，不因结果无 affiliation
元数据而误删。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MetadataRecord:
    """一条论文的二次增强元数据。

    - result_id: 关联到 SearchResult 的稳定标识（如 detail_url 或 document id）。
    - affiliations: 各作者的机构文本（按作者顺序，缺省为空列表）。
    - fetched_at: 获取时间（ISO 8601 字符串），None 表示尚未获取。
    - source_verified: 来源检索已验证单位条件（CNKI 高级检索等），
      True 时 FilterService 信任该来源，无需 affiliations 即可通过 strict。
    """

    result_id: str
    affiliations: list[str] = field(default_factory=list)
    fetched_at: str | None = None
    source_verified: bool = False

    def to_dict(self) -> dict:
        return {
            "result_id": self.result_id,
            "affiliations": list(self.affiliations),
            "fetched_at": self.fetched_at,
            "source_verified": self.source_verified,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MetadataRecord":
        return cls(
            result_id=(data.get("result_id") or ""),
            affiliations=list(data.get("affiliations") or []),
            fetched_at=data.get("fetched_at"),
            source_verified=bool(data.get("source_verified", False)),
        )
