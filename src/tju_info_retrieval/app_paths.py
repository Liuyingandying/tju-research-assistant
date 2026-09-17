"""开发态与 PyInstaller frozen 态的集中路径解析。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "TJU_Info_Retrieval"
PACKAGE_VERSION = "v0.17-test2"
TEST_USER_DATA_ROOT_ENV = "TEST_USER_DATA_ROOT"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def bundle_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return project_root()


def resource_path(*parts: str) -> Path:
    """返回只读静态资源路径；frozen 时基于 PyInstaller bundle。"""
    root = bundle_root()
    direct = root.joinpath(*parts)
    if not is_frozen() and not direct.exists():
        return root.joinpath("src", *parts)
    return direct


def user_data_root() -> Path:
    """返回用户级数据根目录；测试可用环境变量指向隔离临时目录。"""
    override = os.environ.get(TEST_USER_DATA_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_DIR_NAME


def _uses_user_dirs() -> bool:
    return is_frozen() or bool(os.environ.get(TEST_USER_DATA_ROOT_ENV, "").strip())


def runtime_dir() -> Path:
    """开发态保留仓库 runtime；frozen/隔离测试使用用户目录。"""
    return user_data_root() if _uses_user_dirs() else project_root() / "runtime"


def edge_profile_dir() -> Path:
    return runtime_dir() / "edge_profile"


def cache_dir() -> Path:
    return user_data_root() / "cache" if _uses_user_dirs() else project_root() / "data"


def logs_dir() -> Path:
    return user_data_root() / "logs"


def library_path() -> Path:
    return cache_dir() / "library.json"


def summary_cache_dir() -> Path:
    """增强分析缓存目录（v0.18 Phase 2.9-B.1）。

    统一由 app_paths 解析，避免相对路径导致的两种问题：
    1. 绕过 TEST_USER_DATA_ROOT → 测试写进仓库 runtime/；
    2. 依赖进程 CWD → 打包后从别的目录启动会另建 runtime/。
    开发态保持原布局（`<repo>/runtime/cache/enhanced_summary`）以复用既有缓存。
    """
    if _uses_user_dirs():
        return cache_dir() / "enhanced_summary"
    return project_root() / "runtime" / "cache" / "enhanced_summary"


def reports_dir() -> Path:
    """科研调研报告目录（v0.18 Phase 2.9-C-B）。

    - 开发态：`<repo>/runtime/reports/`；
    - 隔离态：`$TEST_USER_DATA_ROOT/reports/`；
    - frozen 态：`%LOCALAPPDATA%\\TJU_Info_Retrieval\\reports\\`。

    与 LibraryStore 完全分离：报告是论文库的只读投影，落独立目录，
    绝不写入 `data/library.json`。
    """
    return runtime_dir() / "reports"


def report_cache_dir() -> Path:
    """报告缓存目录（独立命名空间，禁止与 enhanced_summary 混存）。

    开发态：`<repo>/runtime/cache/research_reports/`；
    隔离/frozen 态：`<用户数据根>/cache/research_reports/`。
    """
    return summary_cache_dir().parent / "research_reports"


def demo_results_path() -> Path:
    return cache_dir() / "demo_results.json" if _uses_user_dirs() \
        else runtime_dir() / "demo_results.json"


def storage_state_path() -> Path:
    return cache_dir() / "tju_storage_state.json"


def ensure_user_dirs() -> tuple[Path, Path, Path]:
    """创建 profile/cache/logs，并返回三者；不接触任何已有开发机 Profile。"""
    paths = (edge_profile_dir(), cache_dir(), logs_dir())
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
    return paths
