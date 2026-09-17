"""论文收藏记录模型 LibraryRecord（v0.18 Phase 2.9-B，schema v2）。

独立于 SearchResult：收藏是用户行为产生的元数据，与检索结果本身
（标题、作者、年份等）属于不同语义层。

v2 在 v1（title/authors/year/source/database/venue/abstract/detail_url/
doi/tags/note/saved_at/reading_status）之上扩展：

- 基础身份：library_id 由 id 承担；新增 artifact_type / publication_number；
- 原始 Evidence：abstract / metadata；
- 基础整理：basic_summary（StructuredSummary.to_dict()，含 source_basis）；
- AI 增强分析：enhanced_summary（EnhancedSummary.to_dict()）+ 分析元信息
  （provider / model / prompt_version / profile_version / ai_analyzed_at）；
- 用户管理：user_tags / reading_status / user_note / favorite_time / updated_time。

兼容：v1 记录缺少的字段一律使用默认值（None / [] / "" / unread），
旧库可直接打开，不做破坏性迁移。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

# 论文库 schema 版本（顶层文件与记录共用）
LIBRARY_SCHEMA_VERSION = 2

# 阅读状态（用户实际阅读进度；与 AI 的 reading_priority 严格区分）
STATUS_UNREAD = "unread"
STATUS_SKIMMED = "skimmed"
STATUS_READING = "reading"
STATUS_FINISHED = "finished"
READING_STATUSES = (STATUS_UNREAD, STATUS_SKIMMED, STATUS_READING, STATUS_FINISHED)
READING_STATUS_LABELS = {
    STATUS_UNREAD: "未读",
    STATUS_SKIMMED: "已泛读",
    STATUS_READING: "阅读中",
    STATUS_FINISHED: "已完成",
}

# 旧版状态值 → 新枚举（v1 注释里的 done/skipped + 中文文案）
_LEGACY_STATUS_MAP = {
    "done": STATUS_FINISHED,
    "finished": STATUS_FINISHED,
    "已完成": STATUS_FINISHED,
    "skipped": STATUS_SKIMMED,
    "skimmed": STATUS_SKIMMED,
    "已泛读": STATUS_SKIMMED,
    "reading": STATUS_READING,
    "阅读中": STATUS_READING,
    "unread": STATUS_UNREAD,
    "未阅读": STATUS_UNREAD,
    "未读": STATUS_UNREAD,
    "": STATUS_UNREAD,
}

# 单标签长度上限 / 单论文标签数量上限（§二十二）
TAG_MAX_LENGTH = 30
TAG_MAX_COUNT = 20


def normalize_tags(raw: list | None) -> list[str]:
    """标签归一：strip、去空、去重保序、限长 30、限数 20。"""
    result: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        text = str(item or "").strip()
        if not text:
            continue
        text = text[:TAG_MAX_LENGTH]
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= TAG_MAX_COUNT:
            break
    return result


def normalize_reading_status(value) -> str:
    """阅读状态归一（未知值 → unread）。"""
    text = str(value or "").strip()
    if text in READING_STATUSES:
        return text
    return _LEGACY_STATUS_MAP.get(text, _LEGACY_STATUS_MAP.get(text.lower(),
                                                             STATUS_UNREAD))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_authors(raw) -> list[str]:
    """作者归一：接受 list 或单个字符串（旧数据可能是连写字符串）。

    字符串按常见分隔符切分，避免被逐字符拆开。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p for p in re.split(r"[;；,，、/]+", raw) if p.strip()]
        return [p.strip() for p in parts] if parts else (
            [raw.strip()] if raw.strip() else [])
    if isinstance(raw, (list, tuple)):
        return [str(a).strip() for a in raw if str(a).strip()]
    return [str(raw).strip()] if str(raw).strip() else []


def _norm_title(title: str) -> str:
    """规范化标题（去空白与常见标点，小写）——身份匹配兜底。"""
    text = str(title or "").lower()
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[，。、；：？！,.;:?!\"'“”‘’（）()\[\]【】\-—_/\\]", "", text)
    return text


