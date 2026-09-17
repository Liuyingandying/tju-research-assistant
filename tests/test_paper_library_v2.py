#!/usr/bin/env python3
"""正式测试：v0.18 Phase 2.9-B 我的论文库 v2（科研阅读管理库）。

覆盖 §五十三 的 35 项要求（1-3 在 tests/test_research_profile.py）：

4.  library legacy migration          20. restart persistence
5.  new library schema                21. profile staleness
6.  favorite new item                 22. prompt staleness
7.  favorite duplicate updates        23. reanalyze keeps old result on failure
8.  basic summary persistence         24. reanalyze success updates record
9.  enhanced summary persistence      25. cache reused
10. document_type persistence         26. favorite triggers zero API
11. direction_match persistence       27. search triggers zero API
12. reading_priority persistence      28. basic triggers zero API
13. provider/model persistence        29. enhanced only on user action
14. prompt_version persistence        30. legacy record does not crash
15. profile_version persistence       31. atomic write
16. tags add/remove                   32. no secret in library
17. tags dedupe                       33. 920x560 UI
18. reading status                    34. 1366x768 UI
19. user note                         35. 1920x1080 UI
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import requests
from PySide6.QtWidgets import QApplication, QMessageBox

from tju_info_retrieval.models.enhanced_summary import EnhancedSummary
from tju_info_retrieval.models.library import (
    LIBRARY_SCHEMA_VERSION,
    LibraryRecord,
    normalize_tags,
)
from tju_info_retrieval.models.research_profile import (
    ResearchProfile,
    default_profile,
)
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.models.summary import StructuredSummary
from tju_info_retrieval.services.enhanced_cache import EnhancedSummaryCache
from tju_info_retrieval.services.enhanced_provider import LLMEnhancedProvider
from tju_info_retrieval.services.library_store import LibraryStore
from tju_info_retrieval.services.llm_provider import OpenAICompatibleProvider
from tju_info_retrieval.services.prompts.enhanced_summary_prompt import (
    ENHANCED_PROMPT_VERSION,
)
from tju_info_retrieval.services.summary_provider import (
    EvidenceBundle,
    SummaryContext,
)
from tju_info_retrieval.services.summary_service import (
    MODE_BASIC,
    MODE_ENHANCED,
    SummaryService,
)

VALID_CONFIG = {
    "enabled": True,
    "provider": "openai-compatible",
    "base_url": "https://api.example.invalid/v1",
    "model": "tju-llm",
    "api_key": "test-key-not-real",
}

ABSTRACT = (
    "本文综述太赫兹技术在癌症诊断医学中的应用，包括太赫兹成像在皮肤癌、"
    "乳腺癌等浅表肿瘤的临床研究案例，并讨论未来发展趋势。"
)

RESULT_DICT = {
    "rank": 1,
    "title": "太赫兹技术在癌症诊断医学中的应用",
    "authors": ["何禹霖"],
    "source": "中国激光医学杂志",
    "year": "2026",
    "database": "CNKI",
    "detail_url": "https://kns.cnki.net/kcms2/article/abstract?v=abc",
    "doi": "10.1000/thz.2026.001",
    "abstract": ABSTRACT,
    "artifact_type": "paper",
    "artifact_metadata": {},
}

ENHANCED_PAYLOAD = {
    "document_type": "review",
    "document_type_reason": "摘要出现综述类表述。",
    "direction_match_level": "weak",
    "reading_priority": "defer",
    "research_background": "问题/对象/困难",
    "technical_route": "成像 → 光谱 → 临床转化",
    "innovation_points": "系统梳理了临床案例",
    "relation_to_user_direction": "弱相关：属于太赫兹医学应用，而非 THz-ISAC 通信。",
    "reading_recommendation": "阅读优先级：可暂缓\n理由：与 THz-ISAC 距离较远。",
    "limitations": "从当前摘要推测覆盖范围有限。",
}


def _thz_profile() -> ResearchProfile:
    profile = default_profile()
    profile.research_area = "太赫兹"
    profile.sub_direction = ["THz-ISAC"]
    profile.keywords = ["terahertz", "THz", "ISAC"]
    return profile


class _FakeResponse:
    def __init__(self, body: str, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = body

    def json(self):
        return json.loads(self._body)


def _transport_for(payload: dict, counter: list | None = None):
    def transport(*args, **kwargs):
        if counter is not None:
            counter.append(1)
        body = {"choices": [{"message": {"role": "assistant",
                                         "content": json.dumps(payload)}}]}
        return _FakeResponse(json.dumps(body))
    return transport


def _counting_transport(payload: dict, counter: list):
    return _transport_for(payload, counter)


class _NoNetwork:
    """patch requests.post：任何真实网络调用都会让测试失败。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("测试期间不应发起真实网络请求")

    def __enter__(self):
        self._patch = mock.patch.object(requests, "post", self)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False


# ============================================================
# 4-5 / 30-31：schema、legacy 迁移、兼容、原子写
# ============================================================


