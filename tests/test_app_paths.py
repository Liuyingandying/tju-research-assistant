from __future__ import annotations

import sys
from pathlib import Path

from tju_info_retrieval import app_paths


def test_non_frozen_runtime_stays_in_project(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv(app_paths.TEST_USER_DATA_ROOT_ENV, raising=False)
    assert app_paths.runtime_dir() == app_paths.project_root() / "runtime"
    assert app_paths.library_path() == app_paths.project_root() / "data" / "library.json"


def test_frozen_resources_use_meipass(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert app_paths.resource_path("x", "y") == tmp_path / "x" / "y"


def test_frozen_user_data_uses_local_app_data(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv(app_paths.TEST_USER_DATA_ROOT_ENV, raising=False)
    root = tmp_path / app_paths.APP_DIR_NAME
    assert app_paths.user_data_root() == root
    assert app_paths.edge_profile_dir() == root / "edge_profile"
    assert app_paths.cache_dir() == root / "cache"
    assert app_paths.logs_dir() == root / "logs"


def test_test_user_root_creates_isolated_directories(monkeypatch, tmp_path):
    target = tmp_path / "fresh-user"
    monkeypatch.setenv(app_paths.TEST_USER_DATA_ROOT_ENV, str(target))
    profile, cache, logs = app_paths.ensure_user_dirs()
    assert (profile, cache, logs) == (
        target / "edge_profile", target / "cache", target / "logs")
    assert all(path.is_dir() for path in (profile, cache, logs))


def test_resource_path_non_frozen_is_project_relative(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    expected = app_paths.project_root() / "src" / "tju_info_retrieval" / "ui" / "assets"
    assert app_paths.resource_path(
        "tju_info_retrieval", "ui", "assets") == expected
