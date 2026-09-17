#!/usr/bin/env python3
"""正式测试：测试环境隔离（v0.18 Phase 2.9-B.1-B）。

覆盖：
1. 测试运行期间处于隔离态（TEST_USER_DATA_ROOT 指向仓库之外）；
2. 各类 store 的**默认**构造都落在隔离根目录内，而不是仓库 data/、runtime/；
3. 模块导入期常量（browser.session.STORAGE_STATE_PATH）同样被隔离；
4. 增强分析缓存默认目录为绝对路径且随隔离环境变化（修复前的相对路径缺陷）；
5. `isolated_library_store` 等 fixture 可用且写入临时目录；
6. 真实数据护栏本身有效（能发现变化）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests._isolation_support import (
    diff_snapshots,
    is_isolated,
    project_root,
    real_data_targets,
    snapshot_real_data,
)


class TestIsolationActive:
    def test_running_isolated(self):
        """1. 测试必须在隔离态运行。"""
        assert is_isolated(), (
            "测试未隔离：TEST_USER_DATA_ROOT 缺失或指向仓库内部 —— "
            "这会直接污染真实论文库/科研画像")

    def test_isolated_root_is_temp_dir(self):
        from tju_info_retrieval import app_paths

        root = app_paths.user_data_root()
        assert root.is_dir()
        assert root != project_root()
        assert str(root).lower().startswith(os.environ.get("TEMP", "x").lower()[:3]) \
            or "pytest-session" in str(root) or "tmp" in str(root).lower()

    def test_user_data_root_env_matches_app_paths(self):
        from tju_info_retrieval import app_paths

        assert app_paths.TEST_USER_DATA_ROOT_ENV in os.environ
        assert app_paths.user_data_root() == Path(
            os.environ[app_paths.TEST_USER_DATA_ROOT_ENV]).resolve()


class TestDefaultStoresAreIsolated:
    """2. 默认构造的 store 必须落在隔离目录，而不是仓库。"""

    def test_default_library_store_path(self):
        from tju_info_retrieval.services.library_store import LibraryStore

        path = LibraryStore().path.resolve()
        assert path != (project_root() / "data" / "library.json").resolve()
        assert _outside_repo(path)

    def test_default_profile_store_path(self):
        from tju_info_retrieval.services.research_profile_store import (
            profile_path,
        )

        path = profile_path().resolve()
        assert path != (project_root() / "runtime"
                        / "research_profile.json").resolve()
        assert _outside_repo(path)

    def test_default_cache_dir_absolute_and_isolated(self):  # noqa: D401
        """4. 缓存默认目录：绝对路径 + 落在隔离根目录内（修复相对路径缺陷）。"""
        from tju_info_retrieval import app_paths
        from tju_info_retrieval.services.enhanced_cache import (
            EnhancedSummaryCache,
            default_cache_dir,
        )

        cache_dir = default_cache_dir()
        assert cache_dir.is_absolute(), "缓存目录仍是相对路径（依赖 CWD）"
        assert cache_dir == app_paths.summary_cache_dir()
        assert _outside_repo(cache_dir)
        assert EnhancedSummaryCache()._dir == cache_dir

    def test_default_cache_ignores_import_time_cwd(self, tmp_path, monkeypatch):
        """4. 缓存目录不受进程 CWD 影响（打包后换工作目录不会另建）。"""
        from tju_info_retrieval.services.enhanced_cache import default_cache_dir

        first = default_cache_dir()
        monkeypatch.chdir(tmp_path)
        assert default_cache_dir() == first

    def test_storage_state_constant_isolated(self):
        """3. 导入期常量（登录态路径）也不得指向仓库。"""
        from tju_info_retrieval.browser.session import STORAGE_STATE_PATH

        path = Path(STORAGE_STATE_PATH)
        assert path.is_absolute()
        assert _outside_repo(path)
        assert path != project_root() / "data" / "tju_storage_state.json"


class TestFixtures:
    """5. 提供的隔离 fixture 可用。"""

    def test_isolated_library_store(self, isolated_library_store):
        store, path = isolated_library_store
        assert path.name == "library.json"
        assert _outside_repo(path)
        store.save(_record("夹具论文"))
        assert len(store.load_all()) == 1
        assert not (project_root() / "data" / "library.json").samefile(path)

    def test_isolated_profile_store(self, isolated_profile_store):
        store, path = isolated_profile_store
        profile = store.load()
        assert profile.research_area == ""
        profile.research_area = "太赫兹"
        store.save(profile)
        assert store.load().research_area == "太赫兹"
        assert _outside_repo(path)

    def test_isolated_cache(self, isolated_cache):
        assert _outside_repo(isolated_cache._dir)

    def test_isolated_main_window(self, isolated_main_window):
        from tju_info_retrieval import app_paths

        window = isolated_main_window
        assert window._library_store.path == app_paths.library_path()
        assert _outside_repo(window._library_store.path)


class TestGuardItself:
    """6. 护栏本身有效（不是摆设）。"""

    def test_targets_non_empty(self):
        targets = real_data_targets()
        assert "data/library.json" in targets
        assert "runtime/research_profile.json" in targets
        assert targets["data/library.json"] == "file"

    def test_snapshot_and_diff_detects_change(self, tmp_path):
        """模拟一次真实数据写入：差集必须命中。"""
        (tmp_path / "runtime" / "cache").mkdir(parents=True)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "library.json").write_text("[]", encoding="utf-8")

        before = snapshot_real_data(tmp_path)
        assert diff_snapshots(before, before) == []

        (tmp_path / "data" / "library.json").write_text(
            '[{"title": "x"}]', encoding="utf-8")
        after = snapshot_real_data(tmp_path)
        assert diff_snapshots(before, after) == ["data/library.json"]

    def test_snapshot_detects_new_cache_entry(self, tmp_path):
        (tmp_path / "runtime" / "cache").mkdir(parents=True)
        (tmp_path / "runtime" / "cache").joinpath("keep").mkdir()
        (tmp_path / "runtime" / "cache" / "enhanced_summary").mkdir()
        before = snapshot_real_data(tmp_path)
        (tmp_path / "runtime" / "cache" / "enhanced_summary"
         / "abc.json").write_text("{}", encoding="utf-8")
        after = snapshot_real_data(tmp_path)
        assert "runtime/cache/enhanced_summary" in diff_snapshots(before, after)


def _outside_repo(path) -> bool:
    """路径是否位于仓库之外（隔离的判据，不依赖目录名）。"""
    try:
        Path(path).resolve().relative_to(project_root().resolve())
    except ValueError:
        return True
    return False


def _record(title: str):
    from tju_info_retrieval.models.library import LibraryRecord

    return LibraryRecord.from_search_result({"title": title,
                                            "database": "CNKI"})