class TestLibrarySchema(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "library.json"
        self.store = LibraryStore(self.path)

    def test_new_schema_version_written(self):
        """5. 保存后文件为 v2 结构（顶层 schema_version + records）。"""
        self.store.save(LibraryRecord(title="Paper A", database="CNKI"))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsInstance(raw, dict)
        self.assertEqual(raw["schema_version"], LIBRARY_SCHEMA_VERSION)
        self.assertEqual(len(raw["records"]), 1)
        self.assertEqual(raw["records"][0]["schema_version"], 2)

    def test_legacy_bare_array_migrates(self):
        """4. 旧库（顶层裸数组、仅基础字段）可加载并补默认值。"""
        legacy = [{
            "id": "legacy-1",
            "title": "旧库论文",
            "authors": ["张三"],
            "source": "某期刊",
            "year": "2024",
            "detail_url": "https://example.com/legacy",
        }]
        self.path.write_text(json.dumps(legacy, ensure_ascii=False),
                             encoding="utf-8")
        records = self.store.load_all()
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.title, "旧库论文")
        self.assertIsNone(rec.basic_summary)
        self.assertIsNone(rec.enhanced_summary)
        self.assertEqual(rec.tags, [])
        self.assertEqual(rec.note, "")
        self.assertEqual(rec.reading_status, "unread")
        self.assertEqual(rec.artifact_type, "paper")

    def test_legacy_title_only_record_does_not_crash(self):
        """30. 极端旧记录（只有 title+authors+year）不崩溃。"""
        self.path.write_text(json.dumps(
            {"title": "只有标题", "authors": "张三", "year": 2024},
            ensure_ascii=False), encoding="utf-8")
        records = self.store.load_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "只有标题")
        self.assertEqual(records[0].reading_status, "unread")
        # 保存后写为新 schema
        self.store.save(records[0])
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], LIBRARY_SCHEMA_VERSION)

    def test_legacy_save_roundtrip_writes_new_schema(self):
        """30/5. legacy → load → default fields → save → new schema。"""
        self.path.write_text(json.dumps(
            [{"id": "legacy-id-1", "title": "T", "authors": ["A"],
              "year": 2024}]),
            encoding="utf-8")
        rec = self.store.load_all()[0]
        self.assertIsNone(rec.basic_summary)
        rec.set_tags(["THz"])
        self.store.save(rec)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], 2)
        self.assertEqual(len(raw["records"]), 1)
        self.assertEqual(raw["records"][0]["tags"], ["THz"])
        # 分析字段以 null 落盘（旧记录无分析），不生造内容
        self.assertIsNone(raw["records"][0]["basic_summary"])
        self.assertIsNone(raw["records"][0]["enhanced_summary"])

    def test_corrupted_file_returns_empty_without_crash(self):
        self.path.write_text("not-json{{{", encoding="utf-8")
        self.assertEqual(self.store.load_all(), [])

    def test_atomic_write_leaves_no_temp_files(self):
        """31. 原子写：目录中不留 .tmp 残留，且内容完整可读。"""
        for i in range(5):
            self.store.save(LibraryRecord(title=f"P{i}"))
        leftovers = list(Path(self._tmp.name).glob("*.tmp"))
        self.assertEqual(leftovers, [])
        self.assertEqual(len(self.store.load_all()), 5)

    def test_write_failure_does_not_corrupt_existing_file(self):
        """31. 写入失败时原文件保持可读（不产生半截 JSON）。"""
        self.store.save(LibraryRecord(title="Good"))
        before = self.path.read_text(encoding="utf-8")
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.save(LibraryRecord(title="Bad"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(len(self.store.load_all()), 1)


# ============================================================
# 6-7：收藏新增 / 重复收藏更新
# ============================================================


class TestFavoriteDedupe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = LibraryStore(Path(self._tmp.name) / "library.json")

    def _record(self, **overrides) -> LibraryRecord:
        data = dict(RESULT_DICT)
        data.update(overrides)
        return LibraryRecord.from_search_result(data)

    def test_favorite_new_item(self):
        """6. 首次收藏新增一条记录。"""
        record, was_update = self.store.upsert_favorite(self._record())
        self.assertFalse(was_update)
        self.assertEqual(len(self.store.load_all()), 1)

    def test_duplicate_favorite_updates_same_record(self):
        """7. 同 DOI 再次收藏 → 更新，不新增。"""
        first, _ = self.store.upsert_favorite(self._record())
        second, was_update = self.store.upsert_favorite(self._record())
        self.assertTrue(was_update)
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.store.load_all()), 1)

    def test_duplicate_by_url_without_doi(self):
        """7. 无 DOI 时按 detail_url 去重。"""
        a, _ = self.store.upsert_favorite(
            self._record(doi=None))
        b, was_update = self.store.upsert_favorite(
            self._record(doi=None, title="标题略有变化"))
        self.assertTrue(was_update)
        self.assertEqual(a.id, b.id)
        self.assertEqual(len(self.store.load_all()), 1)

    def test_duplicate_by_title_author_year(self):
        """7. 无 DOI/URL 时按 标题+作者+年份 去重。"""
        self.store.upsert_favorite(self._record(doi=None, detail_url=None))
        _, was_update = self.store.upsert_favorite(
            self._record(doi=None, detail_url=None))
        self.assertTrue(was_update)
        self.assertEqual(len(self.store.load_all()), 1)

    def test_normalized_title_matches(self):
        """7. 标题规范化兜底：空白/标点差异视为同一篇。"""
        self.store.upsert_favorite(self._record(
            doi=None, detail_url=None, title="太赫兹 技术：癌症诊断"))
        _, was_update = self.store.upsert_favorite(self._record(
            doi=None, detail_url=None, title="太赫兹技术癌症诊断"))
        self.assertTrue(was_update)
        self.assertEqual(len(self.store.load_all()), 1)

    def test_different_papers_not_merged(self):
        self.store.upsert_favorite(self._record(doi="10.1/a", title="A"))
        _, was_update = self.store.upsert_favorite(
            self._record(doi="10.1/b", title="B", detail_url="https://x/b"))
        self.assertFalse(was_update)
        self.assertEqual(len(self.store.load_all()), 2)

    def test_update_preserves_user_data(self):
        """7. 重复收藏不覆盖用户标签/状态/笔记，并补全空元数据。"""
        first = self._record(abstract=None)
        first.set_tags(["保留", "标签"])
        first.set_reading_status("reading")
        first.set_note("我的笔记")
        self.store.upsert_favorite(first)

        second = self._record()  # 带摘要
        saved, was_update = self.store.upsert_favorite(second)
        self.assertTrue(was_update)
        merged = self.store.load_all()[0]
        self.assertEqual(merged.tags, ["保留", "标签"])
        self.assertEqual(merged.reading_status, "reading")
        self.assertEqual(merged.note, "我的笔记")
        self.assertEqual(merged.abstract, ABSTRACT)  # 空字段被补全


