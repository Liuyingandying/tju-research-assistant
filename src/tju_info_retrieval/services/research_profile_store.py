"""Research Profile 存储（v0.18 Phase 2.9-A）。

- 开发环境：runtime/research_profile.json；
- frozen EXE：%LOCALAPPDATA%\\TJU_Info_Retrieval\\profile\\research_profile.json；
- 首次使用自动创建默认画像（非空，可修改）；
- Profile 不属于 secret（不含 API key/认证信息）。

本机单用户；无账号系统。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from tju_info_retrieval import app_paths
from tju_info_retrieval.models.research_profile import (
    ResearchProfile,
    default_profile,
)

PROFILE_DIR_NAME = "profile"
PROFILE_FILENAME = "research_profile.json"


def profile_dir() -> Path:
    """画像目录：frozen/测试隔离 → LocalAppData/profile；开发态 → runtime/。"""
    if app_paths.is_frozen() or bool(
            os.environ.get(app_paths.TEST_USER_DATA_ROOT_ENV, "").strip()):
        return app_paths.user_data_root() / PROFILE_DIR_NAME
    return app_paths.project_root() / "runtime"


def profile_path() -> Path:
    return profile_dir() / PROFILE_FILENAME


class ResearchProfileStore:
    """科研画像读写（默认创建 + bump_version）。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or profile_path()

    def load(self) -> ResearchProfile:
        """读取画像；缺失/损坏 → 创建并保存默认（非空）。"""
        if self._path.is_file():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return ResearchProfile.from_dict(data)
            except (OSError, ValueError):
                pass
        profile = default_profile()
        self.save(profile)
        return profile

    def save(self, profile: ResearchProfile) -> Path:
        profile.bump_version()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        return self._path

    def reset_to_default(self) -> ResearchProfile:
        """恢复默认画像（版本继续递增，旧缓存失效）。"""
        profile = default_profile()
        self.save(profile)
        return profile