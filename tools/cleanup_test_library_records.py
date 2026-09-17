#!/usr/bin/env python3
"""论文库测试残留清理（v0.18 Phase 2.9-B.1-C）。

默认 **dry-run**：只列出候选，不修改任何文件。
只有显式加 `--execute` 才真正删除。

删除规则（白名单，禁止模糊匹配，细则见
docs/v0.18_phase2_9b1_cleanup_plan.md）：

- R1 `test_title`（默认启用）：标题精确等于测试 fixture 标题
  （Test paper / Deep THz study）；
- R2 `test_marker`（需 --include-marked）：标题/来源/笔记含显式测试标记
  （验收测试 / 测试备注 / test note）；
- R3 `test_fixture_source`（需 --include-fixture-source）：
  来源为 Test Journal 且作者为 ['Author A']。

另有 `--clear-marked-note`：只清除笔记中的测试标记，保留记录本身。

用法：
    python tools/cleanup_test_library_records.py --dry-run
    python tools/cleanup_test_library_records.py --execute
    python tools/cleanup_test_library_records.py --execute --include-marked
    python tools/cleanup_test_library_records.py --execute --clear-marked-note
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---- 规则常量（白名单，禁止模糊匹配）----
TEST_TITLES = ("test paper", "deep thz study")
TEST_MARKERS = ("验收测试", "测试备注", "test note", "testmarker")
FIXTURE_SOURCE = "test journal"
FIXTURE_AUTHORS = ("author a",)

RULE_TEST_TITLE = "test_title"
RULE_TEST_MARKER = "test_marker"
RULE_FIXTURE_SOURCE = "test_fixture_source"


def _norm(text) -> str:
    return str(text or "").strip().lower()


def _record_matches(record: dict, rule: str) -> bool:
    title = _norm(record.get("title"))
    if rule == RULE_TEST_TITLE:
        return title in TEST_TITLES
    if rule == RULE_TEST_MARKER:
        haystack = " ".join([
            str(record.get("title") or ""),
            str(record.get("source") or ""),
            str(record.get("note") or ""),
        ]).lower()
        return any(marker.lower() in haystack for marker in TEST_MARKERS)
    if rule == RULE_FIXTURE_SOURCE:
        authors = [_norm(a) for a in (record.get("authors") or [])]
        return (_norm(record.get("source")) == FIXTURE_SOURCE
                and authors == list(FIXTURE_AUTHORS))
    return False


def find_candidates(records: list[dict], rules: list[str]) -> list[tuple[dict, str]]:
    """返回 [(record, 命中的规则)]，同一记录只保留首个命中的规则。"""
    found: list[tuple[dict, str]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        for rule in rules:
            if _record_matches(record, rule):
                found.append((record, rule))
                break
    return found


def _load(path: Path) -> tuple[list[dict], str]:
    """读取论文库，返回 (records, 文件格式)。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"], "v2"
    if isinstance(data, list):
        return data, "v1"
    raise SystemExit(f"无法识别的论文库格式: {path}")


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _describe(record: dict) -> str:
    return (f"    id={record.get('id')}\n"
            f"      标题={record.get('title')}\n"
            f"      来源={record.get('source')} / 作者={record.get('authors')}\n"
            f"      创建={record.get('saved_at')} / 链接={record.get('detail_url')}\n"
            f"      标签={record.get('tags')} / 状态={record.get('reading_status')}"
            f" / 笔记={record.get('note')!r}")