@dataclass
class LibraryRecord:
    """一条论文收藏记录（schema v2）。"""

    # --- 唯一标识 ---
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    schema_version: int = LIBRARY_SCHEMA_VERSION

    # --- 检索元数据 ---
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: str | None = None
    source: str | None = None
    database: str = ""
    venue: str | None = None
    abstract: str | None = None
    detail_url: str | None = None
    doi: str | None = None
    # v2 新增
    artifact_type: str = "paper"
    publication_number: str | None = None
    metadata: dict = field(default_factory=dict)

    # --- 分析结果（只保存"已有"的分析；收藏流程不触发 API） ---
    basic_summary: dict | None = None
    enhanced_summary: dict | None = None
    # AI 分析元信息（判断分析是否过期；不写 provider 之外的敏感信息）
    enhanced_provider: str = ""
    enhanced_model: str = ""
    enhanced_prompt_version: str = ""
    enhanced_profile_version: str = ""
    enhanced_at: str = ""

    # --- 用户数据 ---
    tags: list[str] = field(default_factory=list)
    note: str = ""
    saved_at: str = ""
    updated_time: str = ""
    reading_status: str = STATUS_UNREAD

    # ---------- 派生字段（唯一真源为 enhanced_summary） ----------

    @property
    def document_type(self) -> str:
        return str((self.enhanced_summary or {}).get("document_type") or "")

    @property
    def document_type_reason(self) -> str:
        return str((self.enhanced_summary or {}).get("document_type_reason") or "")

    @property
    def direction_match_level(self) -> str:
        return str(
            (self.enhanced_summary or {}).get("direction_match_level") or "")

    @property
    def reading_priority(self) -> str:
        return str((self.enhanced_summary or {}).get("reading_priority") or "")

    @property
    def user_tags(self) -> list[str]:
        """别名（§十一 user_tags）；与 tags 同一字段。"""
        return self.tags

    @property
    def user_note(self) -> str:
        """别名（§十一 user_note）；与 note 同一字段。"""
        return self.note

    @property
    def favorite_time(self) -> str:
        """别名（§十一 favorite_time）；与 saved_at 同一字段。"""
        return self.saved_at

    # ---------- 身份 / 去重 ----------

    def identity_keys(self) -> list[tuple]:
        """去重身份键（按优先级从强到弱；空值不产生键）。

        DOI → detail_url / publication_number → 标题+作者+年份 → 规范化标题。
        同一篇论文重复收藏时命中靠前的键 → 更新已有记录而非新增。
        """
        kind = str(self.artifact_type or "paper")
        keys: list[tuple] = []
        doi = str(self.doi or "").strip().lower()
        if doi:
            keys.append(("doi", f"{kind}#{doi}"))
        url = str(self.detail_url or "").strip().lower().rstrip("/")
        if url:
            keys.append(("url", f"{kind}#{url}"))
        number = str(self.publication_number or "").strip().lower()
        if number:
            keys.append(("pubno", f"{kind}#{number}"))
        title_key = _norm_title(self.title)
        if title_key:
            authors_key = "|".join(
                sorted(str(a).strip().lower() for a in self.authors if a))
            year_key = str(self.year or "").strip()
            keys.append(("tay", f"{kind}#{title_key}#{authors_key}#{year_key}"))
            keys.append(("title", f"{kind}#{title_key}"))
        return keys

    def same_paper_as(self, other: "LibraryRecord") -> bool:
        """是否同一篇论文（任一身份键相交，键含类型避免跨类型误判）。"""
        return bool(set(self.identity_keys()) & set(other.identity_keys()))

    # ---------- 分析持久化 ----------

    def has_basic_summary(self) -> bool:
        return bool(self.basic_summary)

    def has_enhanced_summary(self) -> bool:
        return bool(self.enhanced_summary)

    def set_basic_summary(self, summary) -> None:
        """写入基础整理（StructuredSummary 或 dict；保留 source_basis）。"""
        if summary is None:
            self.basic_summary = None
            return
        data = summary.to_dict() if hasattr(summary, "to_dict") else dict(summary)
        self.basic_summary = data
        self.touch()

    def set_enhanced_summary(
        self,
        summary,
        *,
        provider: str = "",
        model: str = "",
        prompt_version: str = "",
        profile_version: str = "",
        analyzed_at: str = "",
    ) -> None:
        """写入 AI 增强分析 + 分析元信息（供过期判断）。"""
        if summary is None:
            self.enhanced_summary = None
            return
        data = summary.to_dict() if hasattr(summary, "to_dict") else dict(summary)
        self.enhanced_summary = data
        self.enhanced_provider = provider or str(data.get("provider") or "")
        self.enhanced_model = model
        self.enhanced_prompt_version = prompt_version
        self.enhanced_profile_version = profile_version
        self.enhanced_at = analyzed_at or _now_iso()
        self.touch()

    def clear_enhanced_summary(self) -> None:
        self.enhanced_summary = None
        self.enhanced_provider = ""
        self.enhanced_model = ""
        self.enhanced_prompt_version = ""
        self.enhanced_profile_version = ""
        self.enhanced_at = ""

    def is_profile_stale(self, current_profile_version) -> bool:
        """分析是否基于旧科研画像（§十七）。"""
        if not self.has_enhanced_summary():
            return False
        saved = str(self.enhanced_profile_version or "")
        if not saved:
            return False
        current = str(current_profile_version or "")
        if not current:
            return False
        return saved != current

    def is_prompt_stale(self, current_prompt_version: str) -> bool:
        """分析是否由旧 Prompt 版本产生（§十八）。"""
        if not self.has_enhanced_summary():
            return False
        saved = str(self.enhanced_prompt_version or "")
        if not saved:
            return False
        return saved != str(current_prompt_version or "")

    def analysis_source_label(self) -> str:
        """分析来源展示（§十九）："TJU LLM / tju-llm" 形式，不伪装成当前模型。"""
        provider = str(self.enhanced_provider or "").strip()
        model = str(self.enhanced_model or "").strip()
        if not provider and not model:
            return ""
        if provider and model:
            return f"{provider} / {model}"
        return provider or model

    # ---------- 用户字段更新 ----------

    def set_tags(self, tags: list | None) -> None:
        self.tags = normalize_tags(tags)
        self.touch()

    def set_reading_status(self, status) -> None:
        self.reading_status = normalize_reading_status(status)
        self.touch()

    def set_note(self, note: str) -> None:
        self.note = str(note or "")
        self.touch()

    def touch(self) -> None:
        self.updated_time = _now_iso()

    # ---------- 构造 / 序列化 ----------

    @staticmethod
    def from_search_result(data: dict) -> "LibraryRecord":
        """从搜索结果 dict 转换为 LibraryRecord（不含任何分析结果）。"""
        return LibraryRecord(
            title=(data.get("title") or "").strip(),
            authors=normalize_authors(data.get("authors")),
            year=data.get("year"),
            source=data.get("source"),
            database=data.get("database") or "",
            venue=data.get("venue"),
            abstract=data.get("abstract"),
            detail_url=data.get("detail_url"),
            doi=data.get("doi"),
            artifact_type=data.get("artifact_type") or "paper",
            publication_number=data.get("publication_number"),
            metadata=dict(data.get("artifact_metadata") or {}),
            saved_at=_now_iso(),
            updated_time=_now_iso(),
        )

    @classmethod
    def from_search_result_obj(cls, result) -> "LibraryRecord":
        """从 SearchResult 对象转换（GUI 主窗口使用）。"""
        data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        record = cls.from_search_result(data)
        record.artifact_type = (
            getattr(result, "artifact_type", None) or record.artifact_type)
        meta = getattr(result, "artifact_metadata", None)
        if isinstance(meta, dict):
            record.metadata = dict(meta)
            number = meta.get("publication_number") or meta.get("patent_number")
            if number:
                record.publication_number = str(number)
        return record

    def to_dict(self) -> dict:
        """序列化（含派生显示字段，便于 JSON 直接可读/可查）。"""
        data = {
            "id": self.id,
            "schema_version": LIBRARY_SCHEMA_VERSION,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "source": self.source,
            "database": self.database,
            "venue": self.venue,
            "abstract": self.abstract,
            "detail_url": self.detail_url,
            "doi": self.doi,
            "artifact_type": self.artifact_type,
            "publication_number": self.publication_number,
            "metadata": dict(self.metadata),
            "basic_summary": self.basic_summary,
            "enhanced_summary": self.enhanced_summary,
            "enhanced_provider": self.enhanced_provider,
            "enhanced_model": self.enhanced_model,
            "enhanced_prompt_version": self.enhanced_prompt_version,
            "enhanced_profile_version": self.enhanced_profile_version,
            "enhanced_at": self.enhanced_at,
            # 派生（读回时不作为真源，仅便于外部工具查看）
            "document_type": self.document_type,
            "document_type_reason": self.document_type_reason,
            "direction_match_level": self.direction_match_level,
            "reading_priority": self.reading_priority,
            # 用户数据
            "tags": list(self.tags),
            "note": self.note,
            "saved_at": self.saved_at,
            "updated_time": self.updated_time,
            "reading_status": self.reading_status,
        }
        return data

    @staticmethod
    def from_dict(data: dict) -> "LibraryRecord":
        """反序列化（v1 记录自动补默认值，不异常）。"""
        data = data or {}
        basic = data.get("basic_summary")
        enhanced = data.get("enhanced_summary")
        metadata = data.get("metadata")
        if metadata is None:
            metadata = data.get("artifact_metadata")
        return LibraryRecord(
            id=str(data.get("id") or uuid.uuid4().hex),
            schema_version=int(
                data.get("schema_version") or LIBRARY_SCHEMA_VERSION),
            title=data.get("title") or "",
            authors=normalize_authors(data.get("authors")),
            year=data.get("year"),
            source=data.get("source"),
            database=data.get("database") or "",
            venue=data.get("venue"),
            abstract=data.get("abstract"),
            detail_url=data.get("detail_url"),
            doi=data.get("doi"),
            artifact_type=data.get("artifact_type") or "paper",
            publication_number=data.get("publication_number"),
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            basic_summary=dict(basic) if isinstance(basic, dict) else None,
            enhanced_summary=(
                dict(enhanced) if isinstance(enhanced, dict) else None),
            enhanced_provider=str(data.get("enhanced_provider") or
                                  (enhanced or {}).get("provider") or ""),
            enhanced_model=str(data.get("enhanced_model") or ""),
            enhanced_prompt_version=str(
                data.get("enhanced_prompt_version") or ""),
            enhanced_profile_version=str(
                data.get("enhanced_profile_version") or ""),
            enhanced_at=str(data.get("enhanced_at") or ""),
            tags=normalize_tags(data.get("tags")),
            note=data.get("note") or "",
            saved_at=data.get("saved_at") or "",
            updated_time=data.get("updated_time") or "",
            reading_status=normalize_reading_status(data.get("reading_status")),
        )
