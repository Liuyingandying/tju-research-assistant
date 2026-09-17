"""多类型成果标识与 typed 元数据辅助（v0.17 Phase 2.1）。

设计约束：
- SearchResult.artifact_metadata 为自由 dict，**不绑定**本模块 dataclass；
- 本模块 dataclass 仅作为构造 / 序列化辅助，供 demo 数据与未来 Adapter
  生成规范化的 artifact_metadata 字典；
- 不引入继承体系，不参与运行期 schema 校验。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# 成果类型常量（SearchResult.artifact_type 取值）
ARTIFACT_PAPER = "paper"
ARTIFACT_PATENT = "patent"
ARTIFACT_NEWS = "news"

# 成果类型 → 中文展示标签（UI 类型列 / 导出 / 报告共用）
ARTIFACT_LABELS: dict[str, str] = {
    ARTIFACT_PAPER: "论文",
    ARTIFACT_PATENT: "专利",
    ARTIFACT_NEWS: "新闻",
}


@dataclass
class PatentMetadata:
    """专利 typed 元数据（辅助构造/序列化）。

    字段仅为课程任务书 R10 的最小覆盖：发明人 / 申请人 / 公开号 /
    申请号 / 日期。SearchResult.artifact_metadata 存放 to_dict() 结果。
    """

    inventors: list[str] = field(default_factory=list)
    applicant: str | None = None
    publication_number: str | None = None
    application_number: str | None = None
    date: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "PatentMetadata | None":
        if not data:
            return None
        return cls(
            inventors=list(data.get("inventors") or []),
            applicant=data.get("applicant"),
            publication_number=data.get("publication_number"),
            application_number=data.get("application_number"),
            date=data.get("date"),
        )


@dataclass
class NewsMetadata:
    """新闻 typed 元数据（辅助构造/序列化）。

    字段覆盖 R7/R12 的最小集：媒体 / 发布时间 / 署名（authors） / 相关人。
    新闻条目不使用 SearchResult.authors 承载媒体名（避免论文字段伪装）；
    SearchResult.artifact_metadata 存放 to_dict() 结果。
    """

    media: str | None = None
    publish_time: str | None = None
    authors: list[str] = field(default_factory=list)
    related_person: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "NewsMetadata | None":
        if not data:
            return None
        return cls(
            media=data.get("media"),
            publish_time=data.get("publish_time"),
            authors=list(data.get("authors") or []),
            related_person=list(data.get("related_person") or []),
        )