# ============================================================
# 8-15：分析结果持久化
# ============================================================


class TestAnalysisPersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "library.json"
        self.store = LibraryStore(self.path)

    def _enhanced(self, **overrides) -> EnhancedSummary:
        data = dict(ENHANCED_PAYLOAD)
        data.update(overrides)
        data["provider"] = "openai-compatible"
        data["ai_generated"] = True
        return EnhancedSummary.from_dict(data)

    def test_basic_summary_persistence_keeps_source_basis(self):
        """8/14(部分). 基础整理持久化，source_basis 不丢。"""
        basic = StructuredSummary(
            research_content="研究内容",
            core_technology="核心技术",
            main_results="主要成果",
            application_value="应用价值",
            source_basis={"research_content": "abstract",
                          "main_results": "summary"},
        )
        record = LibraryRecord.from_search_result(RESULT_DICT)
        record.set_basic_summary(basic)
        self.store.save(record)

        reloaded = LibraryStore(self.path).load_all()[0]
        self.assertEqual(reloaded.basic_summary["research_content"], "研究内容")
        self.assertEqual(
            reloaded.basic_summary["source_basis"],
            {"research_content": "abstract", "main_results": "summary"})
        self.assertTrue(reloaded.has_basic_summary())

    def test_enhanced_summary_full_persistence(self):
        """9/10/11/12/13/14/15. EnhancedSummary 结构化字段与元信息全部落盘。"""
        record = LibraryRecord.from_search_result(RESULT_DICT)
        record.set_enhanced_summary(
            self._enhanced(),
            provider="openai-compatible",
            model="tju-llm",
            prompt_version=ENHANCED_PROMPT_VERSION,
            profile_version="p3",
        )
        self.store.save(record)

        reloaded = LibraryStore(self.path).load_all()[0]
        data = reloaded.enhanced_summary
        self.assertTrue(reloaded.has_enhanced_summary())
        # 9. 结构化字段（不是只有一段文本）
        for key, value in ENHANCED_PAYLOAD.items():
            self.assertEqual(data[key], value, key)
        self.assertEqual(data["provider"], "openai-compatible")
        self.assertTrue(data["ai_generated"])
        # 10/11/12
        self.assertEqual(reloaded.document_type, "review")
        self.assertEqual(reloaded.direction_match_level, "weak")
        self.assertEqual(reloaded.reading_priority, "defer")
        # 13 provider / model
        self.assertEqual(reloaded.enhanced_provider, "openai-compatible")
        self.assertEqual(reloaded.enhanced_model, "tju-llm")
        self.assertEqual(reloaded.analysis_source_label(),
                         "openai-compatible / tju-llm")
        # 14/15
        self.assertEqual(reloaded.enhanced_prompt_version, "v4")
        self.assertEqual(reloaded.enhanced_profile_version, "p3")
        self.assertTrue(reloaded.enhanced_at)

    def test_derived_fields_written_flat(self):
        """派生字段写入 JSON（供外部工具查看），读回仍以 enhanced_summary 为准。"""
        record = LibraryRecord.from_search_result(RESULT_DICT)
        record.set_enhanced_summary(self._enhanced(), provider="p")
        self.store.save(record)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        rec = raw["records"][0]
        self.assertEqual(rec["document_type"], "review")
        self.assertEqual(rec["direction_match_level"], "weak")
        self.assertEqual(rec["reading_priority"], "defer")

    def test_analysis_survives_restart(self):
        """20. 重启（新 store / 新进程语义）后分析结果仍在。"""
        record = LibraryRecord.from_search_result(RESULT_DICT)
        record.set_basic_summary(StructuredSummary(research_content="R"))
        record.set_enhanced_summary(
            self._enhanced(), provider="openai-compatible", model="tju-llm",
            prompt_version="v4", profile_version="p2")
        self.store.save(record)

        fresh = LibraryStore(self.path)          # 模拟重启
        rec = fresh.load_all()[0]
        self.assertEqual(rec.basic_summary["research_content"], "R")
        self.assertEqual(rec.document_type, "review")
        self.assertEqual(rec.enhanced_profile_version, "p2")

    # ---------- 21/22：过期判断 ----------

    def test_profile_staleness(self):
        """21. 画像版本变化 → 标记"基于旧科研画像"。"""
        record = LibraryRecord.from_search_result(RESULT_DICT)
        record.set_enhanced_summary(self._enhanced(), profile_version="p3")
        self.assertFalse(record.is_profile_stale("p3"))
        self.assertTrue(record.is_profile_stale("p5"))
        # 空画像不产生误报
        self.assertFalse(record.is_profile_stale(""))

    def test_prompt_staleness(self):
        """22. Prompt 版本变化 → 标记"AI分析版本较旧"。"""
        record = LibraryRecord.from_search_result(RESULT_DICT)
        record.set_enhanced_summary(self._enhanced(), prompt_version="v3")
        self.assertTrue(record.is_prompt_stale("v4"))
        self.assertFalse(record.is_prompt_stale("v3"))

    def test_no_analysis_never_stale(self):
        record = LibraryRecord.from_search_result(RESULT_DICT)
        self.assertFalse(record.is_profile_stale("p9"))
        self.assertFalse(record.is_prompt_stale("v9"))


# ============================================================
# 16-19：标签 / 状态 / 笔记
# ============================================================


