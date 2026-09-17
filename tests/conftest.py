"""测试隔离与真实数据护栏（v0.18 Phase 2.9-B.1）。

背景：开发态"用户数据"就落在仓库内（`data/library.json`、
`runtime/research_profile.json`、`runtime/cache/`），因此只要某个测试忘了隔离，
其写入会直接污染真实论文库（历史残留 `Test paper` ×3 / `Deep THz study` ×2
即由此产生）。

本 conftest 做三件事：

1. **导入期**即把 TEST_USER_DATA_ROOT 指向会话级临时目录——这一步必须发生在
   测试模块导入之前，否则 `browser/session.py` 里
   `STORAGE_STATE_PATH = storage_state_path()` 这类"导入期常量"
   仍会固化成仓库真实路径（曾导致导入期就锁定真实登录态文件）。
2. **每个测试**再把 TEST_USER_DATA_ROOT 收窄到该测试专属子目录，
   测试结束还原；所有 store 默认路径因此都在临时目录内。
3. **真实数据指纹护栏**：测试前后对比仓库内真实数据文件/目录指纹，
   一旦变化立即 fail 该测试（不允许任何测试写真实数据）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

for _p in (ROOT, SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---- 测试环境稳定化（Phase 2.10-A） ----
# 必须在 _isolation_support 之前导入，以便在 pytest 首次调用 tempfile.gettempdir()
# 之前覆盖缓存。否则 pytest 的 tmp_path fixture 会使用系统 TEMP 指向的
# 不可写目录（D:\DevCache\Temp），导致 97 个 PermissionError。
import tests._test_environment  # noqa: E402  # isort: skip

from tests._isolation_support import (  # noqa: E402
    TEST_USER_DATA_ROOT_ENV,
    cleanup_tree,
    diff_snapshots,
    make_isolated_root,
    snapshot_real_data,
)

# ---------- 导入期隔离（必须早于任何测试模块导入）----------

_SESSION_ROOT = Path(tempfile.mkdtemp(prefix="tju-pytest-session-"))
os.environ[TEST_USER_DATA_ROOT_ENV] = str(_SESSION_ROOT)

# 会话开始的真实数据基线（用于最终核对）
_REAL_DATA_BASELINE = snapshot_real_data(ROOT)


# 人工验收例外：显式声明需要真实授权环境（专用 Edge Profile / TJU 登录态）的
# 测试必须打此标记——它们不能隔离（隔离后没有登录态），因此跳过隔离与护栏。
# 默认套件用 `-m "not real_user_data"` 即可排除；整体放开用
# 环境变量 TJU_ALLOW_REAL_USER_DATA=1。
REAL_DATA_MARKER = "real_user_data"
ALLOW_REAL_DATA_ENV = "TJU_ALLOW_REAL_USER_DATA"


def pytest_configure(config) -> None:
    # ---- Phase 2.10-A: 覆盖 pytest basetemp ----
    # 系统 TEMP 指向 D:\\DevCache\\Temp，该目录下残留的 pytest-of-FAJ
    # 子目录存在权限拒绝（WinError 5），导致 tmp_path fixture 全部失败。
    # 将 basetemp 强制指向可写目录，绕过权限问题。
    from tests._test_environment import get_test_temp_root

    new_basetemp = get_test_temp_root() / "pytest-basetemp"
    new_basetemp.mkdir(parents=True, exist_ok=True)
    # 设置 pytest 的 --basetemp 选项（必须在 collect 之前）
    config.option.basetemp = str(new_basetemp)

    config.addinivalue_line(
        "markers",
        f"{REAL_DATA_MARKER}: 使用真实用户数据/授权环境（人工验收），"
        "跳过测试隔离与真实数据护栏",
    )


def _uses_real_user_data(request) -> bool:
    """是否走真实环境（标记或全局开关）。

    兼容 FixtureRequest 与 SubRequest（class 级 fixture 传入的是 SubRequest，
    没有 get_closest_marker）。
    """
    if os.environ.get(ALLOW_REAL_DATA_ENV, "").strip() == "1":
        return True
    node = getattr(request, "node", None)
    getter = getattr(node, "get_closest_marker", None)
    if getter is not None and getter(REAL_DATA_MARKER) is not None:
        return True
    keywords = getattr(request, "keywords", None)
    if keywords and REAL_DATA_MARKER in keywords:
        return True
    return False


def _node_id(request) -> str:
    node = getattr(request, "node", None)
    nodeid = getattr(node, "nodeid", None) or getattr(request, "nodeid", None)
    return str(nodeid or "test")


def pytest_report_header(config) -> str:
    return (f"TJU 测试隔离: TEST_USER_DATA_ROOT={_SESSION_ROOT}\n"
            f"TJU 真实数据护栏: 监控 {len(_REAL_DATA_BASELINE)} 个仓库数据目标"
            f"（人工验收标记 {REAL_DATA_MARKER} 除外）")


@pytest.fixture(autouse=True)
def isolated_user_data(request):
    """每个测试使用独立的用户数据根目录 + 真实数据写入护栏。

    打了 `real_user_data` 标记（或 TJU_ALLOW_REAL_USER_DATA=1）的测试
    走真实环境，不做隔离与护栏——仅限人工验收测试使用。
    """
    previous = os.environ.get(TEST_USER_DATA_ROOT_ENV)

    if _uses_real_user_data(request):
        if previous is not None:
            os.environ.pop(TEST_USER_DATA_ROOT_ENV, None)
        try:
            yield None
        finally:
            if previous is not None:
                os.environ[TEST_USER_DATA_ROOT_ENV] = previous
        return

    per_test_root = make_isolated_root(_SESSION_ROOT, _node_id(request))
    os.environ[TEST_USER_DATA_ROOT_ENV] = str(per_test_root)

    before = snapshot_real_data(ROOT)
    try:
        yield per_test_root
    finally:
        after = snapshot_real_data(ROOT)
        changed = diff_snapshots(before, after)
        if previous is None:
            os.environ.pop(TEST_USER_DATA_ROOT_ENV, None)
        else:
            os.environ[TEST_USER_DATA_ROOT_ENV] = previous
        cleanup_tree(per_test_root)
        if changed:
            pytest.fail(
                "测试写入了仓库真实用户数据（隔离失败）: "
                + ", ".join(changed)
                + f"\n  TEST_USER_DATA_ROOT 本应为 {per_test_root}"
                + "\n  请改用隔离目录（tests/conftest.py 的 isolated_user_data "
                  "或显式传入临时路径），不要把测试数据写入 data/ 或 runtime/。"
                  "\n  若确实是人工验收测试，请显式打 @pytest.mark."
                  f"{REAL_DATA_MARKER} 标记。")


@pytest.fixture
def isolated_library_store(tmp_path):
    """独立论文库 store（写入临时目录），供论文库相关测试使用。

    返回 (store, path)。
    """
    from tju_info_retrieval.services.library_store import LibraryStore

    path = Path(tmp_path) / "library.json"
    return LibraryStore(path), path


@pytest.fixture
def isolated_profile_store(tmp_path):
    """独立科研画像 store（写入临时目录）。返回 (store, path)。"""
    from tju_info_retrieval.services.research_profile_store import (
        ResearchProfileStore,
    )

    path = Path(tmp_path) / "research_profile.json"
    return ResearchProfileStore(path), path


@pytest.fixture
def isolated_cache(tmp_path):
    """独立增强分析缓存（写入临时目录）。"""
    from tju_info_retrieval.services.enhanced_cache import EnhancedSummaryCache

    return EnhancedSummaryCache(Path(tmp_path) / "enhanced_summary")


@pytest.fixture
def isolated_main_window(isolated_user_data):
    """隔离态 MainWindow（LibraryStore/ProfileStore/缓存均落在临时目录）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from tju_info_retrieval.ui.main_window import MainWindow

    window = MainWindow()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()
