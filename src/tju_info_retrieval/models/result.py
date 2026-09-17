"""统一结果模型 SearchResult。"""
from __future__ import annotations

from dataclasses import dataclass, field

from tju_info_retrieval.models.artifact import ARTIFACT_PAPER


def normalize_url(url: str | None) -> str | None:
    """规范化详情链接：去空白，处理协议相对 URL。"""
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    return url


@dataclass
class SearchResult:
    """一条检索结果。

    预留字段（本轮不实现，不进入详情页获取）：
    core_technology / key_findings。
    """

    rank: int
    title: str
    authors: list[str] = field(default_factory=list)
    source: str | None = None
    year: str | None = None
    detail_url: str | None = None
    document_type: str | None = None
    database: str = "CNKI"
    # v0.4 扩展字段（搜索结果页不可见时保持 None/[]，不进入详情页获取）
    abstract: str | None = None
    doi: str | None = None
    keywords: list[str] = field(default_factory=list)
    venue: str | None = None  # 会议/期刊名称（实验性，与 source 可能重叠）
    authors_raw: str | None = None  # 原始作者字符串（如无分隔符的连写）
    # v0.11 扩展字段（引用影响力排序）
    citation_count: int | None = None  # None=未知，0=明确无引用
    # v0.17 Phase 2.1 多类型成果（论文/专利/新闻）：
    # - artifact_type：成果类型（ARTIFACT_PAPER 默认，旧数据等价 paper）；
    # - artifact_metadata：typed 元数据字典（自由 dict，不绑定 dataclass，
    #   由 artifact.py 的 PatentMetadata/NewsMetadata 辅助构造）。
    artifact_type: str = ARTIFACT_PAPER
    artifact_metadata: dict | None = None

    def dedup_title_key(self) -> tuple:
        """类型感知去重键（v0.17 Phase 2.1）。

        返回 (artifact_type, 归一化标题)：跨类型同名条目不互相去重
        （新闻报道与论文同名、专利与论文同名应分别保留），
        同类型同名仍按标题去重。URL/DOI 去重沿用详情标识，与类型无关。
        """
        from tju_info_retrieval.services.result_merger import ResultMerger

        return (
            self.artifact_type or ARTIFACT_PAPER,
            ResultMerger._normalize_title(self.title),
        )

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "title": self.title,
            "authors": list(self.authors),
            "source": self.source,
            "year": self.year,
            "detail_url": normalize_url(self.detail_url),
            "document_type": self.document_type,
            "database": self.database,
            "abstract": self.abstract,
            "doi": self.doi,
            "keywords": list(self.keywords),
            "venue": self.venue,
            "authors_raw": self.authors_raw,
            "citation_count": self.citation_count,
            "artifact_type": self.artifact_type or ARTIFACT_PAPER,
            "artifact_metadata": self.artifact_metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SearchResult":
        return cls(
            rank=data.get("rank"),
            title=(data.get("title") or "").strip(),
            authors=list(data.get("authors") or []),
            source=data.get("source"),
            year=data.get("year"),
            detail_url=normalize_url(data.get("detail_url")),
            document_type=data.get("document_type"),
            database=data.get("database") or "CNKI",
            abstract=data.get("abstract"),
            doi=data.get("doi"),
            keywords=list(data.get("keywords") or []),
            venue=data.get("venue"),
            authors_raw=data.get("authors_raw"),
            citation_count=data.get("citation_count"),
            # 旧数据无 artifact 字段 → 等价 paper（默认值即 ARTIFACT_PAPER）
            artifact_type=data.get("artifact_type") or ARTIFACT_PAPER,
            artifact_metadata=data.get("artifact_metadata"),
        )
