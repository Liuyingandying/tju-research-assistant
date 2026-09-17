"""测试隔离支持（v0.18 Phase 2.9-B.1）。

提供两件事：

1. **真实数据指纹护栏**：对仓库内"真实用户数据"文件/目录取指纹，
   测试前后对比，一旦有变化即判定"测试写了真实数据"。
   开发态用户数据与仓库同目录（`data/`、`runtime/`），这是历史污染的
   结构性前提；护栏让这种写入立刻可见，而不是靠人工谨慎。

2. **隔离目录工具**：为每个测试创建独立的 TEST_USER_DATA_ROOT。
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

TEST_USER_DATA_ROOT_ENV = "TEST_USER_DATA_ROOT"

# 真实用户数据（仓库内 dev 态路径）——测试严禁写入
REAL_DATA_FILES = (
    "data/library.json",
    "data/tju_storage_state.json",
    "runtime/research_profile.json",
    "runtime/demo_results.json",
)
REAL_DATA_DIRS = (
    "runtime/cache/enhanced_summary",
    "runtime/cache",
    "runtime/profile",
)

# Edge Profile **不做整目录指纹**：真实浏览器进程会持续刷 BrowserMetrics /
# ShaderCache / Default/Cache 等易变文件，整目录指纹会把外部进程的正常写入
# 误判成"测试写了真实数据"（实测出现过一次假阳性）。
# 改为只指纹"登录态相关的敏感文件"——这才是"测试不得写入"的真实目标。
REAL_DATA_PROFILE_FILES = (
    "runtime/edge_profile/Local State",
    "runtime/edge_profile/Default/Cookies",
    "runtime/edge_profile/Default/Login Data",
    "runtime/edge_profile/Default/Preferences",
    "runtime/edge_profile/Default/Network/Cookies",
)


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _file_fingerprint(path: Path) -> dict:
    if not path.is_file():
        return {"exists": False}
    data = path.read_bytes()
    stat = path.stat()
    return {
        "exists": True,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "mtime_ns": stat.st_mtime_ns,
    }


def _dir_fingerprint(path: Path) -> dict:
    """目录指纹：顶层条目数 + 条目名集合 + 各条目 size/mtime（不递归内容）。"""
    if not path.is_dir():
        return {"exists": False}
    entries = []
    for item in sorted(path.iterdir(), key=lambda p: p.name):
        try:
            stat = item.stat()
            entries.append([item.name, stat.st_size, stat.st_mtime_ns])
        except OSError:
            entries.append([item.name, -1, -1])
    return {"exists": True, "entries": entries}


def real_data_targets(root: Path | None = None) -> dict[str, str]:
    """需要护栏保护的路径：{相对路径: "file"|"dir"}。

    以函数形式暴露，便于测试在隔离环境里替换为替身路径。
    """
    root = Path(root) if root is not None else project_root()
    targets: dict[str, str] = {}
    for rel in REAL_DATA_FILES:
        targets[rel] = "file"
    for rel in REAL_DATA_DIRS:
        targets[rel] = "dir"
    for rel in REAL_DATA_PROFILE_FILES:
        # 敏感文件可能不存在（未登录时）；父目录在即纳入监控
        if (root / rel).is_file() or (root / rel).parent.is_dir():
            targets[rel] = "file"
    return targets


def snapshot_real_data(root: Path | None = None) -> dict:
    """取全部真实数据目标当前指纹。"""
    root = Path(root) if root is not None else project_root()
    snapshot: dict[str, dict] = {}
    for rel, kind in real_data_targets(root).items():
        path = root / rel
        snapshot[rel] = (
            _file_fingerprint(path) if kind == "file"
            else _dir_fingerprint(path)
        )
    return snapshot


def diff_snapshots(before: dict, after: dict) -> list[str]:
    """返回发生变化的相对路径列表（新增/修改/删除都算）。"""
    changed = []
    for rel, old in (before or {}).items():
        new = (after or {}).get(rel)
        if old != new:
            changed.append(rel)
    for rel in (after or {}):
        if rel not in (before or {}):
            changed.append(rel)
    return sorted(set(changed))


def make_isolated_root(base: Path, name: str) -> Path:
    """在 base 下为单个测试创建独立数据根目录。"""
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
    root = Path(base) / safe[:120]
    root.mkdir(parents=True, exist_ok=True)
    return root


def current_user_data_root() -> Path | None:
    value = os.environ.get(TEST_USER_DATA_ROOT_ENV, "").strip()
    return Path(value) if value else None


def is_isolated() -> bool:
    """当前是否处于隔离态（TEST_USER_DATA_ROOT 指向仓库之外）。"""
    root = current_user_data_root()
    if root is None:
        return False
    try:
        root.resolve().relative_to(project_root().resolve())
    except ValueError:
        return True
    return False


def cleanup_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
