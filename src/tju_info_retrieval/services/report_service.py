"""ReportService：论文集合 → 科研调研报告（v0.18 Phase 2.9-C-C-D）。

职责边界（本阶段只做 Service 层）：

- 输入：论文库中的 `paper_ids`（+ 可选标题）；
  标题优先级：用户显式 > Provider 生成 > 服务默认（画像领域 + 篇数）；
- 输出：`ResearchReport`（已保存），以及可读的生成结果状态。

明确不做：UI / Prompt 优化 / Markdown 导出 / 批量任务队列 / 真实 LLM Provider。

## 流程

```
generate_report(paper_ids, title=None)
  1) 读取论文库（只读）
  2) 校验论文数量：0 拒绝 / 1-4 warning / 5-20 正常 / >20 拒绝
  3) 组装 ReportContext（papers 一律转换为 ReportSource 快照）
  4) 查 ReportCache → 命中直接返回（禁止调用 Provider）
  5) 调用 ReportProvider
  6) 来源校验 validate_sources() → 失败即降级
  7) 保存 ResearchReport
```

## 失败降级（绝不把异常抛给 UI）

API 不可用 / Provider 异常 / 返回结构不可用 / 来源校验失败 → 一律生成
**基础报告**（`status="draft"`），只由论文库既有数据聚合：

- 必含章节：`文献集合概览`、`已有研究方向`；
- 其余章节标注「未调用 AI」；
- **禁止编造**：基础报告只做统计与原文转述，不产生新的领域结论。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tju_info_retrieval.models.enhanced_summary import (
    READING_PRIORITY_LABELS,
)
from tju_info_retrieval.models.library import LibraryRecord
from tju_info_retrieval.models.report_context import ReportContext
from tju_info_retrieval.models.research_report import (
    BASE_SECTION_KEYS,
    NOT_GENERATED_REASON_TEXT,
    NOT_GENERATED_TEXT,
    REPORT_PROMPT_VERSION,
    REPORT_SECTION_KEYS,
    REPORT_SECTION_LABELS,
    SECTION_EXISTING_DIRECTIONS,
    SECTION_OVERVIEW,
    STATUS_DRAFT,
    STATUS_GENERATED,
    ReportSection,
    ReportSource,
    ResearchReport,
    build_generation_scope,
)
from tju_info_retrieval.models.research_profile import DIRECTION_MATCH_LABELS
from tju_info_retrieval.services.library_store import LibraryStore
from tju_info_retrieval.services.prompts.research_report_prompt import (
    ReportGroundingError,
    ReportParseError,
)
from tju_info_retrieval.services.report_cache import (
    ReportCache,
    paper_version_tag,
)
from tju_info_retrieval.services.research_profile_store import ResearchProfileStore
from tju_info_retrieval.services.research_report_store import ResearchReportStore

# 数量约束
REPORT_MIN_PAPERS_SOFT = 5      # 低于此值 → 允许但提示
REPORT_MAX_PAPERS = 20          # 高于此值 → 拒绝

# 结果状态
RESULT_GENERATED = "generated"
RESULT_DRAFT = "draft"
RESULT_REJECTED = "rejected"
RESULT_CACHED = "cached"

# 降级原因
DEGRADED_NOT_CONFIGURED = "not_configured"
DEGRADED_PROVIDER_ERROR = "provider_error"
DEGRADED_EMPTY_SECTIONS = "empty_sections"
DEGRADED_SOURCE_VALIDATION = "source_validation"
DEGRADED_PARSE = "parse"

DEGRADED_MESSAGES = {
    DEGRADED_NOT_CONFIGURED: "AI 报告生成未配置，已生成基础报告（未调用 AI）。",
    DEGRADED_PROVIDER_ERROR: "AI 报告生成失败，已生成基础报告（未调用 AI）。",
    DEGRADED_EMPTY_SECTIONS: "AI 未返回可用章节，已生成基础报告（未调用 AI）。",
    DEGRADED_SOURCE_VALIDATION: "AI 报告来源标注不符合要求（空来源 / 越界 / "
                                "无法对应），已生成基础报告（未调用 AI）。",
    DEGRADED_PARSE: "AI 报告结果解析失败，已生成基础报告（未调用 AI）。",
}

_DOC_TYPE_LABELS = {
    "experimental_method": "实验/方法研究",
    "review": "综述",
    "theory_model": "理论/建模",
    "application_case": "应用/案例研究",
    "perspective": "观点/展望",
    "other": "其他",
    "uncertain": "类型不确定",
    "": "未分析",
}


def report_source_from_record(record: LibraryRecord, index: int) -> ReportSource:
    """把 `LibraryRecord` 转成**生成时快照** `ReportSource`。

    只复制值（字符串/列表），不持有记录实例——论文库后续变化不影响报告。
    """
    return ReportSource(
        paper_id=str(getattr(record, "id", "") or ""),
        title=str(getattr(record, "title", "") or ""),
        authors=list(getattr(record, "authors", []) or []),
        year="" if getattr(record, "year", None) in (None, "")
        else str(record.year),
        doi=str(getattr(record, "doi", "") or ""),
        abstract=str(getattr(record, "abstract", "") or ""),
        artifact_type=str(getattr(record, "artifact_type", "") or "paper"),
        direction_match_level=str(
            getattr(record, "direction_match_level", "") or ""),
        reading_priority=str(getattr(record, "reading_priority", "") or ""),
        source=str(getattr(record, "source", "") or ""),
        document_type=str(getattr(record, "document_type", "") or ""),
        index=index,
    )


def source_materials_from_record(record: LibraryRecord) -> dict:
    """提取该论文**已存在**的分析材料（不触发任何新分析）。"""
    summary = getattr(record, "enhanced_summary", None) or {}
    basic = getattr(record, "basic_summary", None) or {}
    materials: dict = {}
    for key in ("research_background", "technical_route", "innovation_points",
                "relation_to_user_direction", "reading_recommendation",
                "limitations"):
        value = str(summary.get(key) or "").strip()
        if value:
            materials[key] = value
    if basic:
        for key in ("research_content", "core_technology", "main_results",
                    "application_value"):
            value = str(basic.get(key) or "").strip()
            if value:
                materials[f"basic_{key}"] = value
    return materials


@dataclass
class ReportGenerationResult:
    """生成结果（供 UI 直接展示；不抛异常）。"""

    ok: bool = False
    status: str = RESULT_REJECTED
    report: ResearchReport | None = None
    saved_path: str = ""
    message: str = ""
    warning: str = ""
    from_cache: bool = False
    degraded_reason: str = ""
    provider_calls: int = 0
    validation_problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "report": self.report.to_dict() if self.report else None,
            "saved_path": self.saved_path,
            "message": self.message,
            "warning": self.warning,
            "from_cache": self.from_cache,
            "degraded_reason": self.degraded_reason,
            "provider_calls": self.provider_calls,
            "validation_problems": list(self.validation_problems),
        }

    @classmethod
    def from_dict(cls, data) -> "ReportGenerationResult":
        """从 to_dict() 结果还原（Worker 跨线程传递用；脏数据安全降级）。"""
        data = data if isinstance(data, dict) else {}
        raw_report = data.get("report")
        report = None
        if isinstance(raw_report, dict) and raw_report:
            report = ResearchReport.from_dict(raw_report)
        return cls(
            ok=bool(data.get("ok")),
            status=str(data.get("status") or RESULT_REJECTED),
            report=report,
            saved_path=str(data.get("saved_path") or ""),
            message=str(data.get("message") or ""),
            warning=str(data.get("warning") or ""),
            from_cache=bool(data.get("from_cache")),
            degraded_reason=str(data.get("degraded_reason") or ""),
            provider_calls=int(data.get("provider_calls") or 0),
            validation_problems=list(data.get("validation_problems") or []),
        )


class ReportService:
    """论文集合 → 科研调研报告（独立于 SummaryService）。"""

    def __init__(
        self,
        provider=None,
        store: ResearchReportStore | None = None,
        cache: ReportCache | None = None,
        library_store: LibraryStore | None = None,
        profile_store: ResearchProfileStore | None = None,
        user_research_direction: str = "",
    ) -> None:
        self._provider = provider
        self._store = store or ResearchReportStore()
        self._cache = cache or ReportCache()
        self._library = library_store or LibraryStore()
        self._profile_store = profile_store or ResearchProfileStore()
        self._direction = user_research_direction

    # ---------- 状态 ----------

    @property
    def configured(self) -> bool:
        """是否具备可用的报告 Provider（本阶段仅注入时才有）。"""
        return self._provider is not None

    @property
    def provider_name(self) -> str:
        return str(getattr(self._provider, "name", "") or "")

    def set_provider(self, provider) -> None:
        self._provider = provider

    # ---------- 主流程 ----------

    def generate_report(self, paper_ids, title: str | None = None
                        ) -> ReportGenerationResult:
        """生成（并保存）调研报告；任何失败都降级，不抛异常给 UI。"""
        ids = [str(p) for p in (paper_ids or []) if str(p).strip()]
        records = self._load_records(ids)

        # 2) 数量校验
        rejection = self._check_count(len(records), len(ids))
        if rejection is not None:
            return rejection
        warning = self._soft_warning(len(records))

        # 3) ReportContext（一律快照）
        context = self._build_context(records, title)

        # 4) 缓存
        cache_key = self._cache_key(records, context)
        if cache_key:
            cached = self._cache.get(cache_key)
            if cached is not None:
                # 显式标题优先于缓存中的标题（重命名不重算，也不浪费 API）
                report = cached
                if title and str(title) != cached.title:
                    report = ResearchReport.from_dict(cached.to_dict())
                    report.title = str(title)
                    try:
                        self._store.save(report)
                    except Exception:  # noqa: BLE001 - 保存失败不影响返回
                        pass
                return ReportGenerationResult(
                    ok=True, status=RESULT_CACHED, report=report,
                    saved_path=str(self._store.path_for(report.id)),
                    message=f"已从缓存读取报告（{report.paper_count} 篇）。",
                    warning=warning, from_cache=True)

        # 5) Provider（未配置 → 直接降级，且不产生任何调用）
        if not self.configured:
            report = self.build_basic_report(
                context, reason=DEGRADED_NOT_CONFIGURED)
            result = self._finalize(report, context, cache_key, warning,
                                    DEGRADED_NOT_CONFIGURED)
            return result

        try:
            produced = self._provider.generate(context)
        except ReportGroundingError as exc:  # 来源标注违规 → 来源校验失败
            report = self.build_basic_report(
                context, reason=DEGRADED_SOURCE_VALIDATION)
            result = self._finalize(
                report, context, cache_key, warning, DEGRADED_SOURCE_VALIDATION,
                provider_calls=self._provider_calls())
            result.validation_problems = [str(exc)]
            return result
        except ReportParseError as exc:  # JSON 契约违规 → 解析失败
            report = self.build_basic_report(context, reason=DEGRADED_PARSE)
            result = self._finalize(
                report, context, cache_key, warning, DEGRADED_PARSE,
                provider_calls=self._provider_calls())
            result.message = f"{DEGRADED_MESSAGES[DEGRADED_PARSE]}（{type(exc).__name__}）"
            return result
        except Exception as exc:  # noqa: BLE001 - 统一降级，不抛给 UI
            report = self.build_basic_report(
                context, reason=DEGRADED_PROVIDER_ERROR)
            result = self._finalize(
                report, context, cache_key, warning, DEGRADED_PROVIDER_ERROR,
                provider_calls=self._provider_calls())
            result.message = (f"{DEGRADED_MESSAGES[DEGRADED_PROVIDER_ERROR]}"
                              f"（{type(exc).__name__}）")
            return result

        # 6) 归一 + 来源校验（归一失败同样降级，绝不抛给 UI）
        try:
            report = self._normalize(produced, context, title)
        except Exception as exc:  # noqa: BLE001
            degraded = self.build_basic_report(
                context, reason=DEGRADED_PROVIDER_ERROR)
            result = self._finalize(
                degraded, context, cache_key, warning, DEGRADED_PROVIDER_ERROR,
                provider_calls=self._provider_calls())
            result.message = (f"{DEGRADED_MESSAGES[DEGRADED_PROVIDER_ERROR]}"
                              f"（{type(exc).__name__}）")
            return result
        problems = self.validate_sources(report, context)
        if problems:
            degraded = self.build_basic_report(
                context, reason=DEGRADED_SOURCE_VALIDATION)
            degraded.id = report.id          # 保留同一次生成的身份
            result = self._finalize(
                degraded, context, cache_key, warning,
                DEGRADED_SOURCE_VALIDATION,
                provider_calls=self._provider_calls())
            result.validation_problems = problems
            return result

        # 7) 成功
        report.status = STATUS_GENERATED
        report.provider = self.provider_name
        result = self._finalize(report, context, cache_key, warning, "",
                                provider_calls=self._provider_calls())
        return result

    # ---------- 内部：输入 ----------

    def _load_records(self, ids: list[str]) -> list[LibraryRecord]:
        """按 id 读取库记录（保持用户选择顺序；只读，不写库）。"""
        if not ids:
            return []
        by_id = {r.id: r for r in self._library.load_all()}
        return [by_id[i] for i in ids if i in by_id]

    @staticmethod
    def _check_count(found: int, requested: int
                     ) -> ReportGenerationResult | None:
        """数量校验：0 拒绝 / >20 拒绝（先给上限提示，再给未找到提示）。"""
        if requested == 0:
            return ReportGenerationResult(
                ok=False, status=RESULT_REJECTED,
                message="请先选择要纳入报告的论文（建议 5–20 篇）。")
        if requested > REPORT_MAX_PAPERS or found > REPORT_MAX_PAPERS:
            over = max(requested, found)
            return ReportGenerationResult(
                ok=False, status=RESULT_REJECTED,
                message=f"最多支持 {REPORT_MAX_PAPERS} 篇论文（当前 {over} 篇）；"
                        "请先筛选后再生成。")
        if found == 0:
            return ReportGenerationResult(
                ok=False, status=RESULT_REJECTED,
                message="所选论文在论文库中未找到，请重新选择。")
        return None

    @staticmethod
    def _soft_warning(count: int) -> str:
        if count < REPORT_MIN_PAPERS_SOFT:
            return (f"当前仅 {count} 篇，报告覆盖度有限"
                    f"（建议 {REPORT_MIN_PAPERS_SOFT}–{REPORT_MAX_PAPERS} 篇）。")
        return ""

    def _build_context(self, records: list[LibraryRecord],
                       title: str | None) -> ReportContext:
        papers = [report_source_from_record(r, index)
                  for index, r in enumerate(records, start=1)]
        materials = {p.paper_id: source_materials_from_record(r)
                     for p, r in zip(papers, records) if p.paper_id}
        profile = self._profile_store.load()
        profile_snapshot = profile.to_dict()
        scope = build_generation_scope(
            paper_count=len(papers),
            profile_version=profile_snapshot.get("profile_version"),
            library_filter={},
        )
        return ReportContext(
            papers=papers,
            profile_snapshot=profile_snapshot,
            generation_scope=scope,
            source_materials=materials,
            title_hint=str(title or ""),
        )

    def _cache_key(self, records: list[LibraryRecord],
                   context: ReportContext) -> str:
        return self._cache.key_for(
            paper_ids=context.paper_ids(),
            paper_versions=[paper_version_tag(r) for r in records],
            profile_version=context.profile_version(),
            prompt_version=REPORT_PROMPT_VERSION,
            provider=self.provider_name,
            model=str(getattr(self._provider, "model", "") or ""),
        )

    def _provider_calls(self) -> int:
        return int(getattr(self._provider, "calls", 0) or 0)

    # ---------- 内部：归一 / 校验 / 保存 ----------

    def _normalize(self, produced, context: ReportContext,
                   title: str | None) -> ResearchReport:
        """把 Provider 产物归一为完整报告（身份/来源/版本由本方法填充）。"""
        if isinstance(produced, ResearchReport):
            sections = list(produced.sections)
            produced_title = produced.title
        elif isinstance(produced, dict):          # 宽松：允许 dict 产物
            sections = [ReportSection.from_dict(x)
                        for x in (produced.get("sections") or [])
                        if isinstance(x, dict)]
            produced_title = str(produced.get("title") or "")
        else:
            raise TypeError("ReportProvider 必须返回 ResearchReport 或 dict")

        for order, section in enumerate(sections):
            if not section.title:
                section.title = REPORT_SECTION_LABELS.get(section.key,
                                                           section.key)
            section.order = order
        report = ResearchReport(
            # 标题优先级：用户显式指定 > Provider 生成 > 服务默认（含量与画像）
            title=title or produced_title or self._default_title(context),
            sections=sections,
            papers=list(context.papers),
            profile_snapshot=dict(context.profile_snapshot),
            generation_scope=dict(context.generation_scope),
            provider=self.provider_name,
            model=str(getattr(self._provider, "model", "") or ""),
            report_prompt_version=REPORT_PROMPT_VERSION,
            status=STATUS_DRAFT,
        )
        return report

    def validate_sources(self, report: ResearchReport,
                         context: ReportContext | None = None) -> list[str]:
        """来源可追溯校验：返回问题列表（空列表 = 通过）。

        规则：
        1. 报告必须有章节；
        2. 每个 `source_papers` 里的 id 必须存在于 `report.papers`；
        3. 至少有一个章节引用了来源（否则等于没有可追溯内容）。
        """
        problems: list[str] = []
        known = {p.paper_id for p in report.papers if p.paper_id}
        if context is not None:
            known |= context.known_paper_ids()
        if not report.sections:
            problems.append("报告没有任何章节")
            return problems
        dangling = report.dangling_source_ids()
        if dangling:
            problems.append(
                "章节引用了不存在的来源论文：" + "、".join(sorted(dangling)))
        if not report.traced_paper_ids():
            problems.append("报告未标注任何来源论文")
        return problems

    def _finalize(self, report: ResearchReport, context: ReportContext,
                  cache_key: str, warning: str, degraded_reason: str,
                  provider_calls: int = 0) -> ReportGenerationResult:
        """补全元信息 → 保存 → 写缓存 → 组装结果。"""
        report.papers = list(context.papers)
        report.profile_snapshot = dict(context.profile_snapshot)
        report.generation_scope = dict(context.generation_scope)
        report.report_prompt_version = REPORT_PROMPT_VERSION
        if degraded_reason and report.status != STATUS_DRAFT:
            report.status = STATUS_DRAFT
        if degraded_reason:
            report.provider = report.provider or self.provider_name

        saved_path = ""
        try:
            saved_path = str(self._store.save(report))
        except Exception:  # noqa: BLE001 - 保存失败不抛给 UI
            saved_path = ""

        if cache_key and not degraded_reason:
            self._cache.put(cache_key, report)

        status = RESULT_DRAFT if degraded_reason else RESULT_GENERATED
        message = DEGRADED_MESSAGES.get(degraded_reason, "")
        if not message:
            message = (f"报告已生成（{report.paper_count} 篇，"
                       f"来源可追溯）。")
        return ReportGenerationResult(
            ok=True,
            status=status,
            report=report,
            saved_path=saved_path,
            message=message,
            warning=warning,
            degraded_reason=degraded_reason,
            provider_calls=provider_calls,
        )

    def _default_title(self, context: ReportContext) -> str:
        area = str((context.profile_snapshot or {}).get("research_area")
                   or "").strip()
        head = f"{area} 文献调研报告" if area else "科研调研报告"
        return f"{head}（{context.paper_count} 篇）"

    # ---------- 降级：基础报告 ----------

    def build_basic_report(self, context: ReportContext,
                           reason: str = DEGRADED_PROVIDER_ERROR
                           ) -> ResearchReport:
        """基础报告（不调用 AI，只用论文库既有数据；禁止编造）。"""
        return ResearchReport(
            title=self._default_title(context),
            papers=list(context.papers),
            profile_snapshot=dict(context.profile_snapshot),
            generation_scope=dict(context.generation_scope),
            sections=self._basic_sections(context),
            provider=self.provider_name,
            model=str(getattr(self._provider, "model", "") or ""),
            report_prompt_version=REPORT_PROMPT_VERSION,
            status=STATUS_DRAFT,
        )

    def _basic_sections(self, context: ReportContext) -> list[ReportSection]:
        """必含「文献集合概览」「已有研究方向」；其余标注未调用 AI。"""
        papers = list(context.papers)
        all_ids = [p.paper_id for p in papers if p.paper_id]
        sections = [
            ReportSection(
                title=REPORT_SECTION_LABELS[SECTION_OVERVIEW],
                content=self._overview_text(papers, context),
                order=0, key=SECTION_OVERVIEW, source_papers=all_ids),
            ReportSection(
                title=REPORT_SECTION_LABELS[SECTION_EXISTING_DIRECTIONS],
                content=self._existing_directions_text(papers, context),
                order=1, key=SECTION_EXISTING_DIRECTIONS,
                source_papers=all_ids),
        ]
        for offset, key in enumerate(REPORT_SECTION_KEYS, start=2):
            sections.append(ReportSection(
                title=REPORT_SECTION_LABELS.get(key, key),
                content=NOT_GENERATED_REASON_TEXT,
                order=offset, key=key, source_papers=[], insufficient=True))
        return sections

    @staticmethod
    def _overview_text(papers: list[ReportSource],
                       context: ReportContext) -> str:
        """文献集合概览：全部来自快照统计，不含任何领域结论。"""
        if not papers:
            return "本次报告没有纳入任何论文。"
        by_year = Counter(p.year or "年份未知" for p in papers)
        by_source = Counter(p.source or "来源未知" for p in papers)
        by_type = Counter(_DOC_TYPE_LABELS.get(p.document_type or "",
                                              p.document_type or "未分析")
                          for p in papers)
        by_artifact = Counter(p.artifact_type or "paper" for p in papers)

        lines = [f"本次报告共纳入 {len(papers)} 篇成果。", ""]
        lines.append("年份分布：" + "、".join(
            f"{k}（{v}）" for k, v in sorted(by_year.items(), reverse=True)))
        lines.append("来源分布：" + "、".join(
            f"{k}（{v}）" for k, v in by_source.most_common()))
        lines.append("成果类型：" + "、".join(
            f"{k}（{v}）" for k, v in by_artifact.most_common()))
        lines.append("文献类型：" + "、".join(
            f"{k}（{v}）" for k, v in by_type.most_common()))
        lines.append("")
        lines.append("论文清单：")
        for paper in papers:
            lines.append(f"[P{paper.index}] {paper.display_name()}")
        return "\n".join(lines)

    @staticmethod
    def _existing_directions_text(papers: list[ReportSource],
                                  context: ReportContext) -> str:
        """已有研究方向：按既有分析字段分组 + 画像快照（不新增判断）。"""
        lines = [f"科研画像（生成时快照）：{context.profile_summary_text()}", ""]

        analyzed = [p for p in papers
                    if p.direction_match_level or p.reading_priority]
        lines.append("按既有 AI 分析的方向匹配等级分组：")
        if analyzed:
            grouped: dict[str, list[ReportSource]] = {}
            for paper in analyzed:
                label = DIRECTION_MATCH_LABELS.get(
                    paper.direction_match_level,
                    paper.direction_match_level or "未标注")
                grouped.setdefault(label, []).append(paper)
            for label, group in grouped.items():
                ids = "、".join(f"[P{p.index}]" for p in group)
                lines.append(f"- {label}：{ids}")
        else:
            lines.append("- 所选论文尚未进行 AI 增强分析，暂无可分组信息。")

        lines.append("")
        lines.append("按既有阅读优先级分组：")
        prioritized = [p for p in papers if p.reading_priority]
        if prioritized:
            grouped_priority: dict[str, list[ReportSource]] = {}
            for paper in prioritized:
                label = READING_PRIORITY_LABELS.get(
                    paper.reading_priority, paper.reading_priority)
                grouped_priority.setdefault(label, []).append(paper)
            for label, group in grouped_priority.items():
                ids = "、".join(f"[P{p.index}]" for p in group)
                lines.append(f"- {label}：{ids}")
        else:
            lines.append("- 所选论文尚未标注阅读优先级。")

        excluded = (context.profile_snapshot or {}).get("excluded_topics") or []
        if excluded:
            lines.append("")
            lines.append("画像中标注「暂不关注」的主题："
                         + "、".join(str(x) for x in excluded))
        lines.append("")
        lines.append("（本节仅汇总论文库已有字段与画像快照，未调用 AI。）")
        return "\n".join(lines)
