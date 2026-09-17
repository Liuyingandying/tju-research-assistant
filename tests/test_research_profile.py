#!/usr/bin/env python3
"""正式测试：v0.18 Phase 2.9-A Research Profile 科研画像。

覆盖：
1. 默认生成 / 2. 保存读取 / 3. 修改 profile_version / 4. 无 profile fallback /
5. Profile 不污染 Evidence / 6-8. 方向匹配（THz-ISAC×medical≠strong 等，
mock LLM 输出验证链路透传 + Prompt 约束断言）/ 9. 阅读建议含用户方向因素 /
10. Prompt v3 / 11. Cache profile invalidation / 12. 基础模式零变化 /
13. 搜索 API=0（AST）。
"""
import json
import os
import subprocess
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

from tju_info_retrieval.models.enhanced_summary import EnhancedSummary
from tju_info_retrieval.models.research_profile import (
    DIRECTION_MATCH_LEVELS,
    RESEARCH_STAGES,
    ResearchProfile,
    default_profile,
)
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.models.summary import StructuredSummary
from tju_info_retrieval.services import research_profile_store as rps
from tju_info_retrieval.services.enhanced_cache import EnhancedSummaryCache
from tju_info_retrieval.services.enhanced_provider import LLMEnhancedProvider
from tju_info_retrieval.services.llm_provider import OpenAICompatibleProvider
from tju_info_retrieval.services.prompts.enhanced_summary_prompt import (
    ENHANCED_PROMPT_VERSION,
    ENHANCED_SYSTEM_PROMPT,
    build_enhanced_summary_prompt,
    profile_version_of,
)
from tju_info_retrieval.services.research_profile_store import (
    ResearchProfileStore,
    profile_path,
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
    "model": "test-model",
    "api_key": "test-key-not-real",
}


class _FakeResponse:
    def __init__(self, text):
        self.ok = True
        self.status_code = 200
        self._text = text

    @property
    def text(self):
        return self._text

    def json(self):
        return json.loads(self._text)


def _llm_payload(**overrides) -> str:
    fields = {
        "document_type": "review",
        "document_type_reason": "综述表述",
        "direction_match_level": "medium",
        "research_background": "背景",
        "technical_route": "A → B → C",
        "innovation_points": "梳理与分类。",
        "relation_to_user_direction": "中等相关：同属太赫兹。",
        "reading_recommendation": "阅读优先级：泛读\n理由：建立视野。",
        "limitations": "从当前摘要推测：覆盖有限。",
    }
    fields.update(overrides)
    return json.dumps(fields, ensure_ascii=False)


def _transport_for(content):
    def transport(*args, **kwargs):
        body = {"choices": [{"message": {"role": "assistant",
                                         "content": content}}]}
        return _FakeResponse(json.dumps(body))
    return transport


def _paper(abstract, title="测试论文") -> SearchResult:
    return SearchResult(
        rank=1, title=title, authors=["张三"], source="CNKI", year=2024,
        detail_url="https://x", database="CNKI", artifact_type="paper",
        artifact_metadata={}, abstract=abstract)


def _thz_profile() -> ResearchProfile:
    """已配置画像（等价 2.9-A 开发用户：太赫兹 / THz-ISAC）。

    v0.18 Phase 2.9-B 起默认画像为空，需要具体方向的老用例显式构造。
    """
    profile = default_profile()
    profile.research_area = "太赫兹"
    profile.sub_direction = ["THz-ISAC"]
    profile.keywords = ["terahertz", "THz", "ISAC"]
    return profile


MEDICAL_ABSTRACT = (
    "本文综述太赫兹技术在癌症诊断医学中的应用，包括太赫兹成像在皮肤癌、"
    "乳腺癌等浅表肿瘤的临床研究案例，并讨论未来发展趋势。"
)
COMMS_ABSTRACT = (
    "本文面向太赫兹通信系统，研究 ISAC 波形设计与信道建模，"
    "提出一种融合感知与通信的太赫兹波形方案。"
)


