"""测试环境稳定化（v0.18 Phase 2.10-A）。

解决 pytest tmp_path 因系统 TEMP 目录权限问题导致的 97 errors。

根因：
    Windows 系统 TEMP/TMP 环境变量指向 D:\\DevCache\\Temp，
    该目录下残留的 pytest-of-FAJ 子目录存在权限拒绝（WinError 5），
    导致 pytest 的 mktemp 在扫描已有编号目录时失败。

修复：
    1. 提供 get_test_temp_root() 返回明确可写的测试临时根目录
       （Windows: LOCALAPPDATA\\TJU_Info_Retrieval_TestTemp，
        Linux/macOS: /tmp/tju-pytest-<session>）。
    2. 在 conftest 导入期覆盖 tempfile.gettempdir 的返回值，
       使 pytest 的 tmp_path fixture 使用可写目录。
    3. 清理残留的旧 basetemp 目录（如果存在且不可写）。

约束：
    - 不写入 data/、runtime/、用户真实目录。
    - 不修改 SearchService、Adapter、BrowserSession、LLM Provider、
      ReportService、LibraryRecord 等业务代码。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 测试临时根目录
# ---------------------------------------------------------------------------

_TEST_TEMP_DIR_WINDOWS = "TJU_Info_Retrieval_TestTemp"
_TEST_TEMP_DIR_UNIX = "tju-pytest-temp"


def get_test_temp_root() -> Path:
    """返回测试临时根目录（明确可写）。

    Windows:
        %LOCALAPPDATA%\\TJU_Info_Retrieval_TestTemp\\

    Linux / macOS:
        /tmp/tju-pytest-temp/

    目录不存在时自动创建，已存在且不可写时尝试清理后重建。
    """
    platform = sys.platform
    if platform.startswith("win"):
        localapp = os.environ.get("LOCALAPPDATA", "")
        if localapp:
            root = Path(localapp) / _TEST_TEMP_DIR_WINDOWS
        else:
            root = Path(tempfile.gettempdir()) / _TEST_TEMP_DIR_WINDOWS
    else:
        root = Path("/tmp") / _TEST_TEMP_DIR_UNIX

    _ensure_writable(root)
    return root


def _ensure_writable(path: Path) -> None:
    """确保 path 目录存在且可写。

    如果目录存在但不可写（如权限拒绝），尝试删除后重建。
    """
    if path.exists():
        if _is_writable(path):
            return
        # 目录存在但不可写，尝试清理
        try:
            shutil.rmtree(path)
        except OSError:
            # 完全无法删除（如被进程锁定），记录警告但不阻塞
            import warnings
            warnings.warn(
                f"测试临时目录 {path} 无法清理（可能已被锁定），"
                "将尝试直接使用。如果后续测试出现 PermissionError，"
                "请手动删除该目录后重试。",
                stacklevel=2,
            )
            return

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"测试临时目录创建失败: {path}\n"
            f"错误: {exc}\n"
            f"请检查目录权限或设置 TMPDIR 环境变量指向可写目录。"
        ) from exc


def _is_writable(path: Path) -> bool:
    """检查目录是否可写。"""
    try:
        test_file = path / ".write_permission_check"
        test_file.write_text("ok")
        test_file.unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 覆盖 pytest 的 basetemp
# ---------------------------------------------------------------------------

def override_pytest_basetemp() -> None:
    """在 conftest 导入期调用，覆盖 pytest 的 basetemp。

    pytest 的 tmp_path fixture 依赖 tempfile.gettempdir() 确定 basetemp。
    如果系统 TEMP 指向不可写的目录（如 D:\\DevCache\\Temp），
    此函数将 tempfile.tempdir 的缓存值替换为可写目录。

    注意：tempfile.gettempdir() 的结果在首次调用后被缓存（模块级 tempdir 变量），
    因此必须在任何模块（包括 pytest）首次调用 tempfile.gettempdir() 之前
    调用此函数。conftest.py 在导入期执行，时机正确。

    Python 3.13 中缓存变量为 tempfile.tempdir（全局），
    早期版本可能使用 tempfile._tempdir。
    """
    new_root = get_test_temp_root()

    # 清除 tempfile 的缓存值
    # Python 3.13: tempfile.tempdir（全局变量）
    # 早期版本: tempfile._tempdir
    for attr in ("tempdir", "_tempdir", "_default_tempdir"):
        if hasattr(tempfile, attr):
            val = getattr(tempfile, attr)
            if val is not None:
                old_path = Path(val)
                if not _is_writable(old_path):
                    setattr(tempfile, attr, str(new_root))
            else:
                setattr(tempfile, attr, str(new_root))

    # 同时设置环境变量（pytest 和 subprocess 也读取这些）
    os.environ["TMPDIR"] = str(new_root)
    os.environ["TEMP"] = str(new_root)
    os.environ["TMP"] = str(new_root)


# ---------------------------------------------------------------------------
# 清理残留 basetemp
# ---------------------------------------------------------------------------

def cleanup_stale_basetemp() -> None:
    """清理残留的旧 pytest basetemp 目录。

    如果 D:\\DevCache\\Temp\\pytest-of-FAJ 等目录存在且不可写，
    尝试删除它们以释放权限。
    """
    # 检查系统 TEMP 指向的目录
    system_temp = Path(tempfile.gettempdir())
    if not system_temp.exists():
        return

    # 查找所有 pytest-of-* 目录
    for item in system_temp.iterdir():
        if item.name.startswith("pytest-of-"):
            if not _is_writable(item):
                try:
                    shutil.rmtree(item)
                except OSError:
                    pass  # 无法删除，跳过


# ---------------------------------------------------------------------------
# 模块级初始化（conftest 导入时自动调用）
# ---------------------------------------------------------------------------

# 立即执行：覆盖 tempfile 缓存 + 清理残留
override_pytest_basetemp()
cleanup_stale_basetemp()
