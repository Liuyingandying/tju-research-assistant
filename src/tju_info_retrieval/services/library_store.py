"""论文收藏本地存储层（JSON 文件，schema v2）。

路径：app_paths.library_path()（开发态 data/library.json；
frozen 态 %LOCALAPPDATA%\\TJU_Info_Retrieval\\cache\\library.json）。

文件格式：
- v2：``{"schema_version": 2, "records": [...]}``
- v1（旧库）：顶层裸数组 ``[...]`` —— 读取时在内存迁移为 v2，保存时写 v2。

写盘安全：临时文件 → flush + fsync → os.replace（原子替换），
异常退出不会写坏整个收藏文件。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from tju_info_retrieval.app_paths import library_path
from tju_info_retrieval.models.library import (
    LIBRARY_SCHEMA_VERSION,
    STATUS_UNREAD,
    LibraryRecord,
)


class LibraryStore:
    """JSON 文件读写封装。

    公共读取方法捕获异常并返回安全默认值（旧库/坏库不阻塞启动）；
    写入方法在失败时抛出，交由调用方决定如何提示用户。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or library_path()

    @property
    def path(self) -> Path:
        return self._path

    # ---------- 读取 ----------

    def load_all(self) -> list[LibraryRecord]:
        """读取全部收藏记录（v1 裸数组 / v2 对象 / 损坏文件均可安全处理）。"""
        try:
            if not self._path.exists():
                return []
            raw = self._path.read_text(encoding="utf-8")
            if not raw.strip():
                return []
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError, ValueError):
            return []
        return self._migrate(data)

    @staticmethod
    def _migrate(data) -> list[LibraryRecord]:
        """内存迁移：接受 v1 裸数组、v2 对象、或单条记录对象。

        单条记录对象（旧手工编辑/极小库）也能加载，不抛异常。
        """
        items: list = []
        if isinstance(data, list):
            items = data                      # v1 旧库
        elif isinstance(data, dict):
            records = data.get("records")
            if isinstance(records, list):
                items = records               # v2
            elif any(k in data for k in
                     ("title", "id", "doi", "detail_url", "authors")):
                items = [data]                # 单条记录对象
        return [
            LibraryRecord.from_dict(item)
            for item in items
            if isinstance(item, dict)
        ]

    def get(self, record_id: str) -> LibraryRecord | None:
        """按 id 取单条记录。"""
        return next((r for r in self.load_all() if r.id == record_id), None)

    def exists(self, record_id: str) -> bool:
        return self.get(record_id) is not None

    def find_by_identity(self, record: LibraryRecord) -> LibraryRecord | None:
        """按业务身份（DOI → URL/公开号 → 标题+作者+年份 → 标题）查找同名论文。"""
        wanted = set(record.identity_keys())
        if not wanted:
            return None
        for existing in self.load_all():
            if existing.id == record.id:
                continue
            if wanted & set(existing.identity_keys()):
                return existing
        return None

    # ---------- 写入 ----------

    def save(self, record: LibraryRecord) -> None:
        """按 id 写入（同 id 覆盖，新 id 追加）。"""
        records = self.load_all()
        for i, existing in enumerate(records):
            if existing.id == record.id:
                records[i] = record
                break
        else:
            records.append(record)
        self._write(records)

    def upsert_favorite(self, record: LibraryRecord) -> tuple[LibraryRecord, bool]:
        """收藏语义写入：身份命中已有记录 → 更新（保留用户数据），否则新增。

        返回 (最终记录, 是否为更新既有记录)。
        更新时保留用户字段（tags/note/reading_status）与原 id/saved_at，
        并用新元数据补全空字段（不覆盖已有非空值）。
        """
        existing = self.find_by_identity(record)
        if existing is None:
            self.save(record)
            return record, False

        # 元数据补全（新数据非空且旧值为空时才写入）
        for field_name in ("title", "year", "source", "database", "venue",
                           "abstract", "detail_url", "doi",
                           "publication_number"):
            old = getattr(existing, field_name, None)
            new = getattr(record, field_name, None)
            if (not old) and new:
                setattr(existing, field_name, new)
        if not existing.authors and record.authors:
            existing.authors = list(record.authors)
        if not existing.metadata and record.metadata:
            existing.metadata = dict(record.metadata)
        # 分析结果：已有则保留；缺失则用新记录带来的补齐
        if not existing.basic_summary and record.basic_summary:
            existing.basic_summary = record.basic_summary
        if not existing.enhanced_summary and record.enhanced_summary:
            existing.enhanced_summary = record.enhanced_summary
            existing.enhanced_provider = record.enhanced_provider
            existing.enhanced_model = record.enhanced_model
            existing.enhanced_prompt_version = record.enhanced_prompt_version
            existing.enhanced_profile_version = record.enhanced_profile_version
            existing.enhanced_at = record.enhanced_at
        existing.touch()
        self.save(existing)
        return existing, True

    def update(self, record_id: str, **changes) -> LibraryRecord | None:
        """按 id 局部更新字段（tags / note / reading_status / 分析结果等）。

        返回更新后的记录；id 不存在返回 None。
        """
        record = self.get(record_id)
        if record is None:
            return None
        for key, value in changes.items():
            if key == "tags":
                record.set_tags(value)
            elif key == "reading_status":
                record.set_reading_status(value)
            elif key == "note":
                record.set_note(value)
            elif key == "basic_summary":
                record.set_basic_summary(value)
            elif key == "enhanced_summary":
                record.set_enhanced_summary(
                    value,
                    provider=str(changes.get("enhanced_provider") or ""),
                    model=str(changes.get("enhanced_model") or ""),
                    prompt_version=str(
                        changes.get("enhanced_prompt_version") or ""),
                    profile_version=str(
                        changes.get("enhanced_profile_version") or ""),
                )
            elif hasattr(record, key):
                setattr(record, key, value)
        record.touch()
        self.save(record)
        return record

    def remove(self, record_id: str) -> bool:
        """删除指定 id 的记录，返回是否成功。

        只删除论文库记录本身，不触碰原始文件 / 浏览器缓存 / 全局 AI 缓存。
        """
        records = self.load_all()
        remaining = [r for r in records if r.id != record_id]
        if len(remaining) == len(records):
            return False
        self._write(remaining)
        return True

    def clear(self) -> int:
        """清空论文库，返回删除条数（测试与显式用户操作使用）。"""
        count = len(self.load_all())
        if count:
            self._write([])
        return count

    # ---------- 统计 ----------

    def stats(self) -> dict:
        """本地统计（不调用 AI；§三十九）。"""
        records = self.load_all()
        strong = sum(1 for r in records if r.direction_match_level == "strong")
        priority = sum(
            1 for r in records
            if r.reading_priority in ("deep_read", "priority_read"))
        finished = sum(1 for r in records if r.reading_status == "finished")
        analyzed = sum(1 for r in records if r.has_enhanced_summary())
        return {
            "total": len(records),
            "strong": strong,
            "priority": priority,
            "finished": finished,
            "analyzed": analyzed,
            "unread": sum(1 for r in records
                          if r.reading_status == STATUS_UNREAD),
        }

    # ---------- 导出 ----------

    def export_json(self, path: Path) -> None:
        """导出全部记录为 JSON 数组（保持旧导出格式）。"""
        records = self.load_all()
        Path(path).write_text(
            json.dumps([r.to_dict() for r in records],
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def export_csv(self, path: Path) -> None:
        """导出全部记录为 CSV 文件。"""
        import csv
        records = self.load_all()
        if not records:
            Path(path).write_text("", encoding="utf-8-sig")
            return
        fieldnames = [
            "id", "title", "authors", "source", "year", "type",
            "database", "detail_url", "doi", "tags", "note",
            "saved_at", "reading_status", "document_type",
            "direction_match_level", "reading_priority", "enhanced_provider",
            "enhanced_model",
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in records:
                d = r.to_dict()
                d["authors"] = "；".join(d.get("authors") or [])
                d["tags"] = "；".join(d.get("tags") or [])
                d["type"] = d.get("artifact_type") or ""
                writer.writerow(d)

    # ---------- 内部方法 ----------

    def _write(self, records: list[LibraryRecord]) -> None:
        """原子写入 v2 格式：临时文件 → flush+fsync → os.replace。"""
        payload = {
            "schema_version": LIBRARY_SCHEMA_VERSION,
            "records": [r.to_dict() for r in records],
        }
        parent = self._path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, indent=2))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, str(self._path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
