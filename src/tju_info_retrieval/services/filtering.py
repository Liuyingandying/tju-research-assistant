"""高级筛选服务 FilterService（v0.13 Phase 3）。

在 merge 与 ranking 之间执行高级条件过滤，为后续（CNKI 高级检索 /
IEEE affiliation / 万方 detail 补全）提供统一入口：

- author_name：SearchResult.authors 部分匹配（大小写不敏感、去空白）。
- author_affiliation：metadata_map[result_id].affiliations 匹配
  （单位是延迟增强数据；soft 模式缺少 MetadataRecord 时保留并记录日志，
  strict 模式缺少/不匹配时删除，不因元数据缺失而误删）。
- start_date / end_date：按 SearchResult.year 年份闭区间过滤
  （v1 仅支持年份粒度，YYYY.MM → YYYY）。

单位筛选模式（query.affiliation_filter_mode，旧 Query 对象回退 soft）：
- soft（默认，智能匹配）：有单位数据时匹配过滤；无单位数据时保留。
- strict（严格匹配）：已确认匹配保留、已确认不匹配删除、无单位数据删除。

v0.17 Phase 2.2-A 多类型语义（论文路径逐行不变）：
- 作者：paper 保持"无作者即删"；patent 候选 = authors + inventors，
  news 候选 = authors + artifact_metadata.authors + related_person；
  有候选按部分匹配决定保留/删除，全部缺失 → 保留（unknown != mismatch）。
- 单位：paper 不变；patent 优先匹配 applicant（缺失落入既有 soft/strict）；
  news 无单位概念（soft 保留 / strict 删除）。
- 时间：paper 不变（含万方缺失保护）；非 paper 年份解析顺序
  year → typed 日期（patent.date / news.publish_time）；完全无年份 → 保留。
- soft/strict 仍仅表示单位匹配语义，不扩散到作者与时间条件。

无任何筛选条件时直接返回原列表（保持旧行为与检索性能）。
纯逻辑、无网络、无 GUI 依赖，可单测。
"""
from __future__ import annotations

import logging
import re

from tju_info_retrieval.models.metadata import MetadataRecord
from tju_info_retrieval.models.query import QueryRequest
from tju_info_retrieval.models.result import SearchResult

logger = logging.getLogger(__name__)


