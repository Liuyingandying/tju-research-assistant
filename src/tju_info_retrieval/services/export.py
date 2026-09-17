"""结果导出：JSON / CSV / Markdown。

导出字段固定为文献元数据，绝不包含 Cookie、token、认证信息或整页 HTML。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

EXPORT_FIELDS = [
    "rank",
    "title",
    "authors",
    "source",
    "year",
    "document_type",
    "database",
    "detail_url",
    "abstract",
    "doi",
    "keywords",
    "venue",
    "authors_raw",
]
HEADERS = [
    "序号", "标题", "作者", "来源", "年份", "类型", "数据库", "详情链接",
    "摘要", "DOI", "关键词", "会议/期刊", "原始作者",
]


def _rows(results: Iterable[dict]) -> list[dict]:
    out = []
    for r in results:
        d = dict(r)
        d["authors"] = "；".join(d.get("authors") or [])
        out.append(d)
    return out


def export_json(results: Iterable[dict], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(_rows(results), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def export_csv(results: Iterable[dict], path: str | Path) -> None:
    rows = _rows(results)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in EXPORT_FIELDS})


def export_markdown(results: Iterable[dict], path: str | Path) -> None:
    rows = _rows(results)
    lines = [
        "| " + " | ".join(HEADERS) + " |",
        "| " + " | ".join(["---"] * len(HEADERS)) + " |",
    ]
    for row in rows:
        cells = [str(row.get(f, "")) for f in EXPORT_FIELDS]
        lines.append("| " + " | ".join(cells) + " |")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