class TestResearchProfileModel(unittest.TestCase):
    """1. 默认生成 / 3. 版本递增。"""

    def test_default_profile_empty(self):
        """2.9-B P0：发布版默认画像不含任何具体研究方向。"""
        p = default_profile()
        self.assertEqual(p.research_area, "")
        self.assertEqual(p.sub_direction, [])
        self.assertEqual(p.keywords, [])
        self.assertEqual(p.excluded_topics, [])
        self.assertEqual(p.research_stage, "undergraduate")
        self.assertEqual(p.preferred_output_style, "normal")
        self.assertFalse(p.is_configured())
        # 禁止预置开发用户专属方向
        raw = json.dumps(p.to_dict(), ensure_ascii=False)
        for banned in ("太赫兹", "THz", "ISAC", "terahertz"):
            self.assertNotIn(banned, raw)

    def test_is_configured_detects_any_field(self):
        p = default_profile()
        self.assertFalse(p.is_configured())
        p.keywords = ["THz"]
        self.assertTrue(p.is_configured())
        p.keywords = []
        p.sub_direction = ["THz-ISAC"]
        self.assertTrue(p.is_configured())
        p.sub_direction = []
        p.research_area = "太赫兹"
        self.assertTrue(p.is_configured())

    def test_stage_enum_valid(self):
        self.assertEqual(
            set(RESEARCH_STAGES),
            {"undergraduate", "master", "phd", "researcher"})

    def test_roundtrip(self):
        p = default_profile()
        p.research_area = "毫米波"
        p.sub_direction = ["beamforming"]
        restored = ResearchProfile.from_dict(p.to_dict())
        self.assertEqual(restored, p)

    def test_bump_version_increments(self):
        p = default_profile()
        v1 = p.profile_version
        v2 = p.bump_version()
        self.assertEqual(v2, v1 + 1)
        self.assertTrue(p.updated_time)