class TestUserFields(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "library.json"
        self.store = LibraryStore(self.path)
        self.record = LibraryRecord.from_search_result(RESULT_DICT)
        self.store.save(self.record)

    def test_tags_add_and_remove(self):
        """16. 标签增删。"""
        updated = self.store.update(self.record.id, tags=["THz", "信道"])
        self.assertEqual(updated.tags, ["THz", "信道"])
        updated = self.store.update(self.record.id, tags=["THz"])
        self.assertEqual(updated.tags, ["THz"])
        updated = self.store.update(self.record.id, tags=[])
        self.assertEqual(updated.tags, [])

    def test_tags_dedupe_and_trim(self):
        """17. 标签 strip / 去重 / 空白归一。"""
        self.assertEqual(normalize_tags([" a ", "a", "b", "", "  "]),
                         ["a", "b"])
        updated = self.store.update(
            self.record.id, tags=["THz", " THz ", "THz", "6G"])
        self.assertEqual(updated.tags, ["THz", "6G"])

    def test_tags_limits(self):
        """17. 单标签 ≤30 字符、单论文 ≤20 个。"""
        long_tag = "x" * 50
        updated = self.store.update(self.record.id, tags=[long_tag])
        self.assertEqual(len(updated.tags[0]), 30)
        many = [f"t{i}" for i in range(40)]
        updated = self.store.update(self.record.id, tags=many)
        self.assertEqual(len(updated.tags), 20)

    def test_reading_status_set_and_persist(self):
        """18. 阅读状态写入与重载。"""
        for status in ("skimmed", "reading", "finished", "unread"):
            self.store.update(self.record.id, reading_status=status)
            reloaded = LibraryStore(self.path).load_all()[0]
            self.assertEqual(reloaded.reading_status, status)

    def test_reading_status_is_not_ai_priority(self):
        """18. 阅读状态与 AI 阅读优先级是两个独立字段。"""
        record = LibraryRecord.from_search_result(RESULT_DICT)
        record.set_enhanced_summary(
            EnhancedSummary.from_dict({**ENHANCED_PAYLOAD,
                                       "ai_generated": True}))
        record.set_reading_status("finished")
        self.assertEqual(record.reading_status, "finished")
        self.assertEqual(record.reading_priority, "defer")

    def test_user_note(self):
        """19. 用户笔记（多行文本）持久化，不被改写。"""
        note = "周五组会重点看图3\n这个模型可能用于沙尘 THz channel"
        self.store.update(self.record.id, note=note)
        reloaded = LibraryStore(self.path).load_all()[0]
        self.assertEqual(reloaded.note, note)
        self.assertIn("图3", reloaded.note)

    def test_prompt_requires_chinese_label_in_text(self):
        """12/实数缺陷回归：reading_recommendation 文本用中文标签，
        reading_priority 只在 JSON 字段里用英文枚举（禁止枚举进文本）。"""
        from tju_info_retrieval.services.prompts.enhanced_summary_prompt import (
            ENHANCED_SYSTEM_PROMPT,
        )

        self.assertIn("阅读优先级：泛读", ENHANCED_SYSTEM_PROMPT)
        self.assertIn("禁止把 deep_read/priority_read/skim/defer 等英文枚举写进文本",
                      ENHANCED_SYSTEM_PROMPT)
        self.assertIn("reading_priority 只能取英文枚举", ENHANCED_SYSTEM_PROMPT)

    def test_reading_priority_parser_enum_only(self):
        """12. parser 只接受枚举值；非法值留空（不猜、不从文本提炼）。"""
        from tju_info_retrieval.services.prompts.enhanced_summary_prompt import (
            parse_enhanced_json,
        )

        for value in ("deep_read", "priority_read", "skim", "defer"):
            fields = parse_enhanced_json(json.dumps(
                {**ENHANCED_PAYLOAD, "reading_priority": value}))
            self.assertEqual(fields["reading_priority"], value)
        fields = parse_enhanced_json(json.dumps(
            {**ENHANCED_PAYLOAD, "reading_priority": "泛读"}))
        self.assertEqual(fields["reading_priority"], "")
        fields = parse_enhanced_json(json.dumps(
            {**ENHANCED_PAYLOAD, "reading_priority": ""}))
        self.assertEqual(fields["reading_priority"], "")

    def test_legacy_enhanced_summary_without_priority(self):
        """旧分析（无 reading_priority 字段）加载 → 未标注，不报错。"""
        record = LibraryRecord.from_dict({
            "title": "旧分析论文",
            "enhanced_summary": {
                "document_type": "review",
                "direction_match_level": "weak",
                "research_background": "b",
            },
            "enhanced_provider": "openai-compatible",
        })
        self.assertEqual(record.document_type, "review")
        self.assertEqual(record.direction_match_level, "weak")
        self.assertEqual(record.reading_priority, "")
        self.assertTrue(record.has_enhanced_summary())

    def test_no_secret_in_library_file(self):
        """32. 论文库文件不含 API Key / token / cookie / 认证信息。"""
        record = LibraryRecord.from_search_result(RESULT_DICT)
        record.set_enhanced_summary(
            EnhancedSummary.from_dict({**ENHANCED_PAYLOAD,
                                       "provider": "openai-compatible",
                                       "ai_generated": True}),
            provider="openai-compatible", model="tju-llm",
            prompt_version="v4", profile_version="p1")
        self.store.save(record)
        self.store.update(record.id, note="笔记", tags=["THz"])
        raw = self.path.read_text(encoding="utf-8").lower()
        for banned in ("api_key", "apikey", "bearer", "authorization",
                       "cookie", "password", "token", "secret",
                       "storage_state", "credential"):
            self.assertNotIn(banned, raw, banned)


# ============================================================
# 23-29：重新分析 / 缓存 / API 隔离
# ============================================================


class TestReanalyzeAndApiIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name) / "cache"
        self.store = LibraryStore(Path(self._tmp.name) / "library.json")
        self.profile = _thz_profile()

    def _result(self, **overrides) -> SearchResult:
        data = dict(RESULT_DICT)
        data.update(overrides)
        return SearchResult.from_dict(data)

    def _provider(self, counter: list | None = None, payload=None):
        provider = LLMEnhancedProvider(
            llm_client=OpenAICompatibleProvider(
                VALID_CONFIG,
                transport=_transport_for(payload or ENHANCED_PAYLOAD, counter)),
            cache=EnhancedSummaryCache(self.cache_dir),
        )
        return provider

    def test_basic_mode_zero_api(self):
        """28. 基础整理不调用任何 API（requests.post 被禁用）。"""
        with _NoNetwork():
            service = SummaryService(config={"enabled": False})
            out = service.summarize(
                MODE_BASIC, self._result(), research_profile=self.profile)
        self.assertTrue(out.research_content)

    def test_favorite_zero_api(self):
        """26. 收藏流程 API calls = 0（即使已有分析也只做本地写入）。"""
        with _NoNetwork():
            record = LibraryRecord.from_search_result(dict(RESULT_DICT))
            record.set_basic_summary(StructuredSummary(research_content="R"))
            self.store.upsert_favorite(record)
        self.assertEqual(len(self.store.load_all()), 1)

    def test_search_chain_zero_api(self):
        """27. 搜索链路不引入 LLM/网络依赖（AST 静态核验）。"""
        import ast

        files = [
            ROOT / "src/tju_info_retrieval/services/search_service.py",
            ROOT / "src/tju_info_retrieval/services/library_store.py",
            ROOT / "src/tju_info_retrieval/models/library.py",
        ]
        banned = ("llm_provider", "enhanced_provider", "summary_service",
                  "requests", "api_config_manager")
        for f in files:
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    for word in banned:
                        self.assertNotIn(
                            word, name, f"{f.name}: {name}")

    def test_enhanced_only_on_user_action(self):
        """29. 只有用户主动分析才调用 API；同参数第二次走缓存。"""
        counter: list = []
        provider = self._provider(counter)
        result = self._result()
        bundle = EvidenceBundle.from_result(result)
        ctx = SummaryContext.from_result(
            result, "太赫兹", research_profile=self.profile)
        first = provider.summarize(bundle, ctx)
        self.assertEqual(len(counter), 1)          # 仅此一次真实请求
        self.assertTrue(first.ai_generated)

        second = provider.summarize(bundle, ctx)   # 缓存命中
        self.assertEqual(len(counter), 1)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_cache_reused_across_library_reanalyze(self):
        """25. 论文库重新分析命中已有 EnhancedCache（不重复消耗 API）。"""
        counter: list = []
        provider = self._provider(counter)
        result = self._result()
        bundle = EvidenceBundle.from_result(result)
        ctx = SummaryContext.from_result(
            result, "太赫兹", research_profile=self.profile)
        provider.summarize(bundle, ctx)             # 搜索区分析
        # 论文库重新分析：同 Evidence / profile / prompt / provider / model
        provider.summarize(bundle, ctx)
        self.assertEqual(len(counter), 1)

    def test_reanalyze_success_updates_record(self):
        """24. 重新分析成功 → 记录更新（新 document_type / 优先级 / profile 版本）。"""
        record = LibraryRecord.from_search_result(dict(RESULT_DICT))
        record.set_enhanced_summary(
            EnhancedSummary.from_dict({**ENHANCED_PAYLOAD,
                                       "ai_generated": True}),
            provider="openai-compatible", model="tju-llm",
            prompt_version="v3", profile_version="p1")
        self.store.save(record)
        self.assertTrue(record.is_profile_stale("p5"))

        new_payload = dict(ENHANCED_PAYLOAD)
        new_payload["direction_match_level"] = "strong"
        new_payload["document_type"] = "experimental_method"
        new_payload["reading_priority"] = "deep_read"
        provider = self._provider(payload=new_payload)
        result = self._result()
        summary = provider.summarize(
            EvidenceBundle.from_result(result),
            SummaryContext.from_result(
                result, "太赫兹", research_profile=self.profile))

        self.store.update(
            record.id,
            enhanced_summary=summary,
            enhanced_provider=summary.provider,
            enhanced_model="tju-llm",
            enhanced_prompt_version=ENHANCED_PROMPT_VERSION,
            enhanced_profile_version="p5",
        )
        reloaded = self.store.load_all()[0]
        self.assertEqual(reloaded.document_type, "experimental_method")
        self.assertEqual(reloaded.direction_match_level, "strong")
        self.assertEqual(reloaded.reading_priority, "deep_read")
        self.assertEqual(reloaded.enhanced_profile_version, "p5")
        self.assertEqual(reloaded.enhanced_prompt_version, ENHANCED_PROMPT_VERSION)
        # 过期警告消失
        self.assertFalse(reloaded.is_profile_stale("p5"))
        self.assertFalse(reloaded.is_prompt_stale(ENHANCED_PROMPT_VERSION))
        # 用户数据不受影响
        self.assertEqual(reloaded.reading_status, "unread")

    def test_reanalyze_failure_keeps_old_result(self):
        """23. 重新分析失败（降级结果）→ 旧结果必须保留。"""
        record = LibraryRecord.from_search_result(dict(RESULT_DICT))
        record.set_enhanced_summary(
            EnhancedSummary.from_dict({**ENHANCED_PAYLOAD,
                                       "ai_generated": True}),
            provider="openai-compatible", model="tju-llm",
            prompt_version="v3", profile_version="p1")
        self.store.save(record)

        # 失败路径：provider 返回降级结果（ai_generated=False）
        provider = LLMEnhancedProvider(
            llm_client=OpenAICompatibleProvider(
                {"enabled": True, "provider": "openai-compatible",
                 "base_url": "https://api.invalid/v1", "model": "m",
                 "api_key": ""},   # 无凭据 → validate 失败 → 降级
                transport=_transport_for(ENHANCED_PAYLOAD)),
            cache=EnhancedSummaryCache(self.cache_dir),
        )
        result = self._result()
        degraded = provider.summarize(
            EvidenceBundle.from_result(result),
            SummaryContext.from_result(result, "太赫兹",
                                       research_profile=self.profile))
        self.assertFalse(degraded.ai_generated)

        # UI 逻辑：仅当 ai_generated 为真才写库（此处模拟该分支）
        if degraded.ai_generated:
            self.store.update(record.id, enhanced_summary=degraded,
                              enhanced_provider=degraded.provider,
                              enhanced_model="tju-llm",
                              enhanced_prompt_version=ENHANCED_PROMPT_VERSION,
                              enhanced_profile_version="p2")

        reloaded = self.store.load_all()[0]
        self.assertEqual(reloaded.document_type, "review")     # 旧结果仍在
        self.assertEqual(reloaded.direction_match_level, "weak")
        self.assertEqual(reloaded.enhanced_profile_version, "p1")

    def test_profile_change_invalidates_library_analysis_warning(self):
        """21. 画像变化后论文库标记过期但保留原分析（不自动重算）。"""
        counter: list = []
        provider = self._provider(counter)
        result = self._result()
        ctx = SummaryContext.from_result(
            result, "太赫兹", research_profile=self.profile)
        summary = provider.summarize(EvidenceBundle.from_result(result), ctx)

        record = LibraryRecord.from_search_result(dict(RESULT_DICT))
        record.set_enhanced_summary(summary, provider=summary.provider,
                                   model="tju-llm", prompt_version="v4",
                                   profile_version="p1")
        self.store.save(record)

        # 用户修改画像 → 新版本 p2（不改库，不自动调用 API）
        self.assertTrue(self.store.load_all()[0].is_profile_stale("p2"))
        self.assertEqual(len(counter), 1)   # 未触发任何新请求


