"""ReportProvider 协议（v0.18 Phase 2.9-C-C-B）。

与 `SummaryProvider`（单篇摘要）**完全独立**：
- 输入是 `ReportContext`（论文快照集合 + 画像快照），不是单条 EvidenceBundle；
- 输出是 `ResearchReport`（多章节 + 来源映射），不是 EnhancedSummary；
- 本模块不 import `EnhancedSummary` / `SummaryProvider` / `SummaryService`。

Provider 契约（宽松版：Provider 只需产出 `title` 与 `sections`）：

- `ReportService` 负责填充 `id` / `created_time` / `papers` /
  `profile_snapshot` / `generation_scope` / `provider` / `model` /
  `report_prompt_version` / `status`；
- Provider 产出的每个 `ReportSection.source_papers` 必须是
  `ReportContext.papers[].paper_id` 的子集（由 ReportService 校验）；
- Provider **失败必须抛异常**（不要自己吞掉）：由 ReportService 统一降级，
  这样降级策略只有一处，便于测试与审计。

本阶段只提供 `MockReportProvider`（测试用；固定输出、绝不联网）。
真实 LLM Provider 留待 Phase 2.9-C-D。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from tju_info_retrieval.models.report_context import ReportContext
from tju_info_retrieval.models.research_report import (
    REPORT_SECTION_KEYS,
    REPORT_SECTION_LABELS,
    ReportSection,
    ResearchReport,
)

MOCK_REPORT_PROVIDER_NAME = "mock"


@runtime_checkable
class ReportProvider(Protocol):
    """报告生成 Provider 协议。"""

    name: str

    def generate(self, context: ReportContext) -> ResearchReport:
        """根据上下文生成报告（失败时抛异常，由调用方降级）。"""
        ...


class MockReportProvider:
    """测试/演示用 Provider：固定输出，绝不调用任何网络或 API。

    行为：
    - 每个 AI 章节生成一行固定文案，并绑定**第一篇论文**作为来源
      （保证 source_papers 合法、可追溯）；
    - 可注入 `raises` 模拟 Provider 异常、`dangling_sources` 模拟
      来源越界（用于验证 ReportService 的来源校验与降级）。
    """

    name = MOCK_REPORT_PROVIDER_NAME

    def __init__(
        self,
        *,
        raises: Exception | None = None,
        dangling_sources: bool = False,
        empty_sections: bool = False,
        title: str = "Mock 调研报告",
        template: str = "【{label}】基于所选论文的固定测试内容。[P1]",
    ) -> None:
        self._raises = raises
        self._dangling_sources = dangling_sources
        self._empty_sections = empty_sections
        self._title = title
        self._template = template
        self.calls = 0

    def generate(self, context: ReportContext) -> ResearchReport:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        if self._empty_sections:
            return ResearchReport(title=self._title, sections=[])

        first_id = context.papers[0].paper_id if context.papers else ""
        sections: list[ReportSection] = []
        for order, key in enumerate(REPORT_SECTION_KEYS):
            sources = ["dangling-paper-id"] if self._dangling_sources \
                else ([first_id] if first_id else [])
            sections.append(ReportSection(
                title=REPORT_SECTION_LABELS.get(key, key),
                content=self._template.format(
                    label=REPORT_SECTION_LABELS.get(key, key)),
                order=order,
                key=key,
                source_papers=sources,
            ))
        return ResearchReport(title=self._title, sections=sections)


class ReportingProviderError(RuntimeError):
    """Provider 内部错误（便于测试构造可读异常）。"""
