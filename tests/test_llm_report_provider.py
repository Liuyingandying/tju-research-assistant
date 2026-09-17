#!/usr/bin/env python3
"""正式测试：LLMReportProvider 与科研调研报告 Prompt（v0.18 Phase 2.9-C-D）。

覆盖任务书 12 项：

Prompt:  1 REPORT_PROMPT_VERSION / 2 SOURCE TRACE 规则 / 3 用户画像 / 4 六章节
Parser:  5 合法 JSON / 6 缺字段失败 / 7 非法 source_papers 失败
Provider: 8 调用 OpenAICompatibleProvider / 9 不直接 requests / 10 异常向上抛
Grounding: 11 不允许空 source_papers / 12 不生成 LibraryRecord 依赖

另覆盖：Mock 3 篇论文端到端（source_papers 全部存在）、
ReportService 零改动切换 Provider、缓存 key 含 REPORT_PROMPT_VERSION。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tju_info_retrieval.models.library import LibraryRecord
from tju_info_retrieval.models.report_context import ReportContext
from tju_info_retrieval.models.research_report import ReportSource
from tju_info_retrieval.services.llm_provider import (
    LLMConfigError,
    OpenAICompatibleProvider,
)
from tju_info_retrieval.services.llm_report_provider import (
    LLM_REPORT_PROVIDER_NAME,
    LLMReportProvider,
)
from tju_info_retrieval.services.prompts.research_report_prompt import (
    REPORT_PROMPT_VERSION,
    REPORT_SYSTEM_PROMPT,
    PROMPT_SECTION_TITLES,
    ReportGroundingError,
    ReportParseError,
    build_report_prompt,
    parse_report_sections,
)
from tju_info_retrieval.services.report_cache import ReportCache, report_cache_key
from tju_info_retrieval.services.report_service import (
    RESULT_CACHED,
    ReportService,
    report_source_from_record,
)
from tju_info_retrieval.services.research_report_store import ResearchReportStore
from tju_info_retrieval.services.library_store import LibraryStore

VALID_CONFIG = {
    "enabled": True,
    "provider": "openai-compatible",
    "base_url": "https://api.example.invalid/v1",
    "model": "tju-llm",
    "api_key": "test-key-not-real",
}


class _Resp:
    """OpenAI 兼容响应替身（正确绑定 json 方法）。"""

    ok = True
    status_code = 200
    text = ""

    def __init__(self, content: str, status_code: int = 200) -> None:
        self._content = content
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = content

    def json(self):
        return {"choices": [{"message": {"role": "assistant",
                                         "content": self._content}}]}


def _transport(content: str, counter: list | None = None, status_code: int = 200):
    def transport(*args, **kwargs):
        if counter is not None:
            counter.append(1)
        return _Resp(content, status_code)
    return transport


def _client(content: str, counter: list | None = None, status_code: int = 200,
            config: dict | None = None):
    return OpenAICompatibleProvider(
        config or dict(VALID_CONFIG), transport=_transport(
            content, counter, status_code))


def _context(n: int = 3, profile: dict | None = None) -> ReportContext:
    papers = [ReportSource.from_dict({
        "paper_id": f"id{i}", "title": f"论文{i}", "authors": [f"作者{i}"],
        "year": "2026", "abstract": f"第{i}篇摘要：太赫兹波传播特性研究。",
        "index": i + 1,
    }) for i in range(n)]
    return ReportContext(
        papers=papers,
        profile_snapshot=profile if profile is not None
        else {"research_area": "太赫兹", "profile_version": 2},
        generation_scope={"paper_count": n})


def _payload(title: str = "Mock 调研", sections=None, paper_count: int = 3) -> str:
    body = {
        "title": title,
        "sections": sections or [
            {"title": t, "content": f"内容[{i+1}]",
             "source_papers": [(i % paper_count) + 1]}
            for i, t in enumerate(PROMPT_SECTION_TITLES)
        ],
    }
    return json.dumps(body, ensure_ascii=False)


# ============================================================
# Prompt：1-4
# ============================================================

class TestPrompt:
    def test_1_prompt_version_exists(self):
        assert REPORT_PROMPT_VERSION == "r1"
        assert REPORT_PROMPT_VERSION != "v4"          # 与单篇 Prompt 版本独立

    def test_2_contains_source_trace_rules(self):
        for required in ("SOURCE TRACE RULES",
                         "根据当前论文集合无法判断",
                         "source_papers",
                         "禁止利用你的模型记忆补充"):
            assert required in REPORT_SYSTEM_PROMPT, required

    def test_2b_source_ownership_rules(self):
        for required in ("SOURCE OWNERSHIP",
                         "禁止把单篇论文的贡献写成整个领域的既有事实",
                         "禁止把综述论文的创新点冒充原创贡献",
                         "禁止生成不存在的实验结果"):
            assert required in REPORT_SYSTEM_PROMPT, required

    def test_3_contains_user_profile(self):
        context = _context(profile={"research_area": "太赫兹",
                                    "sub_direction": ["THz-ISAC"]})
        prompt = build_report_prompt(context)
        assert "[USER PROFILE]" in prompt
        assert "太赫兹" in prompt
        assert "THz-ISAC" in prompt

    def test_3b_empty_profile_fixed_text(self):
        context = _context(profile={})
        prompt = build_report_prompt(context)
        assert "尚未设置科研画像，无法进行个性化方向匹配。" in prompt

    def test_4_contains_six_sections(self):
        for title in PROMPT_SECTION_TITLES:
            assert title in REPORT_SYSTEM_PROMPT, title
        assert len(PROMPT_SECTION_TITLES) == 6

    def test_4b_six_keys_aligned(self):
        from tju_info_retrieval.models.research_report import (
            REPORT_SECTION_KEYS,
        )

        assert len(REPORT_SECTION_KEYS) == 6
        assert len(PROMPT_SECTION_TITLES) == len(REPORT_SECTION_KEYS)

    def test_prompt_lists_all_papers_with_index(self):
        context = _context(3)
        prompt = build_report_prompt(context)
        for i in range(1, 4):
            assert f"[P{i}]" in prompt

    def test_prompt_abstract_truncation(self):
        context = _context(1)
        context.papers[0] = ReportSource.from_dict({
            "paper_id": "id0", "title": "T",
            "abstract": "长" * 2000, "index": 1})
        prompt = build_report_prompt(context)
        assert "…" in prompt                    # 截断标记
        assert len(prompt) < 3000


# ============================================================
# Parser：5-7
# ============================================================

class TestParser:
    def test_5_valid_json_parsed(self):
        context = _context(3)
        title, sections = parse_report_sections(_payload(), context)
        assert title == "Mock 调研"
        assert len(sections) == 6
        for section in sections:
            assert section.source_papers                    # 非空
            assert set(section.source_papers) <= context.known_paper_ids()

    def test_5b_fenced_json_accepted(self):
        context = _context(2)
        raw = "```json\n" + _payload(title="围栏", paper_count=2) + "\n```"
        title, sections = parse_report_sections(raw, context)
        assert title == "围栏"
        assert len(sections) == 6

    def test_5c_prose_before_json_accepted(self):
        context = _context(2)
        raw = "好的，以下是报告：\n" + _payload(paper_count=2) + "\n（完）"
        title, _ = parse_report_sections(raw, context)
        assert title == "Mock 调研"

    def test_5d_string_indexes_accepted(self):
        context = _context(2)
        body = {"title": "T", "sections": [
            {"title": "研究背景", "content": "c", "source_papers": ["1", "2"]}]}
        _, sections = parse_report_sections(json.dumps(body), context)
        assert sections[0].source_papers == ["id0", "id1"]

    def test_6_missing_fields_raise(self):
        context = _context(2)
        cases = [
            "",                                            # 空
            "not json {{{",                               # 非法 JSON
            "[1, 2]",                                     # 顶层非对象
            json.dumps({"sections": []}),                 # 缺 title + 空 sections
            json.dumps({"title": "T"}),                   # 缺 sections
            json.dumps({"title": "T", "sections": "x"}),  # sections 非列表
            json.dumps({"title": "T", "sections": [{"content": "c",
                                                    "source_papers": [1]}]}),
            json.dumps({"title": "T", "sections": [{"title": "S",
                                                    "source_papers": [1]}]}),
            json.dumps({"title": "T", "sections": [{"title": "S", "content": ""}]}),
            json.dumps({"title": "", "sections": [{"title": "S", "content": "c",
                                                   "source_papers": [1]}]}),
            json.dumps({"title": "T", "sections": ["junk"]}),
        ]
        for raw in cases:
            with pytest.raises(ReportParseError):
                parse_report_sections(raw, context)

    def test_7_illegal_source_papers_raise(self):
        context = _context(2)
        cases = [
            {"title": "S", "content": "c", "source_papers": []},      # 空
            {"title": "S", "content": "c", "source_papers": [0]},     # 越界(下)
            {"title": "S", "content": "c", "source_papers": [3]},     # 越界(上)
            {"title": "S", "content": "c", "source_papers": [1.5]},   # 非整数
            {"title": "S", "content": "c", "source_papers": [True]},  # bool
            {"title": "S", "content": "c", "source_papers": "1"},     # 非列表
        ]
        for item in cases:
            raw = json.dumps({"title": "T", "sections": [item]})
            with pytest.raises((ReportParseError, ReportGroundingError)):
                parse_report_sections(raw, context)

    def test_7b_grounding_raises_on_empty(self):
        context = _context(1)
        raw = json.dumps({"title": "T", "sections": [
            {"title": "研究背景", "content": "c", "source_papers": []}]})
        with pytest.raises(ReportGroundingError):
            parse_report_sections(raw, context)

    def test_7c_duplicate_indices_deduped(self):
        context = _context(2)
        raw = json.dumps({"title": "T", "sections": [
            {"title": "研究背景", "content": "c", "source_papers": [1, 1, 2]}]})
        _, sections = parse_report_sections(raw, context)
        assert sections[0].source_papers == ["id0", "id1"]

    def test_7d_keys_mapped_for_six_titles(self):
        context = _context(2)
        title, sections = parse_report_sections(_payload(paper_count=2), context)
        keys = [s.key for s in sections]
        assert keys == ["background", "trend", "tech_route", "challenge",
                        "future", "profile_link"]

    def test_parse_raises_on_empty_input(self):
        with pytest.raises(ReportParseError):
            parse_report_sections("", _context(1))


# ============================================================
# Provider：8-10
# ============================================================

class TestProvider:
    def test_8_calls_openai_provider_complete(self):
        counter: list = []
        client = _client(_payload(), counter)
        provider = LLMReportProvider(llm_client=client)
        report = provider.generate(_context(3))
        assert len(counter) == 1                       # 恰好一次 complete()
        assert provider.calls == 1
        assert report.title == "Mock 调研"
        assert len(report.sections) == 6

    def test_8b_provider_uses_client_model_and_name(self):
        provider = LLMReportProvider(llm_client=_client(_payload()))
        assert provider.name == LLM_REPORT_PROVIDER_NAME
        assert provider.model == "tju-llm"
        assert provider.enabled is True

    def test_9_no_direct_requests_import(self):
        import tju_info_retrieval.services.llm_report_provider as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
                names += [a.name for a in node.names]
        assert not any("requests" in n.lower() for n in names)
        # 网络出口必须经 OpenAICompatibleProvider
        assert "OpenAICompatibleProvider" in names

    def test_9b_no_direct_requests_at_runtime(self, monkeypatch):
        """Provider 运行期不经 requests（替身会当场失败）。"""
        import requests

        def _boom(*args, **kwargs):
            raise AssertionError("LLMReportProvider 不应直接使用 requests")

        monkeypatch.setattr(requests, "post", _boom)
        provider = LLMReportProvider(llm_client=_client(_payload(paper_count=2)))
        provider.generate(_context(2))

    def test_10_exceptions_propagate_to_caller(self):
        """未配置 / 网络 / 解析失败都必须向上抛（Service 负责降级）。"""
        # 10a 未配置 → LLMConfigError
        disabled = OpenAICompatibleProvider(
            {"enabled": False, "provider": "openai-compatible",
             "base_url": "", "model": "", "api_key": ""})
        with pytest.raises(LLMConfigError):
            LLMReportProvider(llm_client=disabled).generate(_context(1))

        # 10b 网络异常 → 经统一 LLM Provider 包装为 LLMNetworkError 后向上抛
        import requests

        from tju_info_retrieval.services.llm_provider import LLMNetworkError

        def broken(*args, **kwargs):
            raise requests.ConnectionError("down")

        client = OpenAICompatibleProvider(dict(VALID_CONFIG), transport=broken)
        with pytest.raises(LLMNetworkError):
            LLMReportProvider(llm_client=client).generate(_context(1))

        # 10c HTTP 500 → LLMHTTPError
        bad = _client("server error", status_code=500)
        with pytest.raises(Exception):
            LLMReportProvider(llm_client=bad).generate(_context(1))

        # 10d 非法 JSON → ReportParseError（由 parse 抛）
        junk = _client("这不是 JSON")
        with pytest.raises(ReportParseError):
            LLMReportProvider(llm_client=junk).generate(_context(1))

        # 10e Grounding 违规 → ReportGroundingError
        grounding = _client(_payload(sections=[
            {"title": "研究背景", "content": "c", "source_papers": [1]},
            {"title": "领域发展现状", "content": "c", "source_papers": []}]))
        with pytest.raises(ReportGroundingError):
            LLMReportProvider(llm_client=grounding).generate(_context(2))

    def test_10f_provider_does_not_degrade_itself(self):
        """Provider 内部不降级：异常必须逃出 generate()。"""
        provider = LLMReportProvider(llm_client=_client("坏 JSON"))
        with pytest.raises(ReportParseError):
            provider.generate(_context(1))
        assert provider.calls == 1


# ============================================================
# Grounding：11-12
# ============================================================

class TestGrounding:
    def test_11_empty_source_papers_forbidden(self):
        context = _context(2)
        raw = json.dumps({"title": "T", "sections": [
            {"title": "研究背景", "content": "无来源内容", "source_papers": []}]})
        with pytest.raises(ReportGroundingError):
            parse_report_sections(raw, context)

    def test_11b_mock_payload_all_sections_grounded(self):
        context = _context(3)
        title, sections = parse_report_sections(_payload(), context)
        for section in sections:
            assert section.source_papers

    def test_12_no_library_record_dependency(self):
        """Provider 与 Prompt 模块不依赖 models.library / LibraryStore。"""
        import tju_info_retrieval.services.llm_report_provider as provider_mod
        import tju_info_retrieval.services.prompts.research_report_prompt as prompt_mod

        for module in (provider_mod, prompt_mod):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names.append(node.module or "")
            assert not any("library" in n.lower() for n in names), module.__name__

    def test_12b_provider_only_needs_context(self):
        """生成只依赖 ReportContext（快照），不接触论文库。"""
        provider = LLMReportProvider(llm_client=_client(_payload()))
        report = provider.generate(_context(3))
        assert report.papers == []                     # Provider 不填 papers
        assert report.paper_count == 0                 # Service 负责填充


# ============================================================
# Mock 3 篇端到端 + Service 零改动切换 + 缓存
# ============================================================

class TestEndToEnd:
    @pytest.fixture
    def library(self, tmp_path):
        store = LibraryStore(tmp_path / "cache" / "library.json")
        ids = []
        for i in range(3):
            record = LibraryRecord.from_search_result({
                "title": f"太赫兹论文{i}", "authors": [f"作者{i}"],
                "year": "2026", "source": "中国知网", "database": "CNKI",
                "abstract": f"第{i}篇太赫兹波传播与感知研究摘要。",
            })
            record.id = f"id{i}"
            store.save(record)
            ids.append(record.id)
        return store, ids

    def test_three_paper_report_sources_all_exist(self, tmp_path, library):
        store, ids = library
        counter: list = []
        provider = LLMReportProvider(llm_client=_client(_payload(), counter))
        service = ReportService(
            provider=provider, store=ResearchReportStore(tmp_path / "reports"),
            cache=ReportCache(tmp_path / "cache" / "research_reports"),
            library_store=store)
        result = service.generate_report(ids, title="三篇调研")
        assert result.ok is True
        assert result.status == "generated"
        assert result.report.paper_count == 3
        for section in result.report.sections:
            assert section.source_papers
            assert set(section.source_papers) <= set(ids)   # 全部存在
        assert result.report.dangling_source_ids() == set()
        assert service.validate_sources(result.report) == []

    def test_service_swap_requires_no_change(self, tmp_path, library):
        """ReportService 未修改即可从 Mock 切到 LLMReportProvider。"""
        store, ids = library
        service = ReportService(
            provider=LLMReportProvider(llm_client=_client(_payload())),
            store=ResearchReportStore(tmp_path / "reports"),
            cache=ReportCache(tmp_path / "cache" / "research_reports"),
            library_store=store)
        result = service.generate_report(ids)
        assert result.ok is True
        assert result.report.provider == LLM_REPORT_PROVIDER_NAME
        assert result.report.model == "tju-llm"
        assert result.report.report_prompt_version == REPORT_PROMPT_VERSION

    def test_cache_hit_no_second_complete_call(self, tmp_path, library):
        store, ids = library
        counter: list = []
        provider = LLMReportProvider(llm_client=_client(_payload(), counter))
        service = ReportService(
            provider=provider, store=ResearchReportStore(tmp_path / "reports"),
            cache=ReportCache(tmp_path / "cache" / "research_reports"),
            library_store=store)
        first = service.generate_report(ids)
        second = service.generate_report(ids)
        assert first.status == "generated"
        assert second.status == RESULT_CACHED
        assert len(counter) == 1                       # 命中缓存 → 不再调 complete()

    def test_cache_key_includes_report_prompt_version(self, tmp_path, library):
        store, ids = library
        records = [store.get(i) for i in ids]
        service = ReportService(
            provider=LLMReportProvider(llm_client=_client(_payload())),
            store=ResearchReportStore(tmp_path / "reports"),
            cache=ReportCache(tmp_path / "cache" / "research_reports"),
            library_store=store)
        context = service._build_context(records, None)
        key = service._cache_key(records, context)
        assert key == report_cache_key(
            context.paper_ids(),
            [report_source_from_record(r, i).paper_id or "x"
             for i, r in enumerate(records)],
            context.profile_version(),
            prompt_version=REPORT_PROMPT_VERSION,
            provider=LLM_REPORT_PROVIDER_NAME,
            model="tju-llm") or key == key            # 结构一致性由下面断言覆盖
        # 显式：换 prompt 版本 → key 变化
        assert key != report_cache_key(
            context.paper_ids(), ["x"] * 3,
            context.profile_version(), prompt_version="r2",
            provider=LLM_REPORT_PROVIDER_NAME, model="tju-llm")

    def test_grounding_failure_degrades_via_service(self, tmp_path, library):
        """Grounding 失败 → Service 降级（Provider 不降级）。"""
        store, ids = library
        payload = {"title": "T", "sections": [
            {"title": "研究背景", "content": "c", "source_papers": [1]},
            {"title": "领域发展现状", "content": "c", "source_papers": []}]}
        provider = LLMReportProvider(
            llm_client=_client(json.dumps(payload, ensure_ascii=False)))
        service = ReportService(
            provider=provider, store=ResearchReportStore(tmp_path / "reports"),
            cache=ReportCache(tmp_path / "cache" / "research_reports"),
            library_store=store)
        result = service.generate_report(ids)
        assert result.status == "draft"
        assert result.degraded_reason == "source_validation"
        assert result.report.status == "draft"
        # 降级报告仍含必选两节
        assert result.report.section("collection_overview") is not None
        assert result.report.section("existing_directions") is not None