# ============================================================
# 33-35：UI 响应式
# ============================================================


class TestLibraryWindowResponsive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = LibraryStore(Path(self._tmp.name) / "library.json")
        record = LibraryRecord.from_search_result(dict(RESULT_DICT))
        record.set_enhanced_summary(
            EnhancedSummary.from_dict({**ENHANCED_PAYLOAD,
                                       "provider": "openai-compatible",
                                       "ai_generated": True}),
            # 保存时为旧版本 → 打开论文库应提示过期（p1 ≠ 当前 p2；
            # v3 ≠ 当前 Prompt 版本）
            provider="openai-compatible", model="tju-llm",
            prompt_version="v3", profile_version="p1")
        record.set_tags(["医学应用", "泛读参考"])
        record.set_reading_status("skimmed")
        record.set_note("测试备注")
        self.store.save(record)

    def _window(self):
        from tju_info_retrieval.ui.library_window import LibraryWindow

        win = LibraryWindow(self.store)
        win.set_profile_version("p2")
        win.set_ai_configured(True)
        return win

    def _assert_usable(self, width: int, height: int):
        win = self._window()
        win.resize(width, height)
        win.show()
        self.app.processEvents()
        self.assertEqual(win.table.rowCount(), 1)
        # 表格列完整（不因窄窗口丢列；含 2.9-C-E 的“选择”列）
        self.assertEqual(win.table.columnCount(), 9)
        self.assertGreater(win.table.horizontalHeader().height(), 0)
        # 详情 Tabs 可用
        self.assertEqual(win.detail_tabs.count(), 4)
        # 按钮完整且在窗口内（无裁切/溢出）
        for button in (win.btn_open, win.btn_edit_tags, win.btn_change_status,
                       win.btn_reanalyze_basic, win.btn_reanalyze_ai,
                       win.btn_delete, win.btn_refresh):
            self.assertTrue(button.isVisible(), button.text())
            top_left = button.mapTo(win, button.rect().topLeft())
            self.assertGreaterEqual(top_left.x(), 0, button.text())
            self.assertLessEqual(
                top_left.x() + button.width(), win.width() + 1, button.text())
            self.assertLessEqual(
                top_left.y() + button.height(), win.height() + 1, button.text())
        # 详情可滚动（长内容不撑破窗口）
        win.table.selectRow(0)
        self.app.processEvents()
        self.assertIn("测试备注", win.text_note.toPlainText())
        win.close()

    def test_920x560_usable(self):
        """33. 920×560 可用（表格可见 / 详情可滚动 / 按钮完整）。"""
        self._assert_usable(920, 560)

    def test_1366x768_usable(self):
        """34. 1366×768 可用。"""
        self._assert_usable(1366, 768)

    def test_1920x1080_usable(self):
        """35. 1920×1080 可用。"""
        self._assert_usable(1920, 1080)

    def test_filters_and_detail_render(self):
        """筛选与详情渲染（含过期提示）。"""
        win = self._window()
        win.show()
        self.app.processEvents()
        win.input_search.setText("不存在的标题")
        self.app.processEvents()
        self.assertEqual(win.table.rowCount(), 0)
        win.input_search.clear()
        self.app.processEvents()
        self.assertEqual(win.table.rowCount(), 1)

        win.table.selectRow(0)
        self.app.processEvents()
        # 表格列语义
        self.assertEqual(win.table.item(0, 3).text(), "综述")
        self.assertEqual(win.table.item(0, 4).text(), "弱相关")
        self.assertEqual(win.table.item(0, 5).text(), "可暂缓")
        self.assertEqual(win.table.item(0, 6).text(), "已泛读")
        self.assertEqual(win.table.item(0, 7).text(), "医学应用,泛读参考")
        # 详情：AI 分析含来源与过期提示（不改动旧结果，只提示）
        self.assertIn("openai-compatible / tju-llm", win.ai_meta.text())
        self.assertIn("该分析基于旧科研画像", win.banner_ai.text())
        # 基础整理未做 → 提示
        self.assertIn("尚未进行基础整理", win.label_basic_basis.text())
        win.close()

    def test_prompt_staleness_banner(self):
        win = self._window()
        win.show()
        self.app.processEvents()
        win.set_profile_version("p2")
        win.table.selectRow(0)
        self.app.processEvents()
        self.assertIn("AI分析版本较旧", win.banner_ai.text())
        win.close()

    def test_delete_requires_confirmation(self):
        win = self._window()
        win.table.selectRow(0)
        with mock.patch.object(QMessageBox, "question",
                               return_value=QMessageBox.StandardButton.No):
            win.btn_delete.click()
        self.assertEqual(len(self.store.load_all()), 1)   # 取消不删除
        with mock.patch.object(QMessageBox, "question",
                               return_value=QMessageBox.StandardButton.Yes):
            win.btn_delete.click()
        self.assertEqual(len(self.store.load_all()), 0)
        win.close()

    def test_reanalyze_signals_emitted(self):
        win = self._window()
        win.table.selectRow(0)
        got = []
        win.reanalyze_basic_requested.connect(lambda r: got.append(("basic", r)))
        win.reanalyze_enhanced_requested.connect(
            lambda r: got.append(("enhanced", r)))
        win.btn_reanalyze_basic.click()
        win.btn_reanalyze_ai.click()
        self.assertEqual([k for k, _ in got], ["basic", "enhanced"])
        self.assertEqual(got[0][1].title, RESULT_DICT["title"])
        win.close()

    def test_reanalyze_blocked_when_ai_not_configured(self):
        win = self._window()
        win.set_ai_configured(False)
        win.table.selectRow(0)
        got = []
        win.reanalyze_enhanced_requested.connect(lambda r: got.append(r))
        with mock.patch.object(QMessageBox, "information", return_value=None):
            win.btn_reanalyze_ai.click()
        self.assertEqual(got, [])
        win.close()