class FilterService:
    """按 QueryRequest 高级条件过滤 SearchResult 列表（纯逻辑）。"""

    def filter(
        self,
        results: list[SearchResult],
        query: QueryRequest,
        metadata_map: dict[str, MetadataRecord] | None = None,
    ) -> list[SearchResult]:
        """过滤结果；无任何条件时原样返回（不改变对象与顺序）。"""
        if not results:
            return []
        if not self._has_conditions(query):
            return list(results)
        metadata_map = metadata_map or {}
        mode = getattr(query, "affiliation_filter_mode", "soft")
        kept: list[SearchResult] = []
        for result in results:
            if self._matches(result, query, metadata_map, mode):
                kept.append(result)
        return kept

    # ---------- 条件判定 ----------

    @staticmethod
    def _has_conditions(query: QueryRequest) -> bool:
        return bool(
            (query.author_name or "").strip()
            or (query.author_affiliation or "").strip()
            or (query.start_date or "").strip()
            or (query.end_date or "").strip()
        )

    def _matches(
        self,
        result: SearchResult,
        query: QueryRequest,
        metadata_map: dict[str, MetadataRecord],
        affiliation_mode: str = "soft",
    ) -> bool:
        if not self._match_author(result, query.author_name):
            return False
        if not self._match_affiliation(
            result, query.author_affiliation, metadata_map, affiliation_mode
        ):
            return False
        if not self._match_year(result, query.start_date, query.end_date):
            return False
        return True

    # ---------- 统计（供状态提示，不改变 filter 签名） ----------

    @classmethod
    def affiliation_stats(
        cls,
        results: list[SearchResult],
        query: QueryRequest,
        metadata_map: dict[str, MetadataRecord],
    ) -> dict:
        """统计单位筛选相关的三分类数量（供 SearchService 状态提示）。

        返回 {matched, mismatch, unknown}：
        - matched：来源已验证单位（source_verified）或 MetadataRecord 单位匹配
        - mismatch：有 MetadataRecord 但单位不匹配
        - unknown：无 MetadataRecord（soft 模式会保留、strict 模式会删除）
        仅当 query.author_affiliation 非空时才有意义。
        """
        target = (query.author_affiliation or "").strip()
        normalized = cls._normalize_affiliation(target) if target else ""
        matched = mismatch = unknown = 0
        for result in results:
            record = cls._record_for(result, metadata_map)
            if record is None:
                unknown += 1
                continue
            if record.source_verified:
                matched += 1
                continue
            if normalized and any(
                normalized in cls._normalize_affiliation(aff)
                for aff in (record.affiliations or [])
            ):
                matched += 1
            else:
                mismatch += 1
        return {"matched": matched, "mismatch": mismatch, "unknown": unknown}

    # ---------- 作者筛选 ----------

    def _match_author(self, result: SearchResult, author_name: str) -> bool:
        target = (author_name or "").strip()
        if not target:
            return True
        normalized = self._normalize_author(target)
        if not normalized:
            return True
        artifact_type = getattr(result, "artifact_type", "paper") or "paper"
        if artifact_type == "paper":
            # v0.13 既有语义（逐行不变）：作者缺失即不匹配 → 删除
            return any(
                normalized in self._normalize_author(author)
                for author in (result.authors or [])
            )
        # v0.17 Phase 2.2-A：非 paper 类型候选人物合并。
        # unknown != mismatch：所有人物字段均缺失时保留（adapter 可能已在
        # 服务端按人检索，本地元数据缺失不等于不匹配）。
        meta = getattr(result, "artifact_metadata", None) or {}
        if artifact_type == "patent":
            candidates = self._name_candidates(
                result.authors, meta.get("inventors")
            )
        else:  # news（当前仅 paper/patent/news 三类）
            candidates = self._name_candidates(
                result.authors, meta.get("authors"), meta.get("related_person")
            )
        if not candidates:
            return True
        return any(
            normalized in self._normalize_author(str(candidate))
            for candidate in candidates
        )

    @staticmethod
    def _name_candidates(*sources) -> list[str]:
        """人物字段值归一（string / list / tuple / None → 非空字符串列表）。

        artifact_metadata 是自由 dict，inventors/authors/related_person 的
        运行时形态不得假定；不建立新类体系，仅做最小归一化。
        """
        out: list[str] = []
        for source in sources:
            if source is None:
                continue
            if isinstance(source, str):
                if source.strip():
                    out.append(source.strip())
                continue
            if isinstance(source, (list, tuple)):
                for item in source:
                    if isinstance(item, str) and item.strip():
                        out.append(item.strip())
                continue
            # 其它形态（数字等）：跳过
        return out

    @staticmethod
    def _normalize_author(text: str) -> str:
        """作者归一：小写 + 去空白（zhang san → zhangsan，匹配 Zhang San）。"""
        return re.sub(r"[\s\u3000]+", "", (text or "")).lower()

    # ---------- 单位筛选 ----------

    def _match_affiliation(
        self,
        result: SearchResult,
        affiliation: str,
        metadata_map: dict[str, MetadataRecord],
        affiliation_mode: str = "soft",
    ) -> bool:
        target = (affiliation or "").strip()
        if not target:
            return True
        artifact_type = getattr(result, "artifact_type", "paper") or "paper"
        if artifact_type == "patent":
            # v0.17 Phase 2.2-A：专利优先匹配申请人（artifact_metadata.applicant）。
            # paper 路径完全不受影响；news 无单位概念，直接落入下方既有 soft/strict。
            meta = getattr(result, "artifact_metadata", None) or {}
            applicant = str(meta.get("applicant") or "").strip()
            if applicant:
                normalized = self._normalize_affiliation(target)
                if not normalized:
                    return True
                return normalized in self._normalize_affiliation(applicant)
            # 无申请人 → 继续走既有 record=None soft/strict 语义
        record = self._record_for(result, metadata_map)
        if record is not None and record.source_verified:
            # 来源检索已验证单位条件（如 CNKI 高级检索按 author_affiliation
            # 在服务端过滤）→ 信任来源标记，即使结果无 affiliation 元数据。
            # soft/strict 均信任：来源已代为验证，无需二次元数据。
            logger.debug("来源已验证单位条件，保留: %r", result.title)
            return True
        # v0.13 万方来源保护：万方当前无单位元数据来源（高级检索页不返回
        # 作者单位字段、结果页 detail_url 亦为空）。strict 语义下“无数据”
        # 不等同于“不匹配”，不应误删全部万方结果——保留并记录 warning。
        if (
            affiliation_mode == "strict"
            and self._is_wanfang(result)
            and self._wanfang_org_missing(record)
        ):
            logger.warning(
                "strict 模式：万方结果缺少单位元数据，来源保护保留: %r",
                result.title,
            )
            return True
        if record is None:
            # soft：单位是延迟增强数据，缺少 MetadataRecord 时保留；
            # strict：用户明确要求单位，无数据不可证明匹配 → 删除
            if affiliation_mode == "strict":
                logger.debug("strict 模式：无 MetadataRecord，删除: %r", result.title)
                return False
            logger.debug("soft 模式：无 MetadataRecord，跳过单位筛选（保留）: %r", result.title)
            return True
        normalized = self._normalize_affiliation(target)
        if not normalized:
            return True
        matched = any(
            normalized in self._normalize_affiliation(aff)
            for aff in (record.affiliations or [])
        )
        if not matched and affiliation_mode == "strict":
            logger.debug("strict 模式：单位不匹配，删除: %r", result.title)
        return matched

    @staticmethod
    def _record_for(
        result: SearchResult,
        metadata_map: dict[str, MetadataRecord],
    ) -> MetadataRecord | None:
        """以 detail_url（回退 doi）作为 result_id 关联 MetadataRecord。"""
        result_id = result.detail_url or result.doi
        if not result_id:
            return None
        return metadata_map.get(result_id)

    @staticmethod
    def _is_wanfang(result: SearchResult) -> bool:
        """结果是否来自万方（当前无单位元数据来源，strict 需来源保护）。"""
        return (result.database or "") == "万方"

    @staticmethod
    def _wanfang_org_missing(record: MetadataRecord | None) -> bool:
        """万方结果是否缺少可判定的作者单位数据。"""
        return record is None or not (record.affiliations or [])

    @staticmethod
    def _normalize_affiliation(text: str) -> str:
        """单位归一：小写 + 去标点 + 空格归一。

        "Tianjin University." / "tianjin university" / "Tianjin, University"
        均归一为 "tianjin university"。
        """
        s = (text or "").lower().strip()
        # 去标点：保留 CJK / ASCII 字母数字 / 空白
        s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9\s]+", "", s)
        return re.sub(r"\s+", " ", s).strip()

    # ---------- 时间筛选（v1 仅年份） ----------

    def _match_year(
        self,
        result: SearchResult,
        start_date: str,
        end_date: str,
    ) -> bool:
        start_year = self._date_to_year(start_date)
        end_year = self._date_to_year(end_date)
        if start_year is None and end_year is None:
            return True
        year = self._result_year(result.year)
        if year is None:
            artifact_type = getattr(result, "artifact_type", "paper") or "paper"
            if artifact_type == "paper":
                # 有时间限制且 year 缺失 → 过滤（无法确认在区间内）
                # v0.13 万方来源保护：万方结果当前普遍缺少年份元数据（E2E 实测
                # year=""），"无数据"不应等同于"不在区间内"而误删全部万方结果
                # ——保留并记录 warning；年份可解析时仍按区间正常过滤。
                if self._is_wanfang(result):
                    logger.warning(
                        "万方结果缺少年份元数据，时间过滤保留: %r", result.title)
                    return True
                return False
            # v0.17 Phase 2.2-A：非 paper 从 typed 元数据取日期
            # （patent → artifact_metadata.date；news → publish_time）。
            meta = getattr(result, "artifact_metadata", None) or {}
            date_raw = (
                meta.get("date")
                if artifact_type == "patent"
                else meta.get("publish_time")
            )
            year = self._result_year(date_raw)
            if year is None:
                # 完全无法取得年份 → 保留。单位匹配的 strict/soft 与时间字段
                # 可信度无关，不据此删除。
                return True
        if start_year is not None and year < start_year:
            return False
        if end_year is not None and year > end_year:
            return False
        return True

    @staticmethod
    def _date_to_year(date_str: str | None) -> int | None:
        """YYYY.MM → YYYY；空值或非法返回 None。"""
        if not date_str:
            return None
        match = re.match(r"(\d{4})", date_str.strip())
        return int(match.group(1)) if match else None

    @staticmethod
    def _result_year(year: str | None) -> int | None:
        """从 SearchResult.year（如 "2024" / "2024-05-15"）提取 4 位年份。"""
        if not year:
            return None
        match = re.search(r"\b((?:19|20)\d{2})\b", str(year))
        return int(match.group(1)) if match else None