def _strip_markers(text: str) -> str:
    """去掉笔记中的测试标记片段，保留其余内容。"""
    result = str(text or "")
    for marker in TEST_MARKERS:
        result = result.replace(marker, "")
    result = result.replace("：：", "：").strip(" ：:；;")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="论文库测试残留清理（默认 dry-run，不改文件）")
    parser.add_argument("--library", default=None,
                        help="论文库 JSON 路径（默认 app_paths.library_path()）")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="只列出候选（默认行为）")
    parser.add_argument("--execute", action="store_true",
                        help="真正执行删除（必须先确认 dry-run 结果）")
    parser.add_argument("--include-marked", action="store_true",
                        help="同时删除带测试标记（验收测试/测试备注）的记录")
    parser.add_argument("--include-fixture-source", action="store_true",
                        help="同时删除 Test Journal / Author A 的 fixture 记录")
    parser.add_argument("--clear-marked-note", action="store_true",
                        help="只清除笔记中的测试标记，保留记录")
    parser.add_argument("--backup", default=None,
                        help="执行前的备份路径"
                             "（默认 <library>.bak-<时间戳>）")
    parser.add_argument("--report", default=None,
                        help="输出可追溯的 JSON 报告路径"
                             "（删除明细 + 前后 sha256）")
    args = parser.parse_args(argv)

    if args.library:
        path = Path(args.library)
    else:
        from tju_info_retrieval import app_paths

        path = app_paths.library_path()

    if not path.is_file():
        print(f"论文库文件不存在：{path}")
        return 1

    records, fmt = _load(path)
    rules = [RULE_TEST_TITLE]
    if args.include_marked:
        rules.append(RULE_TEST_MARKER)
    if args.include_fixture_source:
        rules.append(RULE_FIXTURE_SOURCE)

    candidates = find_candidates(records, rules)
    marked_only = [r for r in records
                   if _record_matches(r, RULE_TEST_MARKER)]

    print(f"论文库：{path}")
    print(f"格式：{fmt} ｜ 记录总数：{len(records)}")
    print(f"启用规则：{', '.join(rules)}"
          + (f"（另有 --clear-marked-note）" if args.clear_marked_note else ""))
    print()

    if not candidates and not (args.clear_marked_note and marked_only):
        print("未发现符合规则的测试残留记录：无需清理。")
        return 0

    print(f"命中候选 {len(candidates)} 条：")
    for record, rule in candidates:
        print(f"  [{rule}]")
        print(_describe(record))
    print()

    if not args.execute:
        print("DRY-RUN：未修改任何文件。")
        print("确认无误后加 --execute 执行删除。")
        return 0

    # ---- 执行 ----
    sha_before = _sha256(path)
    backup = (Path(args.backup) if args.backup
              else path.with_name(
                  f"{path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"))
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    print(f"已备份：{backup}")

    ids_to_remove = {r.get("id") for r, _ in candidates}
    detail_by_id = {r.get("id"): r for r, _ in candidates}

    note_changes: list = []
    if args.clear_marked_note and not args.include_marked:
        # 只清标记：保留记录，去掉笔记里的测试标记
        changed = 0
        for record in records:
            if _record_matches(record, RULE_TEST_MARKER):
                before_note = record.get("note")
                new_note = _strip_markers(before_note)
                if new_note != before_note:
                    record["note"] = new_note
                    changed += 1
                    note_changes.append((record, before_note, new_note))
        remaining = records
        removed_n = 0
    else:
        remaining = [r for r in records
                     if not isinstance(r, dict) or r.get("id") not in ids_to_remove]
        changed = 0
        removed_n = len(records) - len(remaining)

    # 原子写（与 LibraryStore 一致：临时文件 → fsync → replace）
    import os
    import tempfile

    payload = ({"schema_version": 2, "records": remaining}
               if fmt == "v2" else remaining)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, indent=2))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    sha_after = _sha256(path)
    if args.report:
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "library": str(path),
            "backup": str(backup),
            "rules_applied": rules,
            "include_marked": bool(args.include_marked),
            "clear_marked_note": bool(args.clear_marked_note),
            "records_before": len(records),
            "records_after": len(remaining),
            "removed_count": removed_n,
            "note_markers_cleared": changed,
            "sha256_before": sha_before,
            "sha256_after": sha_after,
            "removed_records": [
                {"id": r.get("id"), "title": r.get("title"),
                 "authors": r.get("authors"), "source": r.get("source"),
                 "saved_at": r.get("saved_at"), "rule": rule}
                for r, rule in candidates
            ],
            "cleared_notes": [
                {"id": r.get("id"), "title": r.get("title"),
                 "note_before": before, "note_after": after}
                for r, before, after in note_changes
            ],
            "kept_records": [
                {"id": r.get("id"), "title": r.get("title")}
                for r in remaining if isinstance(r, dict)
            ],
        }
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写出清理报告：{report_path}")

    print(f"已删除 {removed_n} 条记录；清除备注标记 {changed} 条。")
    if removed_n:
        print("删除明细：")
        for record_id in ids_to_remove:
            record = detail_by_id.get(record_id) or {}
            print(f"  - {record_id} | {record.get('title')}")
    print(f"剩余记录数：{len(remaining)}")
    print("提示：如需回滚，用上面的备份文件覆盖即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
