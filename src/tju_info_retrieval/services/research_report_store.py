"""科研调研报告持久化（v0.18 Phase 2.9-C-B）。

职责：把 `ResearchReport` 存成独立 JSON 文件。

- 开发态：`<repo>/runtime/reports/<report_id>.json`
- 隔离态：`$TEST_USER_DATA_ROOT/reports/<report_id>.json`
- frozen 态：`%LOCALAPPDATA%\\TJU_Info_Retrieval\\reports\\<report_id>.json`

设计约束：

1. **与 LibraryStore 完全分离**：本模块不 import、不读写 `data/library.json`
   或任何论文库文件；报告是论文库的只读投影。
2. **原子写**：临时文件 → flush + fsync → `os.replace`，异常退出不会写坏报告。
3. **读取安全**：损坏 / 非法 / 非对象 JSON 一律安全跳过，不打断 UI。
4. **报告自包含**：文件内已内嵌论文快照，论文库变化不影响已生成报告。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from tju_info_retrieval import app_paths
from tju_info_retrieval.models.research_report import ResearchReport

REPORT_FILE_SUFFIX = ".json"


class ResearchReportStore:
    """报告目录读写封装（每个报告一个文件）。"""

    def __init__(self, path: Path | None = None) -> None:
        self._dir = Path(path) if path is not None else app_paths.reports_dir()

    @property
    def dir(self) -> Path:
        return self._dir

    def path_for(self, report_id: str) -> Path:
        safe = "".join(ch for ch in str(report_id or "")
                       if ch.isalnum() or ch in "-_")
        return self._dir / f"{safe}{REPORT_FILE_SUFFIX}"

    # ---------- 写 ----------

    def save(self, report: ResearchReport) -> Path:
        """保存报告（同 id 覆盖），返回文件路径。"""
        if report is None:
            raise ValueError("report 不能为空")
        if not report.id:
            raise ValueError("报告缺少 id")
        target = self.path_for(report.id)
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = report.to_json()
        fd, tmp_path = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, str(target))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return target

    # ---------- 读 ----------

    def load(self, report_id: str) -> ResearchReport | None:
        """按 id 读取报告；不存在或损坏 → None。"""
        path = self.path_for(report_id)
        if not path.is_file():
            return None
        return self._read_file(path)

    def load_by_path(self, path: Path) -> ResearchReport | None:
        return self._read_file(Path(path))

    @staticmethod
    def _read_file(path: Path) -> ResearchReport | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return ResearchReport.from_json(raw)
        except ValueError:
            return None

    def list_reports(self) -> list[ResearchReport]:
        """列出全部报告，按创建时间倒序（最新在前）。"""
        reports: list[ResearchReport] = []
        if not self._dir.is_dir():
            return reports
        for path in sorted(self._dir.glob(f"*{REPORT_FILE_SUFFIX}")):
            report = self._read_file(path)
            if report is not None:
                reports.append(report)
        reports.sort(key=lambda r: r.created_time or "", reverse=True)
        return reports

    def list_summaries(self) -> list[dict]:
        """报告列表视图（轻量：不返回 sections 正文）。"""
        return [
            {
                "id": r.id,
                "title": r.title,
                "created_time": r.created_time,
                "status": r.status,
                "paper_count": r.paper_count,
                "provider": r.provider,
                "model": r.model,
                "report_prompt_version": r.report_prompt_version,
                "profile_version": r.profile_version(),
            }
            for r in self.list_reports()
        ]

    def exists(self, report_id: str) -> bool:
        return self.path_for(report_id).is_file()

    # ---------- 删 ----------

    def delete(self, report_id: str) -> bool:
        """删除报告文件，返回是否删除成功（不存在 → False）。

        只删除报告文件本身：不触碰论文库、不触碰缓存与其它用户数据。
        """
        path = self.path_for(report_id)
        if not path.is_file():
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False

    # ---------- 导出 ----------

    def export_json(self, report: ResearchReport, path: Path) -> Path:
        """把报告写到一个任意路径（导出用；不进入 reports/ 管理）。"""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report.to_json(), encoding="utf-8")
        return target

    def _unused(self) -> None:  # pragma: no cover - 占位保持接口稳定
        return None
