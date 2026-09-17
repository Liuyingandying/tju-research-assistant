"""LLMReportProvider（v0.18 Phase 2.9-C-D-B）。

基于 `OpenAICompatibleProvider` 生成科研调研报告：

    ReportContext
        ↓ build_report_prompt（r1，SOURCE TRACE）
        ↓ OpenAICompatibleProvider.complete()      ← 唯一网络出口
        ↓ parse_report_sections（契约校验）
    ResearchReport（title + sections；其余字段由 ReportService 填充）

约束：

1. **不直接使用 requests**：网络只经 `OpenAICompatibleProvider.complete()`；
2. **不降级**：任何失败（未配置 / 网络 / HTTP / 超时 / 解析 / Grounding）都
   **向上抛异常**，由 `ReportService` 统一降级为基础报告；
3. **只负责 title 与 sections**：`id` / `created_time` / `papers` /
   `profile_snapshot` / `generation_scope` / `provider` / `model` /
   `report_prompt_version` / `status` 全部由 Service 填充；
4. **零论文库依赖**：本模块不 import `models.library` / `LibraryStore`，
   输入只有 `ReportContext`（快照）。

`ReportService` 无需修改即可从 `MockReportProvider` 切换到本 Provider：
两者都实现 `ReportProvider`（`name` / `model` / `calls` / `generate(context)`）。
"""
from __future__ import annotations

from tju_info_retrieval.models.report_context import ReportContext
from tju_info_retrieval.models.research_report import ResearchReport
from tju_info_retrieval.services.llm_provider import (
    OpenAICompatibleProvider,
    load_provider_config,
)
from tju_info_retrieval.services.prompts.research_report_prompt import (
    REPORT_PROMPT_VERSION,
    REPORT_SYSTEM_PROMPT,
    build_report_prompt,
    parse_report_sections,
)

LLM_REPORT_PROVIDER_NAME = "openai-compatible"


class LLMReportProvider:
    """通过 OpenAI-compatible LLM 生成科研调研报告（真实网络）。"""

    name = LLM_REPORT_PROVIDER_NAME

    def __init__(
        self,
        llm_client: OpenAICompatibleProvider | None = None,
        prompt_builder=None,
        system_prompt: str | None = None,
        parse=None,
        timeout_s: float | None = None,
    ) -> None:
        self._client = llm_client or OpenAICompatibleProvider(
            load_provider_config())
        self._build = prompt_builder or build_report_prompt
        self._system = system_prompt or REPORT_SYSTEM_PROMPT
        self._parse = parse or parse_report_sections
        self._timeout_s = timeout_s
        self.calls = 0

    @property
    def model(self) -> str:
        return str(getattr(self._client, "model", "") or "")

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._client, "enabled", False))

    def generate(self, context: ReportContext) -> ResearchReport:
        """生成报告；任何失败向上抛（Service 统一降级）。"""
        self.calls += 1
        self._client.validate()                     # 未配置 → LLMConfigError
        prompt = self._build(context)
        raw = self._client.complete(
            prompt, system_prompt=self._system, timeout_s=self._timeout_s)
        title, sections = self._parse(raw, context)
        return ResearchReport(title=title, sections=sections)