# ============================================================
# 主窗口集成：收藏 → 论文库 → 分析同步（Case A/B/C）
# ============================================================


class TestMainWindowLibraryIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["TEST_USER_DATA_ROOT"] = self._tmp.name
        self.addCleanup(lambda: os.environ.pop("TEST_USER_DATA_ROOT", None))
        self.library_path = Path(self._tmp.name) / "library.json"

    def _window(self):
        from tju_info_retrieval.ui.main_window import MainWindow

        win = MainWindow()
        win._library_store = LibraryStore(self.library_path)
        win._research_profile = _thz_profile()
        return win

    def test_case_c_analyze_then_favorite(self):
        """Case C：先分析（basic+enhanced）→ 再收藏 → 一次写入当前分析。"""
        win = self._window()
        win._on_search_done([dict(RESULT_DICT)])
        win.table.selectRow(0)
        result = SearchResult.from_dict(dict(RESULT_DICT))
        # 模拟此前的分析（内存分析缓存）
        win._basic_summary_cache[win._analysis_key(dict(RESULT_DICT))] = (
            StructuredSummary(research_content="R", core_technology="C"))
        win._enhanced_summary_cache[win._analysis_key(dict(RESULT_DICT))] = (
            EnhancedSummary.from_dict({**ENHANCED_PAYLOAD,
                                       "provider": "openai-compatible",
                                       "ai_generated": True}))
        with _NoNetwork():
            win._on_favorite()
        rec = win._library_store.load_all()[0]
        self.assertEqual(rec.basic_summary["research_content"], "R")
        self.assertEqual(rec.document_type, "review")
        self.assertTrue(rec.enhanced_prompt_version)
        # 分析元信息来自当前配置（model 取配置默认值，非伪造空值）
        self.assertTrue(rec.enhanced_model)
        self.assertEqual(rec.enhanced_provider, "openai-compatible")
        win.close()
        win.deleteLater()

    def test_case_a_favorite_then_basic(self):
        """Case A：收藏 → 后来基础整理 → 论文库记录更新 basic_summary。"""
        win = self._window()
        win._on_search_done([dict(RESULT_DICT)])
        win.table.selectRow(0)
        win._on_favorite()
        self.assertIsNone(win._library_store.load_all()[0].basic_summary)

        win.btn_summary.setEnabled(True)
        # main_window 在模块级导入 SummaryDialog → patch 该引用（否则弹真模态）
        with mock.patch(
                "tju_info_retrieval.ui.main_window.SummaryDialog") as Dlg:
            Dlg.return_value.exec.return_value = None
            win.combo_summary_mode.setCurrentIndex(0)   # 基础整理
            win._on_summarize()
        rec = win._library_store.load_all()[0]
        self.assertTrue(rec.has_basic_summary())
        self.assertTrue(rec.basic_summary["research_content"])
        win.close()
        win.deleteLater()

    def test_case_b_favorite_then_enhanced(self):
        """Case B：收藏 → 后来 AI 增强分析 → 论文库记录更新 enhanced_summary。"""
        win = self._window()
        win._on_search_done([dict(RESULT_DICT)])
        win.table.selectRow(0)
        win._on_favorite()
        rec = win._library_store.load_all()[0]

        summary = EnhancedSummary.from_dict({**ENHANCED_PAYLOAD,
                                            "provider": "openai-compatible",
                                            "ai_generated": True})
        win._sync_enhanced_to_library(SearchResult.from_dict(
            dict(RESULT_DICT)), summary)
        reloaded = win._library_store.load_all()[0]
        self.assertEqual(reloaded.id, rec.id)
        self.assertEqual(reloaded.document_type, "review")
        self.assertEqual(reloaded.direction_match_level, "weak")
        self.assertEqual(reloaded.reading_priority, "defer")
        self.assertEqual(reloaded.enhanced_provider, "openai-compatible")
        self.assertTrue(reloaded.enhanced_prompt_version)
        win.close()
        win.deleteLater()

    def test_duplicate_favorite_updates_status_message(self):
        """7. 重复收藏 → 状态栏提示"论文库记录已更新"，不新增记录。"""
        win = self._window()
        win._on_search_done([dict(RESULT_DICT)])
        win.table.selectRow(0)
        win._on_favorite()
        self.assertIn("已收藏", win.label_status.text())
        win._on_favorite()
        self.assertIn("论文库记录已更新", win.label_status.text())
        self.assertEqual(len(win._library_store.load_all()), 1)
        win.close()
        win.deleteLater()

    def test_restart_persistence_via_new_window(self):
        """20. 关闭窗口 → 新建 MainWindow（重启语义）→ 记录仍在。"""
        win = self._window()
        win._on_search_done([dict(RESULT_DICT)])
        win.table.selectRow(0)
        win._on_favorite()
        win._library_store.update(
            win._library_store.load_all()[0].id,
            tags=["重启保留"], reading_status="reading", note="笔记")
        win.close()
        win.deleteLater()

        win2 = self._window()
        rec = win2._library_store.load_all()[0]
        self.assertEqual(rec.tags, ["重启保留"])
        self.assertEqual(rec.reading_status, "reading")
        self.assertEqual(rec.note, "笔记")
        win2.close()
        win2.deleteLater()

    def test_library_record_lookup_by_identity(self):
        """收藏后可按身份找到记录（重新分析 / 同步用）。"""
        win = self._window()
        win._on_search_done([dict(RESULT_DICT)])
        win.table.selectRow(0)
        self.assertIsNone(win._library_record_for(
            SearchResult.from_dict(dict(RESULT_DICT))))
        win._on_favorite()
        found = win._library_record_for(
            SearchResult.from_dict(dict(RESULT_DICT)))
        self.assertIsNotNone(found)
        self.assertEqual(found.title, RESULT_DICT["title"])
        win.close()
        win.deleteLater()

    def test_library_reanalyze_uses_worker_thread(self):
        """重新AI分析必须经 SummaryWorker（不在 GUI 线程同步调用）。"""
        win = self._window()
        win._on_search_done([dict(RESULT_DICT)])
        win.table.selectRow(0)
        win._on_favorite()
        record = win._library_store.load_all()[0]

        started = []
        with mock.patch.object(win, "enhanced_requested") as sig, \
                mock.patch.object(type(win._summary_service),
                                  "enhanced_configured",
                                  new_callable=mock.PropertyMock,
                                  return_value=True):
            sig.emit.side_effect = lambda *a, **k: started.append(a)
            win._reanalyze_library_enhanced(record)
        self.assertEqual(len(started), 1)
        # 目标记录已登记，成功后回写该记录
        self.assertEqual(win._pending_library_record_id, record.id)
        win.close()
        win.deleteLater()

    def test_reanalyze_failure_message_keeps_record(self):
        """23. 失败时提示"已保留原结果"且记录内容不变。"""
        win = self._window()
        win._on_search_done([dict(RESULT_DICT)])
        win.table.selectRow(0)
        win._on_favorite()
        record = win._library_store.load_all()[0]
        summary = EnhancedSummary.from_dict({**ENHANCED_PAYLOAD,
                                            "provider": "openai-compatible",
                                            "ai_generated": True})
        win._library_store.update(record.id, enhanced_summary=summary,
                                  enhanced_provider="openai-compatible",
                                  enhanced_model="tju-llm",
                                  enhanced_prompt_version="v4",
                                  enhanced_profile_version="p1")

        win._pending_library_record_id = record.id
        win._pending_enhanced_result = SearchResult.from_dict(dict(RESULT_DICT))
        with mock.patch.object(QMessageBox, "information") as info:
            win._on_enhanced_failed("增强分析请求失败")
        self.assertIn("已保留原结果", info.call_args.args[2])
        self.assertEqual(
            win._library_store.load_all()[0].direction_match_level, "weak")
        self.assertIsNone(win._pending_library_record_id)
        win.close()
        win.deleteLater()

    def test_reanalyze_degraded_result_not_written(self):
        """23. 降级结果（ai_generated=False）不写库 → 旧分析保留。"""
        win = self._window()
        win._on_search_done([dict(RESULT_DICT)])
        win.table.selectRow(0)
        win._on_favorite()
        record = win._library_store.load_all()[0]
        good = EnhancedSummary.from_dict({**ENHANCED_PAYLOAD,
                                         "provider": "openai-compatible",
                                         "ai_generated": True})
        win._library_store.update(record.id, enhanced_summary=good,
                                  enhanced_provider="openai-compatible",
                                  enhanced_prompt_version="v4",
                                  enhanced_profile_version="p1")
        win._pending_library_record_id = record.id
        win._pending_enhanced_result = SearchResult.from_dict(dict(RESULT_DICT))
        degraded = {**ENHANCED_PAYLOAD, "direction_match_level": "unknown",
                    "provider": "", "ai_generated": False}
        with mock.patch.object(QMessageBox, "information") as info:
            win._on_enhanced_ready(degraded)
        self.assertIn("已保留原结果", info.call_args.args[2])
        self.assertEqual(
            win._library_store.load_all()[0].direction_match_level, "weak")
        win.close()
        win.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