class TestProfileStorage(unittest.TestCase):
    """2. 保存读取 / 16. 旧用户无文件自动创建默认。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["TEST_USER_DATA_ROOT"] = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        os.environ.pop("TEST_USER_DATA_ROOT", None)

    def test_first_load_creates_empty_default(self):
        store = ResearchProfileStore()
        self.assertFalse(profile_path().exists())
        profile = store.load()
        self.assertEqual(profile.research_area, "")
        self.assertEqual(list(profile.sub_direction), [])
        self.assertFalse(profile.is_configured())
        self.assertTrue(profile_path().exists())  # 自动创建

    def test_save_and_reload(self):
        store = ResearchProfileStore()
        profile = store.load()
        profile.research_area = "太赫兹"
        profile.sub_direction = ["THz-ISAC", "THz imaging"]
        store.save(profile)
        store2 = ResearchProfileStore()
        reloaded = store2.load()
        self.assertEqual(reloaded.sub_direction, ["THz-ISAC", "THz imaging"])
        self.assertEqual(reloaded.profile_version, profile.profile_version)

    def test_reset_to_default(self):
        store = ResearchProfileStore()
        profile = store.load()
        profile.research_area = "自定义"
        store.save(profile)
        reset = store.reset_to_default()
        self.assertEqual(reset.research_area, "")
        self.assertFalse(reset.is_configured())

    def test_existing_profile_preserved_after_default_changes(self):
        """P0：已有用户画像（太赫兹/THz-ISAC）不得被新空默认覆盖。"""
        store = ResearchProfileStore()
        legacy = _thz_profile()
        store.save(legacy)
        raw_before = profile_path().read_text(encoding="utf-8")
        # 模拟升级后重新加载（default_profile 已改为空）
        reloaded = ResearchProfileStore().load()
        self.assertEqual(reloaded.research_area, "太赫兹")
        self.assertEqual(list(reloaded.sub_direction), ["THz-ISAC"])
        self.assertTrue(reloaded.is_configured())
        self.assertEqual(reloaded.profile_version, legacy.profile_version)
        # 文件内容未被改写
        self.assertEqual(
            profile_path().read_text(encoding="utf-8"), raw_before)

    def test_missing_file_uses_empty_default_not_legacy(self):
        """P0：没有画像文件的新用户 → 空默认（不写死任何方向）。"""
        store = ResearchProfileStore()
        self.assertFalse(profile_path().exists())
        profile = store.load()
        self.assertFalse(profile.is_configured())

    def test_profile_not_secret(self):
        store = ResearchProfileStore()
        store.save(default_profile())
        raw = profile_path().read_text(encoding="utf-8")
        for banned in ("api_key", "token", "password", "authorization"):
            self.assertNotIn(banned, raw.lower())


class TestContextSeparation(unittest.TestCase):
    """4-5：无 profile fallback / Profile 不污染 Evidence。"""

    def test_context_without_profile(self):
        result = _paper(MEDICAL_ABSTRACT)
        ctx = SummaryContext.from_result(result, "太赫兹")
        self.assertIsNone(ctx.research_profile)
        # Evidence 字段不受影响
        self.assertEqual(ctx.abstract, MEDICAL_ABSTRACT)
        self.assertIn("太赫兹", ctx.abstract)

    def test_profile_does_not_pollute_evidence(self):
        profile = _thz_profile()
        result = _paper(MEDICAL_ABSTRACT)
        ctx = SummaryContext.from_result(result, "太赫兹",
                                         research_profile=profile)
        # Evidence 保持论文事实
        self.assertEqual(ctx.abstract, MEDICAL_ABSTRACT)
        self.assertEqual(ctx.title, "测试论文")
        # Profile 单独存放于用户上下文
        self.assertIsNotNone(ctx.research_profile)
        self.assertIn("ISAC", ctx.research_profile.keywords)

    def test_prompt_injects_user_context_block(self):
        profile = _thz_profile()
        result = _paper(MEDICAL_ABSTRACT)
        ctx = SummaryContext.from_result(result, "太赫兹",
                                         research_profile=profile)
        prompt = build_enhanced_summary_prompt(ctx)
        self.assertIn("[USER CONTEXT]", prompt)
        self.assertIn("研究领域：太赫兹", prompt)
        self.assertIn("THz-ISAC", prompt)
        self.assertEqual(profile_version_of(ctx), "p1")  # p{N}
        # 无 profile → 降级指引
        ctx2 = SummaryContext.from_result(result, "太赫兹")
        prompt2 = build_enhanced_summary_prompt(ctx2)
        self.assertIn("未设置科研画像", prompt2)
        self.assertEqual(profile_version_of(ctx2), "")

    def test_empty_profile_behaves_as_no_profile(self):
        """2.9-B：新用户空画像 = 无画像（unknown + 固定措辞 + 同一缓存 key）。"""
        empty = default_profile()
        self.assertFalse(empty.is_configured())
        result = _paper(MEDICAL_ABSTRACT)
        ctx = SummaryContext.from_result(result, "太赫兹",
                                        research_profile=empty)
        prompt = build_enhanced_summary_prompt(ctx)
        self.assertIn("尚未设置科研画像，无法进行个性化方向匹配。", prompt)
        self.assertIn("unknown", prompt)
        # 空画像不得产生画像版本（避免缓存 key 分叉）
        self.assertEqual(profile_version_of(ctx), "")
        ctx_none = SummaryContext.from_result(result, "太赫兹")
        self.assertEqual(profile_version_of(ctx_none), "")

    def test_system_prompt_forbids_personalized_claims_without_profile(self):
        self.assertIn("尚未设置科研画像，无法进行个性化方向匹配。",
                      ENHANCED_SYSTEM_PROMPT)
        self.assertIn("与你的研究高度相关", ENHANCED_SYSTEM_PROMPT)
        self.assertIn("与你当前课题一致", ENHANCED_SYSTEM_PROMPT)

    def test_prompt_forbids_guessing_user_direction(self):
        self.assertIn("禁止根据模型记忆猜测用户研究方向",
                      ENHANCED_SYSTEM_PROMPT)
        self.assertIn("THz-ISAC", ENHANCED_SYSTEM_PROMPT)  # 不得自动扩展示例


class TestDirectionMatch(unittest.TestCase):
    """6-8：方向匹配等级经真实链路透传（mock LLM）。"""

    def _run(self, content, profile, abstract):
        with tempfile.TemporaryDirectory() as tmp:
            provider = LLMEnhancedProvider(
                llm_client=OpenAICompatibleProvider(
                    VALID_CONFIG, transport=_transport_for(content)),
                cache=EnhancedSummaryCache(Path(tmp)),
            )
            result = _paper(abstract)
            return provider.summarize(
                EvidenceBundle.from_result(result),
                SummaryContext.from_result(
                    result, profile.research_area,
                    research_profile=profile))

    def test_isac_profile_medical_not_strong(self):
        """Profile=THz-ISAC × THz medical → medium/weak（禁止 strong）。"""
        profile = _thz_profile()
        content = _llm_payload(direction_match_level="medium")
        out = self._run(content, profile, MEDICAL_ABSTRACT)
        self.assertEqual(out.direction_match_level, "medium")
        self.assertIn(out.direction_match_level,
                      ("medium", "weak"))  # 链路允许集合

    def test_thz_profile_medical_medium(self):
        """Profile=太赫兹 × THz medical → medium。"""
        profile = _thz_profile()
        profile.sub_direction = []  # 仅研究领域
        content = _llm_payload(direction_match_level="medium")
        out = self._run(content, profile, MEDICAL_ABSTRACT)
        self.assertEqual(out.direction_match_level, "medium")

    def test_isac_profile_communication_strong(self):
        """Profile=THz-ISAC × THz communication/ISAC → strong。"""
        profile = _thz_profile()
        content = _llm_payload(
            document_type="experimental_method",
            direction_match_level="strong")
        out = self._run(content, profile, COMMS_ABSTRACT)
        self.assertEqual(out.direction_match_level, "strong")

    def test_match_level_parser_fallback(self):
        fields = parse_helper(_llm_payload(direction_match_level="bogus"))
        self.assertEqual(fields["direction_match_level"], "unknown")
        self.assertIn(fields["direction_match_level"], DIRECTION_MATCH_LEVELS)


def parse_helper(text: str) -> dict:
    from tju_info_retrieval.services.prompts.enhanced_summary_prompt import (
        parse_enhanced_json,
    )
    return parse_enhanced_json(text)


class TestReadingDecisionPersonalization(unittest.TestCase):
    """9. 阅读建议包含用户方向因素。"""

    def test_reading_decision_references_profile(self):
        prompt_profile = _thz_profile()
        result = _paper(MEDICAL_ABSTRACT)
        ctx = SummaryContext.from_result(result, "太赫兹",
                                         research_profile=prompt_profile)
        prompt = build_enhanced_summary_prompt(ctx)
        # System prompt 要求综合"论文自身价值 + 用户画像 + 关注章节"
        self.assertIn("与用户画像的关系", ENHANCED_SYSTEM_PROMPT)
        self.assertIn("THz-ISAC", prompt)  # 画像已注入 user prompt
        self.assertIn("[USER CONTEXT]", prompt)

    def test_reading_decision_output_format(self):
        content = _llm_payload(
            reading_recommendation=(
                "阅读优先级：泛读\n理由：该综述系统总结太赫兹在医学检测中的"
                "应用，可帮助建立太赫兹 sensing 应用背景，但与当前 THz-ISAC "
                "通信方向距离较远。\n如阅读，优先关注：成像与检测案例。"))
        with tempfile.TemporaryDirectory() as tmp:
            profile = _thz_profile()
            provider = LLMEnhancedProvider(
                llm_client=OpenAICompatibleProvider(
                    VALID_CONFIG, transport=_transport_for(content)),
                cache=EnhancedSummaryCache(Path(tmp)),
            )
            result = _paper(MEDICAL_ABSTRACT)
            out = provider.summarize(
                EvidenceBundle.from_result(result),
                SummaryContext.from_result(result, "太赫兹",
                                           research_profile=profile))
            self.assertIn("阅读优先级：泛读", out.reading_recommendation)
            self.assertIn("THz-ISAC", out.reading_recommendation)


class TestCacheProfileVersion(unittest.TestCase):
    """11. Cache profile invalidation。"""

    def test_profile_version_in_key(self):
        cache = EnhancedSummaryCache()
        k1 = cache.key_for("t", "a", "p", "m", "v3", "p1")
        k2 = cache.key_for("t", "a", "p", "m", "v3", "p2")
        self.assertNotEqual(k1, k2)

    def test_profile_change_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = {"n": 0}

            def counting_transport(*a, **k):
                calls["n"] += 1
                return _FakeResponse(json.dumps(
                    {"choices": [{"message": {"role": "assistant",
                                              "content": _llm_payload()}}]}))

            provider = LLMEnhancedProvider(
                llm_client=OpenAICompatibleProvider(
                    VALID_CONFIG, transport=counting_transport),
                cache=EnhancedSummaryCache(Path(tmp)),
            )
            result = _paper(MEDICAL_ABSTRACT)
            profile = _thz_profile()  # p1
            ev = EvidenceBundle.from_result(result)
            ctx1 = SummaryContext.from_result(
                result, "太赫兹", research_profile=profile)
            provider.summarize(ev, ctx1)
            self.assertEqual(calls["n"], 1)
            # 用户修改方向 → profile_version 递增 → 旧缓存失效
            profile.sub_direction = ["THz imaging"]
            profile.bump_version()  # p2
            ctx2 = SummaryContext.from_result(
                result, "太赫兹", research_profile=profile)
            provider.summarize(ev, ctx2)
            self.assertEqual(calls["n"], 2)  # MISS → 重新请求

    def test_same_profile_second_call_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = {"n": 0}

            def counting_transport(*a, **k):
                calls["n"] += 1
                return _FakeResponse(json.dumps(
                    {"choices": [{"message": {"role": "assistant",
                                              "content": _llm_payload()}}]}))

            provider = LLMEnhancedProvider(
                llm_client=OpenAICompatibleProvider(
                    VALID_CONFIG, transport=counting_transport),
                cache=EnhancedSummaryCache(Path(tmp)),
            )
            result = _paper(MEDICAL_ABSTRACT)
            profile = _thz_profile()
            ev = EvidenceBundle.from_result(result)
            ctx = SummaryContext.from_result(
                result, "太赫兹", research_profile=profile)
            provider.summarize(ev, ctx)
            provider.summarize(ev, ctx)
            self.assertEqual(calls["n"], 1)  # 同 profile HIT


class TestCompatibility(unittest.TestCase):
    """12. 基础模式零变化 / 13. 搜索 API=0 / 无 profile fallback。"""

    def test_basic_mode_unchanged(self):
        from tju_info_retrieval.services.offline_summary import (
            OfflineSummaryEngine,
        )
        result = _paper(MEDICAL_ABSTRACT)
        profile = default_profile()
        service = SummaryService(config={"enabled": False})
        # basic 不接受也不受 profile 影响（签名兼容：传 profile 不报错）
        out = service.summarize(MODE_BASIC, result,
                                research_profile=profile)
        self.assertIsInstance(out, StructuredSummary)
        engine_out = OfflineSummaryEngine().summarize(result)
        self.assertEqual(out.items(), engine_out.items())

    def test_enhanced_without_profile_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = LLMEnhancedProvider(
                llm_client=OpenAICompatibleProvider(
                    VALID_CONFIG, transport=_transport_for(_llm_payload())),
                cache=EnhancedSummaryCache(Path(tmp)),
            )
            result = _paper(MEDICAL_ABSTRACT)
            out = provider.summarize(
                EvidenceBundle.from_result(result),
                SummaryContext.from_result(result))  # 无 profile
            self.assertTrue(out.ai_generated)  # fallback 正常

    def test_search_chain_zero_api(self):
        code = (
            "import ast\n"
            "for f in ('src/tju_info_retrieval/services/search_service.py',\n"
            "          'src/tju_info_retrieval/sources/cnki.py'):\n"
            "    tree = ast.parse(open(f, encoding='utf-8').read())\n"
            "    for node in ast.walk(tree):\n"
            "        if isinstance(node, ast.Import):\n"
            "            for a in node.names:\n"
            "                assert 'llm' not in a.name, f\n"
            "        elif isinstance(node, ast.ImportFrom):\n"
            "            assert 'llm' not in (node.module or ''), f\n"
            "print('CLEAN')\n")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, encoding="utf-8")
        self.assertIn("CLEAN", r.stdout)

    def test_prompt_version_v4(self):
        """2.9-B：新增 reading_priority 字段 → Prompt 升级 v4。"""
        self.assertEqual(ENHANCED_PROMPT_VERSION, "v4")


class TestProfileUi(unittest.TestCase):
    """UI 层：设置页「科研画像」Tab 与结果区「方向匹配」显示。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["TEST_USER_DATA_ROOT"] = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        os.environ.pop("TEST_USER_DATA_ROOT", None)

    def test_settings_dialog_has_profile_tab_and_fields(self):
        from PySide6.QtWidgets import QLabel  # noqa: F401 - 供其它用例

        from tju_info_retrieval.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog()
        tabs = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
        self.assertIn("科研画像", tabs)
        for attr in ("input_research_area", "combo_stage",
                     "input_sub_direction", "input_keywords",
                     "input_excluded", "btn_profile_save",
                     "btn_profile_reset"):
            self.assertTrue(hasattr(dlg, attr), attr)
        # 2.9-B：新用户画像为空，且显示完善提示（不预置任何方向）
        self.assertEqual(dlg.input_research_area.text(), "")
        self.assertEqual(dlg.input_sub_direction.text(), "")
        self.assertIn("完善科研画像", dlg.label_profile_hint.text())
        dlg.deleteLater()

    def test_profile_save_and_reset_roundtrip(self):
        from tju_info_retrieval.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog()
        dlg.input_research_area.setText("太赫兹通信")
        dlg.input_sub_direction.setText("THz-ISAC, THz sensing")
        dlg.input_keywords.setText("terahertz, ISAC")
        dlg.input_excluded.setText("医学诊断")
        dlg._on_save_profile()
        stored = ResearchProfileStore().load()
        self.assertEqual(stored.research_area, "太赫兹通信")
        self.assertEqual(list(stored.sub_direction),
                         ["THz-ISAC", "THz sensing"])
        self.assertEqual(list(stored.excluded_topics), ["医学诊断"])
        dlg._on_reset_profile()
        reset = ResearchProfileStore().load()
        self.assertEqual(reset.research_area, "")  # 2.9-B：默认画像为空
        self.assertFalse(reset.is_configured())
        dlg.deleteLater()

    def test_profile_save_bumps_version(self):
        """连续两次修改：版本必须单调递增（否则缓存 key 不变 → 旧结果复算）。"""
        from tju_info_retrieval.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog()
        v0 = ResearchProfileStore().load().profile_version
        dlg.input_research_area.setText("太赫兹雷达")
        dlg._on_save_profile()
        v1 = ResearchProfileStore().load().profile_version
        self.assertGreater(v1, v0)
        dlg.input_sub_direction.setText("THz-ISAC, THz radar")
        dlg._on_save_profile()
        v2 = ResearchProfileStore().load().profile_version
        self.assertGreater(v2, v1)
        dlg.deleteLater()

    def test_summary_dialog_shows_direction_match(self):
        from PySide6.QtWidgets import QLabel

        from tju_info_retrieval.ui.summary_dialog import SummaryDialog

        result = _paper(MEDICAL_ABSTRACT)
        summary = EnhancedSummary(
            research_background="背景", technical_route="路线",
            innovation_points="创新", relation_to_user_direction="弱相关",
            reading_recommendation="阅读优先级：可暂缓", limitations="局限",
            document_type="review", document_type_reason="综述",
            direction_match_level="weak", provider="openai-compatible",
            ai_generated=True,
        )
        dlg = SummaryDialog(result, summary, mode=MODE_ENHANCED)
        metas = [w.text() for w in dlg.findChildren(QLabel)
                 if w.objectName() == "summaryMeta"]
        self.assertTrue(metas)
        self.assertIn("方向匹配", metas[0])
        self.assertIn("文献类型", metas[0])
        # 只显示匹配等级，不展示完整画像内容
        self.assertNotIn("关注关键词", metas[0])
        dlg.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)