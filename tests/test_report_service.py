#!/usr/bin/env python3
"""正式测试：ReportService（v0.18 Phase 2.9-C-C）。

覆盖任务书要求的 16 项：

基础：1 空论文列表 / 2 单论文 / 3 5论文生成 / 4 超过20拒绝 /
      5 保存成功 / 6 重新加载成功
缓存：7 第一次调用 Provider / 8 第二次命中缓存 / 9 论文版本变化缓存失效
失败：10 Provider异常 / 11 API失败降级 / 12 生成draft报告
隔离：13 不修改LibraryRecord / 14 不写入library.json / 15 测试目录隔离
依赖：16 AST检查（不导入 SearchService / BrowserSession / Evidence）

另覆盖：来源可追溯校验、ReportContext 快照约束、Provider 协议、
基础报告章节要求（文献集合概览 + 已有研究方向）。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests._isolation_support import project_root
from tju_info_retrieval import app_paths
from tju_info_retrieval.models.library import LibraryRecord
from tju_info_retrieval.models.report_context import ReportContext
from tju_info_retrieval.models.research_report import (
    REPORT_PROMPT_VERSION,
    SECTION_EXISTING_DIRECTIONS,
    SECTION_OVERVIEW,
    STATUS_DRAFT,
    STATUS_GENERATED,
    ReportSection,
    ReportSource,
    ResearchReport,
)
from tju_info_retrieval.services.library_store import LibraryStore
from tju_info_retrieval.services.report_cache import ReportCache
from tju_info_retrieval.services.report_provider import (
    MOCK_REPORT_PROVIDER_NAME,
    MockReportProvider,
    ReportProvider,
)
from tju_info_retrieval.services.report_service import (
    DEGRADED_NOT_CONFIGURED,
    DEGRADED_PROVIDER_ERROR,
    DEGRADED_SOURCE_VALIDATION,
    REPORT_MAX_PAPERS,
    RESULT_CACHED,
    RESULT_DRAFT,
    RESULT_GENERATED,
    RESULT_REJECTED,
    ReportService,
    report_source_from_record,
    source_materials_from_record,
)
from tju_info_retrieval.services.research_report_store import ResearchReportStore


# ---------------- 夹具 ----------------

@pytest.fixture
def library(tmp_path) -> LibraryStore:
    return LibraryStore(tmp_path / "cache" / "library.json")


def _seed(library: LibraryStore, count: int, analyzed: int = 0) -> list[str]:
    """写入 count 条库记录（前 analyzed 条带既有 AI 分析），返回 id 列表。"""
    ids = []
    for i in range(count):
        record = LibraryRecord.from_search_result({
            "title": f"测试论文{i}",
            "authors": [f"作者{i}"],
            "year": "2026" if i % 2 == 0 else "2025",
            "source": "中国知网" if i < 3 else "IEEE Xplore",
            "database": "CNKI",
            "detail_url": f"https://example.invalid/{i}",
            "abstract": f"第{i}篇摘要。",
        })
        record.id = f"lib-{i}"
        if i < analyzed:
            record.set_enhanced_summary(
                {"document_type": "review",
                 "direction_match_level": "medium" if i % 2 else "strong",
                 "reading_priority": "priority_read",
                 "research_background": f"背景{i}",
                 "limitations": f"局限{i}"},
                provider="openai-compatible", model="tju-llm",
                prompt_version="v4", profile_version="p2")
        library.save(record)
        ids.append(record.id)
    return ids


def _service(tmp_path, library, provider=None, **kw) -> ReportService:
    return ReportService(
        provider=provider,
        store=kw.pop("store", ResearchReportStore(tmp_path / "reports")),
        cache=kw.pop("cache",
                     ReportCache(tmp_path / "cache" / "research_reports")),
        library_store=library,
        **kw)


# ============================================================
# 1-6 基础
# ============================================================

class TestBasicGeneration:
    def test_1_empty_paper_list_rejected(self, tmp_path, library):
        service = _service(tmp_path, library, MockReportProvider())
        result = service.generate_report([])
        assert result.ok is False
        assert result.status == RESULT_REJECTED
        assert result.report is None
        assert "请先选择" in result.message
        assert result.provider_calls == 0          # 未触发任何 Provider 调用
        assert service._store.list_reports() == []  # 未产生报告文件

    def test_1b_none_and_blank_ids_rejected(self, tmp_path, library):
        service = _service(tmp_path, library, MockReportProvider())
        assert service.generate_report(None).status == RESULT_REJECTED
        assert service.generate_report(["", "  "]).status == RESULT_REJECTED

    def test_2_single_paper_allowed_with_warning(self, tmp_path, library):
        ids = _seed(library, 1)
        provider = MockReportProvider()
        service = _service(tmp_path, library, provider)
        result = service.generate_report(ids)
        assert result.ok is True
        assert result.status == RESULT_GENERATED
        assert result.report.paper_count == 1
        assert "覆盖度有限" in result.warning
        assert provider.calls == 1

    def test_3_five_papers_generated(self, tmp_path, library):
        ids = _seed(library, 5, analyzed=3)
        service = _service(tmp_path, library, MockReportProvider())
        result = service.generate_report(ids, title="五篇调研")
        assert result.ok is True
        assert result.report.title == "五篇调研"
        assert result.report.paper_count == 5
        assert result.warning == ""                # 5 篇不提示
        assert result.report.status == STATUS_GENERATED
        assert [p.paper_id for p in result.report.papers] == ids  # 顺序保持

    def test_3b_twenty_papers_is_upper_bound(self, tmp_path, library):
        ids = _seed(library, REPORT_MAX_PAPERS)
        service = _service(tmp_path, library, MockReportProvider())
        result = service.generate_report(ids)
        assert result.ok is True
        assert result.report.paper_count == REPORT_MAX_PAPERS

    def test_4_over_twenty_rejected(self, tmp_path, library):
        ids = _seed(library, REPORT_MAX_PAPERS + 1)
        provider = MockReportProvider()
        service = _service(tmp_path, library, provider)
        result = service.generate_report(ids)
        assert result.ok is False
        assert result.status == RESULT_REJECTED
        assert str(REPORT_MAX_PAPERS) in result.message
        assert provider.calls == 0                 # 拒绝时不调用 Provider
        assert service._store.list_reports() == []

    def test_4b_unknown_ids_rejected(self, tmp_path, library):
        _seed(library, 3)
        service = _service(tmp_path, library, MockReportProvider())
        result = service.generate_report(["not-exist", "also-not"])
        assert result.ok is False
        assert "未找到" in result.message

    def test_5_saved_successfully(self, tmp_path, library):
        ids = _seed(library, 5)
        store = ResearchReportStore(tmp_path / "reports")
        service = _service(tmp_path, library, MockReportProvider(), store=store)
        result = service.generate_report(ids)
        assert result.saved_path
        assert Path(result.saved_path).is_file()
        assert Path(result.saved_path).parent == store.dir
        assert store.exists(result.report.id)

    def test_6_reload_after_save(self, tmp_path, library):
        ids = _seed(library, 6, analyzed=2)
        store = ResearchReportStore(tmp_path / "reports")
        service = _service(tmp_path, library, MockReportProvider(), store=store)
        result = service.generate_report(ids)

        # 用**新的 store 实例**（模拟重启）重新加载
        reloaded = ResearchReportStore(store.dir).load(result.report.id)
        assert reloaded is not None
        assert reloaded.title == result.report.title
        assert reloaded.paper_count == 6
        assert [s.key for s in reloaded.ordered_sections()] == [
            s.key for s in result.report.sections]
        assert reloaded.report_prompt_version == REPORT_PROMPT_VERSION
        assert reloaded.generation_scope["paper_count"] == 6

    def test_6b_list_summaries_after_generation(self, tmp_path, library):
        ids = _seed(library, 5)
        store = ResearchReportStore(tmp_path / "reports")
        service = _service(tmp_path, library, MockReportProvider(), store=store)
        service.generate_report(ids, title="列表视图")
        summaries = ResearchReportStore(store.dir).list_summaries()
        assert len(summaries) == 1
        assert summaries[0]["title"] == "列表视图"
        assert summaries[0]["paper_count"] == 5
        assert summaries[0]["status"] == STATUS_GENERATED

    def test_title_precedence(self, tmp_path, library):
        """标题优先级：用户显式 > Provider 生成 > 服务默认。"""
        ids = _seed(library, 5)
        service = _service(tmp_path, library, MockReportProvider())

        # Provider 有标题 → 用 Provider 的
        assert service.generate_report([ids[0]]).report.title == "Mock 调研报告"

        # 用户显式指定 → 覆盖一切
        explicit = service.generate_report(
            [ids[0]], title="用户指定标题").report
        assert explicit.title == "用户指定标题"

        # Provider 无标题 → 服务默认（画像领域 + 篇数）
        class NoTitleProvider:
            name = "notitle"

            def generate(self, context):
                return ResearchReport(sections=[ReportSection(
                    title="S", content="c[P1]",
                    source_papers=[context.papers[0].paper_id])])

        fallback = _service(tmp_path, library, NoTitleProvider())             .generate_report([ids[0]]).report
        assert "1 篇" in fallback.title


# ============================================================
# 7-9 缓存
# ============================================================

class TestCacheBehaviour:
    def test_7_first_call_uses_provider(self, tmp_path, library):
        ids = _seed(library, 5, analyzed=2)
        provider = MockReportProvider()
        service = _service(tmp_path, library, provider)
        result = service.generate_report(ids)
        assert provider.calls == 1
        assert result.from_cache is False
        assert result.provider_calls == 1

    def test_8_second_call_hits_cache_without_provider(self, tmp_path, library):
        ids = _seed(library, 5, analyzed=2)
        provider = MockReportProvider()
        service = _service(tmp_path, library, provider)
        first = service.generate_report(ids)
        second = service.generate_report(ids)

        assert provider.calls == 1                 # 命中缓存 → 禁止再调用
        assert second.from_cache is True
        assert second.status == RESULT_CACHED
        assert second.provider_calls == 0
        assert second.report.to_dict() == first.report.to_dict()

    def test_8b_cache_order_independent(self, tmp_path, library):
        ids = _seed(library, 5)
        provider = MockReportProvider()
        service = _service(tmp_path, library, provider)
        service.generate_report(ids)
        service.generate_report(list(reversed(ids)))   # 换选顺序
        assert provider.calls == 1

    def test_9_paper_version_change_invalidates_cache(self, tmp_path, library):
        ids = _seed(library, 5, analyzed=2)
        provider = MockReportProvider()
        service = _service(tmp_path, library, provider)
        service.generate_report(ids)

        # 其中一篇被重新分析（分析版本变化）→ 集合指纹变化 → 缓存失效
        record = library.get(ids[0])
        record.set_enhanced_summary(
            record.enhanced_summary, provider="openai-compatible",
            model="tju-llm", prompt_version="v5", profile_version="p2")
        library.save(record)

        service.generate_report(ids)
        assert provider.calls == 2

    def test_9b_profile_version_change_invalidates_cache(self, tmp_path, library):
        ids = _seed(library, 5)
        provider = MockReportProvider()
        profile_path = tmp_path / "profile" / "research_profile.json"
        from tju_info_retrieval.services.research_profile_store import (
            ResearchProfileStore,
        )

        profile_store = ResearchProfileStore(profile_path)
        profile = profile_store.load()
        profile.research_area = "太赫兹"
        profile_store.save(profile)

        service = _service(tmp_path, library, provider, profile_store=profile_store)
        service.generate_report(ids)

        profile = profile_store.load()
        profile.sub_direction = ["THz-ISAC"]
        profile_store.save(profile)                 # profile_version +1

        service.generate_report(ids)
        assert provider.calls == 2

    def test_9c_different_provider_model_not_reused(self, tmp_path, library):
        ids = _seed(library, 5)

        class NamedProvider(MockReportProvider):
            name = "other"

            def __init__(self):
                super().__init__()
                self.model = "model-x"

        base = _service(tmp_path, library, MockReportProvider())
        base.generate_report(ids)
        other = _service(tmp_path, library, NamedProvider())
        result = other.generate_report(ids)
        assert result.from_cache is False           # provider/model 不同 → 不命中

    def test_cache_key_contains_required_fields(self, tmp_path, library):
        ids = _seed(library, 5, analyzed=2)
        service = _service(tmp_path, library, MockReportProvider())
        records = [library.get(i) for i in ids]
        context = service._build_context(records, None)
        key = service._cache_key(records, context)
        keys = {
            service._cache_key(records, context),
            service._cache_key(records[:2], service._build_context(records[:2], None)),
            service._cache_key(records, context, ) if False else key,
        }
        assert len(keys) == 2                       # paper_ids 参与 key
        assert key == service._cache_key(records, context)   # 稳定

    def test_cache_miss_after_prompt_version_change(self, tmp_path, library,
                                                    monkeypatch):
        ids = _seed(library, 5)
        provider = MockReportProvider()
        service = _service(tmp_path, library, provider)
        service.generate_report(ids)
        monkeypatch.setattr(
            "tju_info_retrieval.services.report_service.REPORT_PROMPT_VERSION",
            "r2")
        service.generate_report(ids)
        assert provider.calls == 2


# ============================================================
# 10-12 失败降级
# ============================================================

class TestDegradation:
    def test_10_provider_exception_degrades(self, tmp_path, library):
        ids = _seed(library, 5, analyzed=2)
        provider = MockReportProvider(raises=RuntimeError("provider boom"))
        service = _service(tmp_path, library, provider)
        result = service.generate_report(ids)

        assert result.ok is True                    # 不抛异常，仍产出报告
        assert result.status == RESULT_DRAFT
        assert result.degraded_reason == DEGRADED_PROVIDER_ERROR
        assert "基础报告" in result.message
        assert result.report.status == STATUS_DRAFT

    def test_11_api_failure_degrades_not_raises(self, tmp_path, library):
        """API/网络类异常（模拟 requests 失败）→ 降级，不抛到调用方。"""
        import requests

        ids = _seed(library, 5)
        provider = MockReportProvider(
            raises=requests.ConnectionError("network down"))
        service = _service(tmp_path, library, provider)
        result = service.generate_report(ids)

        assert result.ok is True
        assert result.status == RESULT_DRAFT
        assert result.degraded_reason == DEGRADED_PROVIDER_ERROR
        assert "ConnectionError" in result.message

    def test_11b_not_configured_degrades_without_calls(self, tmp_path, library):
        ids = _seed(library, 5)
        service = _service(tmp_path, library, provider=None)
        result = service.generate_report(ids)
        assert result.status == RESULT_DRAFT
        assert result.degraded_reason == DEGRADED_NOT_CONFIGURED
        assert result.provider_calls == 0
        assert service.configured is False

    def test_11c_empty_sections_from_provider_degrades(self, tmp_path, library):
        ids = _seed(library, 5)
        service = _service(tmp_path, library, MockReportProvider(empty_sections=True))
        result = service.generate_report(ids)
        assert result.status == RESULT_DRAFT
        assert result.degraded_reason == DEGRADED_SOURCE_VALIDATION
        assert result.validation_problems

    def test_11d_dangling_sources_degrade(self, tmp_path, library):
        ids = _seed(library, 5)
        service = _service(tmp_path, library,
                           MockReportProvider(dangling_sources=True))
        result = service.generate_report(ids)
        assert result.status == RESULT_DRAFT
        assert result.degraded_reason == DEGRADED_SOURCE_VALIDATION
        assert any("不存在的来源论文" in p for p in result.validation_problems)

    def test_12_draft_report_has_required_sections(self, tmp_path, library):
        ids = _seed(library, 6, analyzed=3)
        service = _service(tmp_path, library,
                           MockReportProvider(raises=RuntimeError("x")))
        result = service.generate_report(ids)
        report = result.report

        assert report.status == STATUS_DRAFT
        keys = [s.key for s in report.ordered_sections()]
        # 必含两节
        assert SECTION_OVERVIEW in keys
        assert SECTION_EXISTING_DIRECTIONS in keys
        assert keys[0] == SECTION_OVERVIEW
        assert keys[1] == SECTION_EXISTING_DIRECTIONS
        # 其余章节标注"未调用AI"
        for section in report.sections:
            if section.key not in (SECTION_OVERVIEW, SECTION_EXISTING_DIRECTIONS):
                assert section.insufficient is True
                assert "未调用 AI" in section.content

    def test_12b_draft_overview_content_is_data_only(self, tmp_path, library):
        ids = _seed(library, 6, analyzed=3)
        service = _service(tmp_path, library, provider=None)
        report = service.generate_report(ids).report
        overview = report.section(SECTION_OVERVIEW)

        assert "共纳入 6 篇" in overview.content
        assert "年份分布" in overview.content
        assert "文献类型" in overview.content
        assert "[P1]" in overview.content
        assert overview.source_papers == ids          # 来源可追溯

    def test_12c_draft_directions_uses_existing_analysis(self, tmp_path, library):
        ids = _seed(library, 6, analyzed=3)
        service = _service(tmp_path, library, provider=None)
        report = service.generate_report(ids).report
        directions = report.section(SECTION_EXISTING_DIRECTIONS)

        assert "方向匹配等级" in directions.content
        assert "强相关" in directions.content or "中等相关" in directions.content
        assert "阅读优先级" in directions.content
        assert "未调用 AI" in directions.content
        assert directions.source_papers == ids

    def test_12d_draft_directions_without_analysis(self, tmp_path, library):
        ids = _seed(library, 5)                       # 无任何 AI 分析
        service = _service(tmp_path, library, provider=None)
        report = service.generate_report(ids).report
        directions = report.section(SECTION_EXISTING_DIRECTIONS)
        assert "尚未进行 AI 增强分析" in directions.content

    def test_12e_draft_report_is_saved(self, tmp_path, library):
        ids = _seed(library, 5)
        store = ResearchReportStore(tmp_path / "reports")
        service = _service(tmp_path, library, provider=None, store=store)
        result = service.generate_report(ids)
        assert result.saved_path
        assert ResearchReportStore(store.dir).load(result.report.id) is not None

    def test_degraded_report_not_cached(self, tmp_path, library):
        """降级结果不写缓存：下次仍会尝试 Provider。"""
        ids = _seed(library, 5)
        provider = MockReportProvider(raises=RuntimeError("x"))
        service = _service(tmp_path, library, provider)
        service.generate_report(ids)
        service.generate_report(ids)
        assert provider.calls == 2

    def test_no_fabrication_marker_in_draft(self, tmp_path, library):
        """降级报告不得出现任何 AI 结论性内容占位。"""
        ids = _seed(library, 5)
        service = _service(tmp_path, library, provider=None)
        report = service.generate_report(ids).report
        for section in report.sections:
            if section.key in (SECTION_OVERVIEW, SECTION_EXISTING_DIRECTIONS):
                continue
            assert section.content.strip() == \
                "未调用 AI：本机未配置或调用失败，无法从所选论文归纳该章节。"


# ============================================================
# 来源可追溯
# ============================================================

class TestSourceTracing:
    def test_generated_sections_have_sources(self, tmp_path, library):
        ids = _seed(library, 5)
        service = _service(tmp_path, library, MockReportProvider())
        report = service.generate_report(ids).report
        for section in report.sections:
            assert section.source_papers
            assert set(section.source_papers) <= set(ids)

    def test_validate_sources_passes_for_valid_report(self, tmp_path, library):
        ids = _seed(library, 5)
        service = _service(tmp_path, library, MockReportProvider())
        report = service.generate_report(ids).report
        assert service.validate_sources(report) == []

    def test_validate_sources_detects_dangling(self, tmp_path, library):
        service = _service(tmp_path, library, MockReportProvider())
        report = ResearchReport(
            papers=[ReportSource.from_dict({"paper_id": "a", "title": "A"})],
            sections=[ReportSection(title="S", content="c",
                                    source_papers=["b"])])
        problems = service.validate_sources(report)
        assert any("不存在的来源论文" in p for p in problems)

    def test_validate_sources_detects_missing_sections(self, tmp_path, library):
        service = _service(tmp_path, library, MockReportProvider())
        problems = service.validate_sources(ResearchReport())
        assert any("没有任何章节" in p for p in problems)

    def test_validate_sources_detects_untraced(self, tmp_path, library):
        service = _service(tmp_path, library, MockReportProvider())
        report = ResearchReport(
            papers=[ReportSource.from_dict({"paper_id": "a", "title": "A"})],
            sections=[ReportSection(title="S", content="c", source_papers=[])])
        problems = service.validate_sources(report)
        assert any("未标注任何来源" in p for p in problems)

    def test_all_papers_traceable_in_snapshot(self, tmp_path, library):
        ids = _seed(library, 5, analyzed=3)
        service = _service(tmp_path, library, MockReportProvider())
        report = service.generate_report(ids).report
        assert {p.paper_id for p in report.papers} == set(ids)
        assert report.dangling_source_ids() == set()


# ============================================================
# ReportContext / Provider 协议
# ============================================================

class TestContextAndProvider:
    def test_context_rejects_library_record_instances(self):
        record = LibraryRecord()
        with pytest.raises(ValueError, match="不得传入 LibraryRecord"):
            ReportContext(papers=[record])

    def test_context_rejects_non_snapshot_objects(self):
        with pytest.raises(ValueError, match="必须是 ReportSource"):
            ReportContext(papers=["not-a-source"])

    def test_context_accepts_snapshots(self):
        context = ReportContext(
            papers=[ReportSource.from_dict({"paper_id": "a", "title": "A"})],
            profile_snapshot={"research_area": "太赫兹", "profile_version": 2},
            generation_scope={"paper_count": 1})
        assert context.paper_count == 1
        assert context.known_paper_ids() == {"a"}
        assert context.profile_version() == "2"
        assert context.profile_is_configured() is True
        assert "太赫兹" in context.profile_summary_text()

    def test_context_empty_profile_text(self):
        context = ReportContext(profile_snapshot={})
        assert context.profile_is_configured() is False
        assert "尚未设置科研画像" in context.profile_summary_text()

    def test_context_roundtrip(self):
        context = ReportContext(
            papers=[ReportSource.from_dict({"paper_id": "a", "title": "A"})],
            profile_snapshot={"profile_version": 3},
            generation_scope={"paper_count": 1},
            source_materials={"a": {"limitations": "x"}})
        assert ReportContext.from_dict(context.to_dict()).to_dict() == \
            context.to_dict()

    def test_context_from_dict_tolerates_junk(self):
        context = ReportContext.from_dict(
            {"papers": "junk", "profile_snapshot": [1], "source_materials": 5})
        assert context.papers == []
        assert context.profile_snapshot == {}
        assert context.source_materials == {}

    def test_source_materials_from_record_copies_existing_only(self, tmp_path,
                                                              library):
        _seed(library, 1, analyzed=1)
        record = library.get("lib-0")
        materials = source_materials_from_record(record)
        assert materials["research_background"] == "背景0"
        assert materials["limitations"] == "局限0"
        # 不包含任何新生成的字段
        assert "summary" not in materials

    def test_mock_provider_satisfies_protocol(self):
        provider = MockReportProvider()
        assert isinstance(provider, ReportProvider)
        assert provider.name == MOCK_REPORT_PROVIDER_NAME

    def test_mock_provider_returns_report_with_sources(self):
        context = ReportContext(papers=[
            ReportSource.from_dict({"paper_id": "a", "title": "A"}),
            ReportSource.from_dict({"paper_id": "b", "title": "B"})])
        report = MockReportProvider().generate(context)
        assert isinstance(report, ResearchReport)
        assert report.sections
        for section in report.sections:
            assert section.source_papers == ["a"]

    def test_provider_can_return_partial_report(self, tmp_path, library):
        """Provider 只填 sections 也可以：Service 补齐身份与元信息。"""
        ids = _seed(library, 5)

        class MinimalProvider:
            name = "minimal"

            def generate(self, context):
                return ResearchReport(sections=[ReportSection(
                    title="自定义章节", content="内容[P1]",
                    source_papers=[context.papers[0].paper_id])])

        service = _service(tmp_path, library, MinimalProvider())
        result = service.generate_report(ids)
        assert result.status == RESULT_GENERATED
        assert result.report.id
        assert result.report.paper_count == 5
        assert result.report.provider == "minimal"
        assert result.report.report_prompt_version == REPORT_PROMPT_VERSION
        assert result.report.generation_scope["paper_count"] == 5

    def test_service_rejects_non_report_return(self, tmp_path, library):
        ids = _seed(library, 5)

        class BadProvider:
            name = "bad"

            def generate(self, context):
                return "not a report"

        service = _service(tmp_path, library, BadProvider())
        result = service.generate_report(ids)
        assert result.status == RESULT_DRAFT      # 归一失败 → 降级
        assert result.degraded_reason == DEGRADED_PROVIDER_ERROR


# ============================================================
# 13-16 隔离与依赖
# ============================================================

class TestIsolationAndDependencies:
    def test_13_library_record_not_modified(self, tmp_path, library):
        """13. 生成报告不修改 LibraryRecord（含分析字段与用户字段）。"""
        ids = _seed(library, 6, analyzed=3)
        before = {r.id: r.to_dict() for r in library.load_all()}

        service = _service(tmp_path, library, MockReportProvider())
        service.generate_report(ids, title="不改库")

        after = {r.id: r.to_dict() for r in library.load_all()}
        assert after == before

    def test_13b_library_record_has_no_report_fields(self, tmp_path, library):
        ids = _seed(library, 5)
        service = _service(tmp_path, library, MockReportProvider())
        service.generate_report(ids)
        for record in library.load_all():
            for banned in ("report_id", "reports", "research_report"):
                assert banned not in record.to_dict()

    def test_14_library_json_not_written(self, tmp_path, library):
        """14. 生成报告不写入 library.json（字节不变）。"""
        ids = _seed(library, 5)
        path = library.path
        before = path.read_bytes()
        service = _service(tmp_path, library, MockReportProvider())
        service.generate_report(ids)
        assert path.read_bytes() == before

    def test_14b_real_library_untouched(self, tmp_path):
        """真实路径下的 library.json 不被本测试触碰（护栏之外的显式断言）。"""
        real = project_root() / "data" / "library.json"
        if not real.is_file():
            pytest.skip("真实论文库不存在")
        before = real.read_bytes()
        service = _service(tmp_path, LibraryStore(tmp_path / "lib.json"),
                           MockReportProvider())
        service.generate_report([])
        assert real.read_bytes() == before

    def test_15_reports_and_cache_isolated(self, tmp_path, library):
        """15. 报告与缓存目录都落在隔离根目录内，而非仓库。"""
        ids = _seed(library, 5)
        store = ResearchReportStore()          # 默认路径（app_paths）
        cache = ReportCache()
        service = ReportService(provider=MockReportProvider(), store=store,
                                cache=cache, library_store=library)
        result = service.generate_report(ids)

        repo = project_root().resolve()
        for target in (Path(result.saved_path), cache.dir):
            resolved = target.resolve()
            with pytest.raises(ValueError):
                resolved.relative_to(repo)     # 不在仓库内
        assert "pytest-session" in str(store.dir)
        assert not (repo / "runtime" / "reports").exists()

    def test_16_ast_no_forbidden_dependencies(self):
        """16. ReportService / ReportProvider / ReportContext 不依赖被禁模块。"""
        import tju_info_retrieval.models.report_context as context_module
        import tju_info_retrieval.services.report_provider as provider_module
        import tju_info_retrieval.services.report_service as service_module

        forbidden = ("search_service", "browser", "session", "evidence",
                     "offline_summary", "sources", "summary_service",
                     "enhanced_provider")
        for module in (service_module, provider_module, context_module):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported.append(node.module or "")
            for name in imported:
                lowered = name.lower()
                assert not any(word in lowered for word in forbidden), \
                    f"{module.__name__} 导入了被禁模块 {name}"

    def test_16b_service_does_not_import_search_chain(self):
        import tju_info_retrieval.services.report_service as service_module

        tree = ast.parse(Path(service_module.__file__).read_text(encoding="utf-8"))
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
        assert "tju_info_retrieval.services.search_service" not in names
        assert not any("browser" in n for n in names)

    def test_search_chain_still_zero_api(self):
        """搜索链路对报告模块零依赖（AST 静态核验）。"""
        targets = [project_root() / "src/tju_info_retrieval/services/search_service.py",
                   project_root() / "src/tju_info_retrieval/services/ranking.py"]
        for path in targets:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names.append(node.module or "")
            assert not any("report" in n.lower() for n in names), path.name

    def test_report_source_from_record_is_snapshot(self, tmp_path, library):
        """快照不持有记录实例：记录改名后快照不变。"""
        _seed(library, 1, analyzed=1)
        record = library.get("lib-0")
        snapshot = report_source_from_record(record, 1)
        record.title = "改过的标题"
        assert snapshot.title == "测试论文0"
        assert snapshot.index == 1
        assert snapshot.direction_match_level == "strong"
        assert snapshot.reading_priority == "priority_read"

    def test_no_network_calls_during_generation(self, tmp_path, library,
                                                monkeypatch):
        """报告生成本阶段绝不联网（Mock Provider + 未配置 Provider 两条路径）。"""
        import requests

        def _boom(*args, **kwargs):
            raise AssertionError("报告生成期间不应发起任何网络请求")

        monkeypatch.setattr(requests, "post", _boom)
        monkeypatch.setattr(requests, "get", _boom)

        ids = _seed(library, 5)
        assert _service(tmp_path, library, MockReportProvider()) \
            .generate_report(ids).ok is True
        assert _service(tmp_path, library, provider=None) \
            .generate_report(ids).status == RESULT_DRAFT
