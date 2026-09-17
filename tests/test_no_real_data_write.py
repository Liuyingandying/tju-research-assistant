#!/usr/bin/env python3
"""正式测试：测试流程不得写真实用户数据（v0.18 Phase 2.9-B.1-B/E）。

思路：**主动走一遍"最容易写真实数据"的用户流程**（收藏 / 基础整理 /
科研画像保存 / 增强分析缓存落盘），然后核对仓库真实数据指纹完全未变，
并确认所有写入都落在隔离目录内。

护栏（tests/conftest.py::isolated_user_data）本身也会在 teardown 拦截；
本文件把这些保证变成显式断言，便于回归时定位。
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from tests._isolation_support import (
    diff_snapshots,
    project_root,
    snapshot_real_data,
)

RESULT = {
    "rank": 1,
    "title": "隔离测试论文",
    "authors": ["张三"],
    "source": "测试来源",
    "year": "2026",
    "database": "CNKI",
    "detail_url": "https://example.invalid/paper/1",
    "abstract": "本文研究太赫兹通信中的波形设计问题。",
    "artifact_type": "paper",
    "artifact_metadata": {},
}


@pytest.fixture
def guard_snapshot():
    """测试前后的真实数据指纹（显式断言用）。"""
    before = snapshot_real_data(project_root())
    yield before
    after = snapshot_real_data(project_root())
    changed = diff_snapshots(before, after)
    assert changed == [], f"测试写入了仓库真实数据: {changed}"


class TestNoRealDataWrite:
    def test_real_files_untouched_by_favorite_flow(
            self, guard_snapshot, isolated_user_data):
        """收藏流程（含重复收藏）只写隔离目录。"""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from tju_info_retrieval.ui.main_window import MainWindow

        window = MainWindow()
        try:
            window._on_search_done([dict(RESULT)])
            window.table.selectRow(0)
            window._on_favorite()
            window._on_favorite()          # 重复收藏 → 更新
            library_path = window._library_store.path
            assert len(window._library_store.load_all()) == 1
            assert _outside_repo(library_path)
            assert library_path.exists()
        finally:
            window.close()
            window.deleteLater()
            app.processEvents()

    def test_real_files_untouched_by_basic_summary(
            self, guard_snapshot, isolated_user_data):
        """基础整理（离线）不写任何真实数据。"""
        from tju_info_retrieval.models.result import SearchResult
        from tju_info_retrieval.services.summary_service import (
            MODE_BASIC,
            SummaryService,
        )

        service = SummaryService(config={"enabled": False})
        out = service.summarize(MODE_BASIC, SearchResult.from_dict(dict(RESULT)))
        assert out.research_content

    def test_real_files_untouched_by_profile_save(
            self, guard_snapshot, isolated_user_data):
        """科研画像保存写隔离目录，不覆盖真实 runtime/research_profile.json。"""
        from tju_info_retrieval.services.research_profile_store import (
            ResearchProfileStore,
            profile_path,
        )

        real = project_root() / "runtime" / "research_profile.json"
        real_before = real.read_bytes() if real.is_file() else None

        store = ResearchProfileStore()
        profile = store.load()
        profile.research_area = "隔离测试方向"
        store.save(profile)

        assert store.load().research_area == "隔离测试方向"
        assert _outside_repo(profile_path())
        if real_before is not None:
            assert real.read_bytes() == real_before  # 真实画像字节未变

    def test_real_files_untouched_by_cache_write(
            self, guard_snapshot, isolated_user_data):
        """增强分析缓存落盘写隔离目录，不写仓库 runtime/cache。"""
        from tju_info_retrieval.models.enhanced_summary import EnhancedSummary
        from tju_info_retrieval.services.enhanced_cache import (
            EnhancedSummaryCache,
            default_cache_dir,
        )

        cache = EnhancedSummaryCache()
        summary = EnhancedSummary(research_background="背景",
                                  provider="openai-compatible",
                                  ai_generated=True)
        key = cache.key_for("t", "a", "openai-compatible", "tju-llm")
        cache.put(key, summary)

        assert cache.get(key) is not None
        written = list(default_cache_dir().glob("*.json"))
        assert written, "缓存未落盘到默认目录"
        assert _outside_repo(default_cache_dir())
        assert not (project_root() / "runtime" / "cache"
                    / "enhanced_summary" / f"{key}.json").exists()

    def test_settings_dialog_save_writes_isolated_profile(
            self, guard_snapshot, isolated_user_data):
        """设置页保存科研画像：走 UI 也不碰真实画像文件。"""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from tju_info_retrieval.services.research_profile_store import (
            ResearchProfileStore,
        )
        from tju_info_retrieval.ui.settings_dialog import SettingsDialog

        real = project_root() / "runtime" / "research_profile.json"
        real_before = real.read_bytes() if real.is_file() else None

        dialog = SettingsDialog()
        try:
            dialog.input_research_area.setText("隔离UI方向")
            dialog._on_save_profile()
        finally:
            dialog.deleteLater()
            app.processEvents()

        assert ResearchProfileStore().load().research_area == "隔离UI方向"
        if real_before is not None:
            assert real.read_bytes() == real_before

    def test_default_store_paths_never_point_into_repo(self):
        """所有 store 默认路径都不在仓库内（回归防线）。"""
        from tju_info_retrieval import app_paths
        from tju_info_retrieval.services.enhanced_cache import (
            EnhancedSummaryCache,
        )
        from tju_info_retrieval.services.library_store import LibraryStore
        from tju_info_retrieval.services.research_profile_store import (
            profile_path,
        )

        for path in (LibraryStore().path, profile_path(),
                     EnhancedSummaryCache()._dir, app_paths.cache_dir(),
                     app_paths.runtime_dir()):
            assert _outside_repo(path), f"默认路径落在仓库内: {path}"


def _outside_repo(path) -> bool:
    try:
        Path(path).resolve().relative_to(project_root().resolve())
    except ValueError:
        return True
    return False
