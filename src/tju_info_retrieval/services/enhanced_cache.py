"""增强分析结果缓存（v0.18 Phase 2.7-B）。

- 存储：app_paths.summary_cache_dir()/<sha256>.json
  （开发态 runtime/cache/enhanced_summary；隔离态 $TEST_USER_DATA_ROOT/cache/
  enhanced_summary；frozen 态 %LOCALAPPDATA%/TJU_Info_Retrieval/cache/...）；
- key = sha256(title + abstract + provider + model + prompt_version +
  profile_version)；
- 相同输入第二次调用不请求 API（命中直接返回 EnhancedSummary）；
- 缓存文件仅含增强字段与 provider 标识，不含任何凭据。

v0.18 Phase 2.9-B.1：默认目录改由 app_paths 解析。此前是相对路径
`runtime/cache/enhanced_summary`，既绕过 TEST_USER_DATA_ROOT（测试会写进
仓库 runtime/），又依赖进程 CWD（打包后换工作目录会另建一份）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tju_info_retrieval import app_paths
from tju_info_retrieval.models.enhanced_summary import EnhancedSummary

CACHE_VERSION = "1"


def default_cache_dir() -> Path:
    """默认缓存目录（延迟解析，随 app_paths 与隔离环境变化）。"""
    return app_paths.summary_cache_dir()


class EnhancedSummaryCache:
    """进程内 + 磁盘两级增强摘要缓存。"""

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        self._dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self._memory: dict[str, EnhancedSummary] = {}

    def key_for(
        self,
        title: str,
        abstract: str,
        provider: str,
        model: str,
        prompt_version: str = "",
        profile_version: str = "",
    ) -> str:
        """缓存 key = sha256(title|abstract|provider|model|prompt_version|
        profile_version)。

        - prompt_version：Prompt 实质变化（v2→v3）自动使旧缓存失效；
        - profile_version（2.9-A）：用户修改科研画像后旧 AI 分析失效。
        """
        material = "|".join([
            title or "", abstract or "", provider or "", model or "",
            prompt_version or "", profile_version or "",
        ])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get(self, key: str) -> EnhancedSummary | None:
        """先查内存，再查磁盘；磁盘命中回填内存。"""
        if key in self._memory:
            return self._memory[key]
        path = self._dir / f"{key}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("cache_version") != CACHE_VERSION:
                return None
            summary = EnhancedSummary.from_dict(data.get("summary") or {})
            self._memory[key] = summary
            return summary
        except (OSError, ValueError):
            return None

    def put(self, key: str, summary: EnhancedSummary) -> None:
        """写入内存与磁盘（磁盘写入失败不抛错，仅内存可用）。"""
        self._memory[key] = summary
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "cache_version": CACHE_VERSION,
                "provider": summary.provider,
                "ai_generated": summary.ai_generated,
                "summary": summary.to_dict(),
            }
            path = self._dir / f"{key}.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass  # 缓存不可写时静默降级为仅内存

    def clear(self) -> None:
        self._memory.clear()