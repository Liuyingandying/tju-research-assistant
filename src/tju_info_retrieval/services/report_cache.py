"""报告缓存（v0.18 Phase 2.9-C-B，缓存基础）。

**独立命名空间**：`app_paths.report_cache_dir()`
（开发态 `<repo>/runtime/cache/research_reports/`），
禁止与单篇增强分析缓存 `enhanced_summary/` 混存——两者输入规模、
失效条件、版本节奏都不同，混存会互相误伤。

缓存 key 必须包含（缺一不可）：

- `paper_ids`：纳入报告的论文 id 集合；
- `paper_versions`：每篇论文**当时的分析版本**（prompt/profile 版本 + 分析时间），
  任一论文被重新分析 → key 变化；
- `profile_version`：用户科研画像版本；
- `report_prompt_version`：报告 Prompt 版本（`r1` 起）。

另纳入 `provider` / `model`：换模型后旧报告不应被当作当前模型产物复用。

本阶段只实现"目录 + key 计算 + 进程内存/磁盘读写骨架"，
不含 Provider 调用逻辑（留待 2.9-C-C ReportService）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tju_info_retrieval import app_paths
from tju_info_retrieval.models.research_report import (
    REPORT_PROMPT_VERSION,
    ResearchReport,
)

CACHE_VERSION = "1"


def cache_dir() -> Path:
    """报告缓存目录（延迟解析，随隔离环境变化）。"""
    return app_paths.report_cache_dir()


def collection_fingerprint(paper_versions: list[str]) -> str:
    """论文集合指纹：**顺序无关**（用户换选顺序不应造成重复调用）。"""
    material = "|".join(sorted(str(v) for v in (paper_versions or [])))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def paper_version_tag(record) -> str:
    """单篇论文的分析版本标签（用于 paper_versions 与失效判断）。

    组成：`library_id:enhanced_prompt_version:enhanced_profile_version:enhanced_at`
    —— 任一变化（重新分析 / 画像变更后重算）都会改变集合指纹。
    仅有基础整理、无 AI 分析的记录同样参与（标记 `basic`）。
    """
    if record is None:
        return ""
    paper_id = str(getattr(record, "id", "") or "")
    prompt_version = str(
        getattr(record, "enhanced_prompt_version", "") or "")
    profile_version = str(
        getattr(record, "enhanced_profile_version", "") or "")
    analyzed_at = str(getattr(record, "enhanced_at", "") or "")
    if not prompt_version and not profile_version:
        prompt_version = "basic"
    return f"{paper_id}:{prompt_version}:{profile_version}:{analyzed_at}"


def report_cache_key(
    paper_ids: list[str],
    paper_versions: list[str],
    profile_version,
    prompt_version: str = REPORT_PROMPT_VERSION,
    provider: str = "",
    model: str = "",
) -> str:
    """报告缓存 key（sha256）。

    `paper_ids` 与 `paper_versions` 均按排序后参与，保证"同一集合不同顺序"
    命中同一缓存。
    """
    material = "|".join([
        "report",
        CACHE_VERSION,
        ",".join(sorted(str(p) for p in (paper_ids or []))),
        collection_fingerprint(paper_versions),
        "" if profile_version in (None, "") else str(profile_version),
        str(prompt_version or ""),
        str(provider or ""),
        str(model or ""),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ReportCache:
    """报告结果缓存（进程内存 + 磁盘两级；与单篇缓存目录完全隔离）。"""

    def __init__(self, cache_path: Path | str | None = None) -> None:
        self._dir = Path(cache_path) if cache_path is not None else cache_dir()
        self._memory: dict[str, ResearchReport] = {}

    @property
    def dir(self) -> Path:
        return self._dir

    def key_for(
        self,
        paper_ids: list[str],
        paper_versions: list[str],
        profile_version,
        prompt_version: str = REPORT_PROMPT_VERSION,
        provider: str = "",
        model: str = "",
    ) -> str:
        return report_cache_key(
            paper_ids, paper_versions, profile_version, prompt_version,
            provider, model)

    def get(self, key: str) -> ResearchReport | None:
        if key in self._memory:
            return self._memory[key]
        path = self._dir / f"{key}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("cache_version") != CACHE_VERSION:
            return None
        report = ResearchReport.from_dict(data.get("report") or {})
        self._memory[key] = report
        return report

    def put(self, key: str, report: ResearchReport) -> None:
        """写缓存（memory + disk）；写盘失败不影响调用方（缓存非关键路径）。"""
        self._memory[key] = report
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            (self._dir / f"{key}.json").write_text(
                json.dumps({"cache_version": CACHE_VERSION,
                            "report": report.to_dict()},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            pass

    def clear(self) -> None:
        """清空进程内存缓存（不动磁盘文件）。"""
        self._memory.clear()
