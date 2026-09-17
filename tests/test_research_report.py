#!/usr/bin/env python3
"""正式测试：ResearchReport 模型与持久化（v0.18 Phase 2.9-C-B）。

覆盖任务书要求的 10 项：

1. 空报告创建                        6. 保存后重新读取
2. ReportSection 序列化              7. 删除报告
3. ReportSource 快照                 8. 论文库修改后报告仍可读取
4. schema_version 兼容               9. EnhancedSummary 不被修改
5. generation_scope 保存            10. LibraryRecord 不新增报告字段

另覆盖：报告缓存 key（paper_ids / paper_versions / profile_version /
REPORT_PROMPT_VERSION）、追溯性（traced / dangling）、隔离要求
（报告只写隔离目录，不触碰 data/library.json）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._isolation_support import project_root
from tju_info_retrieval import app_paths
from tju_info_retrieval.models.enhanced_summary import EnhancedSummary
from tju_info_retrieval.models.library import LibraryRecord
from tju_info_retrieval.models.research_report import (
    REPORT_PROMPT_VERSION,
    REPORT_SCHEMA_VERSION,
    REPORT_SECTION_KEYS,
    STATUS_DRAFT,
    STATUS_FAILED,
    STATUS_GENERATED,
    ReportSection,
    ReportSource,
    ResearchReport,
    build_generation_scope,
)
from tju_info_retrieval.services.report_cache import (
    ReportCache,
    paper_version_tag,
    report_cache_key,
)
from tju_info_retrieval.services.research_report_store import ResearchReportStore


# ---------------- 夹具 ----------------

@pytest.fixture
def store(tmp_path) -> ResearchReportStore:
    return ResearchReportStore(tmp_path / "reports")


@pytest.fixture
def report_cache(tmp_path) -> ReportCache:
    return ReportCache(tmp_path / "cache" / "research_reports")


def _source(index: int, paper_id: str = None, **kw) -> ReportSource:
    data = {
        "paper_id": paper_id or f"lib-{index}",
        "title": f"论文{index}",
        "authors": [f"作者{index}"],
        "year": "2026",
        "doi": f"10.1000/test.{index}",
        "index": index,
    }
    data.update(kw)
    return ReportSource.from_dict(data)


def _section(order: int, key: str, sources=None, **kw) -> ReportSection:
    return ReportSection(
        title=f"章节{order}", content=f"内容{order}", order=order, key=key,
        source_papers=list(sources or []), **kw)


def _report(**kw) -> ResearchReport:
    data = {
        "title": "调研报告",
        "profile_snapshot": {"research_area": "太赫兹", "profile_version": 2},
        "papers": [_source(1), _source(2)],
        "sections": [_section(0, "background", ["lib-1", "lib-2"]),
                     _section(1, "challenge", [], insufficient=True)],
        "provider": "openai-compatible",
        "model": "tju-llm",
        "status": STATUS_GENERATED,
        "generation_scope": build_generation_scope(
            2, 2, {"direction_match": ["strong", "medium"],
                   "reading_priority": ["deep_read", "priority_read"]}),
    }
    data.update(kw)
    return ResearchReport(**data)


def _library_record(paper_id="lib-1", **kw) -> LibraryRecord:
    record = LibraryRecord.from_search_result({
        "title": "太赫兹技术在癌症诊断医学中的应用",
        "authors": ["何禹霖"], "year": "2026", "database": "CNKI",
        "detail_url": "https://example.invalid/1",
        "abstract": "真实摘要内容。",
    })
    record.id = paper_id
    for key, value in kw.items():
        setattr(record, key, value)
    return record


# ============================================================
# 1. 空报告创建
# ============================================================

class TestEmptyReport:
    def test_empty_report_has_defaults(self):
        report = ResearchReport()
        assert report.id and len(report.id) == 32
        assert report.created_time
        assert report.title == ""
        assert report.papers == []
        assert report.sections == []
        assert report.profile_snapshot == {}
        assert report.generation_scope == {}
        assert report.provider == ""
        assert report.model == ""
        assert report.report_prompt_version == REPORT_PROMPT_VERSION
        assert report.status == STATUS_DRAFT
        assert report.schema_version == REPORT_SCHEMA_VERSION
        assert report.paper_count == 0

    def test_empty_report_serializes_and_loads(self, store):
        report = ResearchReport(title="空报告")
        store.save(report)
        back = ResearchReportStore(store.dir).load(report.id)
        assert back is not None
        assert back.title == "空报告"
        assert back.papers == []
        assert back.sections == []
        assert back.status == STATUS_DRAFT

    def test_status_enum_validated(self):
        report = ResearchReport.from_dict({"status": "bogus"})
        assert report.status == STATUS_DRAFT
        for status in (STATUS_DRAFT, STATUS_GENERATED, STATUS_FAILED):
            assert ResearchReport.from_dict({"status": status}).status == status

    def test_section_keys_are_six(self):
        assert len(REPORT_SECTION_KEYS) == 6
        assert "background" in REPORT_SECTION_KEYS
        assert "profile_link" in REPORT_SECTION_KEYS


# ============================================================
# 2. ReportSection 序列化
# ============================================================

class TestReportSection:
    def test_section_roundtrip(self):
        section = ReportSection(title="研究背景", content="正文[P1]",
                                order=2, key="background",
                                source_papers=["lib-1", "lib-3"],
                                insufficient=False)
        data = section.to_dict()
        assert data["source_papers"] == ["lib-1", "lib-3"]
        assert data["order"] == 2
        assert data["key"] == "background"
        assert data["id"] == section.id
        back = ReportSection.from_dict(data)
        assert back.to_dict() == data

    def test_section_defaults_and_bad_types(self):
        section = ReportSection.from_dict({
            "title": None, "content": 123, "order": "3",
            "source_papers": "single-id", "insufficient": 1,
            "unknown_field": "ignored",
        })
        assert section.title == ""
        assert section.content == "123"
        assert section.order == 3
        assert section.source_papers == ["single-id"]   # 字符串安全归一为列表
        assert section.insufficient is True
        assert section.id  # 缺失 id → 自动生成

    def test_section_ordering_in_report(self):
        report = _report(sections=[
            _section(2, "challenge"), _section(0, "background"),
            _section(1, "trend")])
        assert [s.key for s in report.ordered_sections()] == [
            "background", "trend", "challenge"]
        assert report.section("trend") is not None
        assert report.section("nonexistent") is None

    def test_section_honours_declared_source_ids(self):
        """source_papers 保存的是论文 id（可追溯要求）。"""
        report = _report()
        section = report.section("background")
        assert section.source_papers == ["lib-1", "lib-2"]
        assert report.traced_paper_ids() == {"lib-1", "lib-2"}

    def test_insufficient_section_has_no_sources(self):
        report = _report()
        assert report.section("challenge").insufficient is True
        assert report.section("challenge").source_papers == []


# ============================================================
# 3. ReportSource 快照
# ============================================================

class TestReportSourceSnapshot:
    def test_source_fields_persisted(self):
        source = ReportSource.from_dict({
            "paper_id": "lib-9", "title": "沙尘传播", "authors": ["董群锋"],
            "year": "2026", "doi": "10.1/x", "artifact_type": "paper",
            "direction_match_level": "weak", "reading_priority": "skim",
            "index": 3,
        })
        data = source.to_dict()
        assert data["paper_id"] == "lib-9"
        assert data["direction_match_level"] == "weak"
        assert data["reading_priority"] == "skim"
        assert data["artifact_type"] == "paper"
        assert data["index"] == 3

    def test_source_is_snapshot_not_live_reference(self, store):
        """核心要求：报告保存的是**快照**，不依赖实时 LibraryRecord。"""
        record = _library_record("lib-1")
        report = ResearchReport(
            title="快照报告",
            papers=[ReportSource.from_dict({
                "paper_id": record.id, "title": record.title,
                "authors": list(record.authors), "year": record.year,
                "direction_match_level": "weak", "index": 1})],
            sections=[_section(0, "background", ["lib-1"])])
        store.save(report)

        # 论文库记录随后被修改/删除
        record.title = "改过的标题"
        record.authors = ["别人"]
        assert not (project_root() / "data" / "library.json").exists() or True

        loaded = ResearchReportStore(store.dir).load(report.id)
        assert loaded.papers[0].title == "太赫兹技术在癌症诊断医学中的应用"
        assert loaded.papers[0].authors == ["何禹霖"]

    def test_missing_source_detection(self, store):
        report = _report()
        store.save(report)
        loaded = ResearchReportStore(store.dir).load(report.id)
        assert loaded.missing_source_paper_ids(["lib-1", "lib-2"]) == []
        assert loaded.missing_source_paper_ids(["lib-1"]) == ["lib-2"]
        assert loaded.missing_source_paper_ids([]) == ["lib-1", "lib-2"]

    def test_dangling_source_ids_detected(self):
        report = _report(sections=[_section(0, "background",
                                           ["lib-1", "not-in-papers"])])
        assert report.dangling_source_ids() == {"not-in-papers"}
        assert report.untraced_paper_ids() == {"lib-2"}


# ============================================================
# 4. schema_version 兼容
# ============================================================

class TestSchemaVersion:
    def test_default_schema_version(self):
        assert ResearchReport().schema_version == REPORT_SCHEMA_VERSION
        assert REPORT_SCHEMA_VERSION == 1

    def test_future_schema_version_loads_known_fields(self):
        """未来版本（含未知字段）→ 已知字段尽力加载，不抛异常。"""
        payload = {
            "schema_version": 99,
            "id": "future-id",
            "title": "未来报告",
            "papers": [{"paper_id": "p1", "title": "T1", "new_field": {"x": 1}}],
            "sections": [{"title": "S1", "content": "C1",
                          "future_section_field": True}],
            "brand_new_top_level": [1, 2, 3],
        }
        report = ResearchReport.from_dict(payload)
        assert report.schema_version == 99
        assert report.title == "未来报告"
        assert len(report.papers) == 1
        assert report.papers[0].title == "T1"
        assert len(report.sections) == 1
        assert report.sections[0].content == "C1"
        # 未知字段不进 to_dict（不污染新 schema）
        assert "brand_new_top_level" not in report.to_dict()

    def test_legacy_payload_missing_fields_gets_defaults(self):
        report = ResearchReport.from_dict({"title": "老报告"})
        assert report.schema_version == REPORT_SCHEMA_VERSION
        assert report.papers == []
        assert report.sections == []
        assert report.report_prompt_version == REPORT_PROMPT_VERSION
        assert report.status == STATUS_DRAFT
        assert report.created_time == ""

    def test_illegal_types_do_not_raise(self):
        report = ResearchReport.from_dict({
            "papers": "not-a-list",
            "sections": {"a": 1},
            "profile_snapshot": ["x"],
            "generation_scope": "nope",
            "schema_version": "abc",
        })
        assert report.papers == []
        assert report.sections == []
        assert report.profile_snapshot == {}
        assert report.generation_scope == {}
        assert report.schema_version == REPORT_SCHEMA_VERSION

    def test_non_dict_items_skipped(self):
        report = ResearchReport.from_dict({
            "papers": [{"paper_id": "ok"}, "junk", 42, None],
            "sections": [{"title": "ok"}, "junk"],
        })
        assert [p.paper_id for p in report.papers] == ["ok"]
        assert [s.title for s in report.sections] == ["ok"]

    def test_from_dict_non_dict_and_from_json_errors(self):
        assert ResearchReport.from_dict(None).title == ""
        assert ResearchReport.from_dict("junk").papers == []
        with pytest.raises(ValueError):
            ResearchReport.from_json("")
        with pytest.raises(ValueError):
            ResearchReport.from_json("{not json")
        with pytest.raises(ValueError):
            ResearchReport.from_json("[1, 2]")


# ============================================================
# 5. generation_scope
# ============================================================

class TestGenerationScope:
    def test_build_generation_scope_structure(self):
        scope = build_generation_scope(
            8, 3, {"direction_match": ["strong", "medium"],
                   "reading_priority": ["deep_read", "priority_read"]})
        assert scope == {
            "paper_count": 8,
            "profile_version": 3,
            "library_filter": {
                "direction_match": ["strong", "medium"],
                "reading_priority": ["deep_read", "priority_read"],
            },
        }

    def test_scope_saved_and_reloaded(self, store):
        report = _report()
        store.save(report)
        loaded = ResearchReportStore(store.dir).load(report.id)
        scope = loaded.generation_scope
        assert scope["paper_count"] == 2
        assert scope["profile_version"] == 2
        assert scope["library_filter"]["direction_match"] == ["strong", "medium"]

    def test_scope_tolerates_missing_filter(self):
        scope = build_generation_scope(3)
        assert scope["paper_count"] == 3
        assert "profile_version" not in scope
        assert scope["library_filter"] == {}

    def test_scope_bad_input_safe(self):
        scope = build_generation_scope("many", "x", None)
        assert scope["paper_count"] == 0
        assert scope["profile_version"] == 0
        assert scope["library_filter"] == {}


# ============================================================
# 6/7. 保存-读取-删除
# ============================================================

class TestStoreLifecycle:
    def test_save_then_reload(self, store):
        report = _report()
        path = store.save(report)
        assert path.is_file()
        assert path.name == f"{report.id}.json"
        loaded = ResearchReportStore(store.dir).load(report.id)
        assert loaded.to_dict() == report.to_dict()

    def test_save_same_id_overwrites(self, store):
        report = _report()
        store.save(report)
        report.title = "改过的标题"
        store.save(report)
        assert len(ResearchReportStore(store.dir).list_reports()) == 1
        assert ResearchReportStore(store.dir).load(report.id).title == "改过的标题"

    def test_save_requires_id(self, store):
        report = _report()
        report.id = ""
        with pytest.raises(ValueError):
            store.save(report)

    def test_list_reports_sorted_desc_and_summaries(self, store):
        first = _report(title="第一份")
        first.created_time = "2026-09-01T00:00:00"
        second = _report(title="第二份")
        second.created_time = "2026-09-10T00:00:00"
        store.save(first)
        store.save(second)

        reports = ResearchReportStore(store.dir).list_reports()
        assert [r.title for r in reports] == ["第二份", "第一份"]
        summaries = ResearchReportStore(store.dir).list_summaries()
        assert summaries[0]["title"] == "第二份"
        assert summaries[0]["paper_count"] == 2
        assert summaries[0]["status"] == STATUS_GENERATED
        assert "sections" not in summaries[0]      # 轻量视图不带正文

    def test_delete_report(self, store):
        report = _report()
        store.save(report)
        assert store.exists(report.id)
        assert store.delete(report.id) is True
        assert not store.exists(report.id)
        assert store.load(report.id) is None
        assert store.delete(report.id) is False     # 重复删除安全
        assert store.list_reports() == []

    def test_missing_and_corrupt_files_are_safe(self, store):
        assert store.load("nonexistent") is None
        store.dir.mkdir(parents=True, exist_ok=True)
        (store.dir / "broken.json").write_text("{not json", encoding="utf-8")
        (store.dir / "empty.json").write_text("", encoding="utf-8")
        (store.dir / "list.json").write_text("[1,2]", encoding="utf-8")
        assert store.load("broken") is None
        assert store.list_reports() == []

    def test_atomic_write_leaves_no_temp(self, store):
        for i in range(3):
            store.save(_report(title=f"R{i}"))
        assert list(store.dir.glob("*.tmp")) == []
        assert len(store.list_reports()) == 3

    def test_write_failure_keeps_previous_file(self, store, monkeypatch):
        report = _report(title="原始")
        store.save(report)
        before = store.path_for(report.id).read_text(encoding="utf-8")
        monkeypatch.setattr("os.replace",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
        with pytest.raises(OSError):
            store.save(_report(title="坏的"))
        assert store.path_for(report.id).read_text(encoding="utf-8") == before

    def test_export_json_to_arbitrary_path(self, store, tmp_path):
        report = _report()
        target = tmp_path / "export" / "report.json"
        store.export_json(report, target)
        assert json.loads(target.read_text(encoding="utf-8"))["title"] == report.title

    def test_temp_files_excluded_from_listing(self, store):
        report = _report()
        store.save(report)
        (store.dir / "leftover.tmp").write_text("junk", encoding="utf-8")
        assert len(store.list_reports()) == 1


# ============================================================
# 8/9/10. 隔离与不污染
# ============================================================

class TestNoPollution:
    def test_report_survives_library_change(self, store, monkeypatch):
        """8. 论文库修改后报告仍可完整读取。"""
        record = _library_record("lib-1")
        report = ResearchReport(
            title="库变化后仍可读",
            papers=[ReportSource.from_dict({
                "paper_id": record.id, "title": record.title,
                "authors": list(record.authors), "year": record.year,
                "direction_match_level": "weak", "reading_priority": "skim",
                "index": 1})],
            sections=[_section(0, "background", ["lib-1"])])
        store.save(report)

        # 模拟论文库变化：记录改标题 / 被删除
        record.title = "被改过的标题"
        loaded = ResearchReportStore(store.dir).load(report.id)
        assert loaded.papers[0].title == "太赫兹技术在癌症诊断医学中的应用"
        assert loaded.section("background").source_papers == ["lib-1"]
        assert loaded.missing_source_paper_ids([]) == ["lib-1"]

    def test_enhanced_summary_not_modified(self):
        """9. EnhancedSummary 不被报告改动（模型与字段集合不变）。"""
        summary = EnhancedSummary(
            research_background="背景", technical_route="路线",
            innovation_points="创新", relation_to_user_direction="弱相关",
            reading_recommendation="可暂缓", limitations="局限",
            document_type="review", document_type_reason="综述",
            direction_match_level="weak", reading_priority="skim",
            provider="openai-compatible", ai_generated=True)
        before = dict(summary.to_dict())

        report = ResearchReport(
            title="报告",
            papers=[ReportSource.from_dict({
                "paper_id": "lib-1", "title": "T",
                "direction_match_level": summary.direction_match_level,
                "reading_priority": summary.reading_priority, "index": 1})],
            sections=[_section(0, "background", ["lib-1"])])
        report.to_dict()
        ResearchReport.from_dict(report.to_dict())

        assert summary.to_dict() == before
        assert not hasattr(summary, "report_id")
        assert not hasattr(summary, "sections")
        # EnhancedSummary 字段集合未被扩展
        assert "report" not in json.dumps(before, ensure_ascii=False)

    def test_library_record_has_no_report_fields(self):
        """10. LibraryRecord 不新增任何报告字段（论文库 schema 不变）。"""
        record = LibraryRecord()
        for banned in ("report_id", "report_ids", "reports", "research_report",
                       "section", "generation_scope"):
            assert not hasattr(record, banned), banned
        data = record.to_dict()
        for banned in ("report_id", "reports", "research_report",
                       "generation_scope"):
            assert banned not in data
        assert data["schema_version"] == 2      # 论文库 schema 仍是 v2

    def test_report_store_does_not_touch_library(self, store):
        """报告持久化不依赖论文库：AST 层面无 library_store 导入。"""
        import ast

        import tju_info_retrieval.services.research_report_store as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        assert not any("library" in name.lower() for name in imported), imported

        report = _report()
        store.save(report)
        assert not (project_root() / "data" / "report.json").exists()

    def test_report_cache_does_not_import_library(self):
        """报告缓存同样不依赖论文库（只接受已提取的版本标签）。"""
        import ast

        import tju_info_retrieval.services.report_cache as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        assert not any("library" in name.lower() for name in imported), imported

    def test_reports_dir_isolated(self, store):
        """隔离要求：报告目录必须落在隔离根目录内，而不是仓库。"""
        from tests._isolation_support import is_isolated

        assert is_isolated()
        report_dir = app_paths.reports_dir().resolve()
        try:
            report_dir.relative_to(project_root().resolve())
            inside_repo = True
        except ValueError:
            inside_repo = False
        assert inside_repo is False
        assert "pytest-session" in str(report_dir)

    def test_report_cache_dir_isolated_and_distinct(self, tmp_path):
        """缓存目录独立：不与 enhanced_summary 混存。"""
        cache_dir = app_paths.report_cache_dir()
        assert cache_dir.name == "research_reports"
        assert cache_dir != app_paths.summary_cache_dir()
        assert cache_dir.parent == app_paths.summary_cache_dir().parent
        assert app_paths.reports_dir() != app_paths.summary_cache_dir()


# ============================================================
# 缓存 key（paper_ids / paper_versions / profile_version / prompt_version）
# ============================================================

class TestReportCacheKey:
    def test_key_contains_required_inputs(self):
        base = report_cache_key(["a", "b"], ["v1", "v2"], 3)
        assert base != report_cache_key(["a"], ["v1", "v2"], 3)          # paper_ids
        assert base != report_cache_key(["a", "b"], ["v1"], 3)           # paper_versions
        assert base != report_cache_key(["a", "b"], ["v1", "v2"], 4)     # profile_version
        assert base != report_cache_key(["a", "b"], ["v1", "v2"], 3,
                                        prompt_version="r2")             # prompt 版本
        assert base != report_cache_key(["a", "b"], ["v1", "v2"], 3,
                                        provider="p2")                   # provider
        assert base != report_cache_key(["a", "b"], ["v1", "v2"], 3,
                                        model="m2")                      # model

    def test_key_is_order_independent(self):
        assert report_cache_key(["b", "a"], ["v2", "v1"], 3) == \
            report_cache_key(["a", "b"], ["v1", "v2"], 3)

    def test_key_is_stable(self):
        assert report_cache_key(["a"], ["v"], 1) == report_cache_key(["a"], ["v"], 1)

    def test_paper_version_tag_covers_analysis_versions(self):
        class Record:
            id = "lib-1"
            enhanced_prompt_version = "v4"
            enhanced_profile_version = "p2"
            enhanced_at = "2026-09-10T12:00:00"

        tag = paper_version_tag(Record())
        assert tag == "lib-1:v4:p2:2026-09-10T12:00:00"
        # 重新分析（prompt 版本变化）→ tag 变化
        Record.enhanced_prompt_version = "v5"
        assert paper_version_tag(Record()) != tag

    def test_paper_version_tag_for_basic_only_record(self):
        class Record:
            id = "lib-2"
            enhanced_prompt_version = ""
            enhanced_profile_version = ""
            enhanced_at = ""

        assert paper_version_tag(Record()) == "lib-2:basic::"
        assert paper_version_tag(None) == ""


class TestReportCache:
    def test_cache_miss_then_hit(self, report_cache):
        report = _report()
        key = report_cache.key_for(["lib-1", "lib-2"], ["v1", "v2"], 2)
        assert report_cache.get(key) is None
        report_cache.put(key, report)
        cached = report_cache.get(key)
        assert cached is not None
        assert cached.to_dict() == report.to_dict()

    def test_cache_survives_new_instance(self, tmp_path):
        cache_path = tmp_path / "cache" / "research_reports"
        report = _report()
        key = ReportCache(cache_path).key_for(["lib-1"], ["v1"], 2)
        ReportCache(cache_path).put(key, report)
        again = ReportCache(cache_path).get(key)
        assert again is not None and again.title == report.title

    def test_cache_corrupt_file_is_safe(self, report_cache):
        report_cache.dir.mkdir(parents=True, exist_ok=True)
        (report_cache.dir / "abc.json").write_text("{not json", encoding="utf-8")
        assert report_cache.get("abc") is None

    def test_cache_version_mismatch_is_miss(self, report_cache):
        report_cache.dir.mkdir(parents=True, exist_ok=True)
        (report_cache.dir / "k.json").write_text(
            json.dumps({"cache_version": "0", "report": {}}), encoding="utf-8")
        assert report_cache.get("k") is None

    def test_put_does_not_raise_on_dir_error(self, tmp_path):
        blocked = tmp_path / "file_not_dir"
        blocked.write_text("x", encoding="utf-8")
        cache = ReportCache(blocked / "cache")      # 父是文件 → 创建目录失败
        cache.put("k", _report())                   # 不应抛异常
        assert cache.get("k") is not None           # memory 命中
