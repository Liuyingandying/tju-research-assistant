"""基础报告生成：根据当前 SearchResult 列表生成 Markdown 检索报告。

纯模板渲染，不接入 AI；仅包含文献元数据，
不包含 Cookie、token、认证信息或整页 HTML。

v0.12 Phase 2：可选传入 explanations（RankingExplanation 列表，与 results
等长对齐）时，为每条文献追加"综合评分 / 推荐理由"两行；
不传时输出与旧格式完全一致。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from tju_info_retrieval.models.ranking import RankingExplanation
from tju_info_retrieval.version import VERSION


def _fmt(value: Any) -> str:
    """字段空值安全格式化：缺失时显示占位符。"""
    if value is None or value == "" or value == []:
        return "—"
    return str(value)


def _join_list(values: Any, sep: str = "、") -> str:
    """列表安全拼接；缺失时显示占位符。"""
    if not values:
        return "—"
    if isinstance(values, list):
        items = [str(v).strip() for v in values if str(v).strip()]
        return sep.join(items) if items else "—"
    return str(values)


def _source_statistics(results: list[dict]) -> list[tuple[str, int]]:
    """按首次出现顺序统计各数据库的结果数量。"""
    counts: dict[str, int] = {}
    for r in results:
        db = str(r.get("database") or "未知")
        counts[db] = counts.get(db, 0) + 1
    return list(counts.items())


def generate_markdown(
    results: list[dict],
    query_info: dict | None = None,
    explanations: list[RankingExplanation] | None = None,
) -> str:
    """根据 SearchResult.to_dict() 列表生成 Markdown 检索报告。

    ``query_info`` 可选，支持键：research_direction / expansion_direction / sources。
    ``explanations`` 可选，须与 results 等长并按序对齐；长度不匹配时整体忽略，
    不影响旧报告格式。
    """
    query_info = query_info or {}
    if explanations is not None and len(explanations) != len(results):
        explanations = None
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    lines.append("# 文献检索报告")
    lines.append("")
    lines.append(f"生成时间：{now}")
    lines.append(f"系统版本：v{VERSION}")
    lines.append("")

    # ---- 检索条件 ----
    lines.append("## 检索条件")
    lines.append("")
    lines.append(f"- 研究方向：{_fmt(query_info.get('research_direction'))}")
    lines.append(f"- 扩展方向：{_fmt(query_info.get('expansion_direction'))}")
    lines.append(f"- 数据来源：{_join_list(query_info.get('sources'))}")
    lines.append(f"- 结果数量：{len(results)}")
    lines.append("")

    # ---- 数据源统计 ----
    lines.append("## 数据源统计")
    lines.append("")
    stats = _source_statistics(results)
    if stats:
        lines.append("| 数据库 | 数量 |")
        lines.append("| --- | --- |")
        for db, n in stats:
            lines.append(f"| {db} | {n} |")
    else:
        lines.append("（无结果）")
    lines.append("")

    # ---- 文献列表 ----
    lines.append(f"## 文献列表（共 {len(results)} 条）")
    lines.append("")
    if not results:
        lines.append("（无检索结果）")
        return "\n".join(lines)

    for i, r in enumerate(results):
        rank = _fmt(r.get("rank"))
        title = _fmt(r.get("title"))
        lines.append(f"### {rank}. {title}")
        lines.append("")
        lines.append(f"- 作者：{_join_list(r.get('authors'), sep='；')}")
        lines.append(f"- 来源：{_fmt(r.get('source'))}")
        lines.append(f"- 年份：{_fmt(r.get('year'))}")
        lines.append(f"- 数据库：{_fmt(r.get('database'))}")
        lines.append(f"- 类型：{_fmt(r.get('document_type'))}")
        explanation = explanations[i] if explanations is not None else None
        if explanation is not None:
            lines.append(
                f"- 综合评分：{explanation.total_score:.1f}"
                f"（相关性 {explanation.relevance_score:.1f}"
                f" / 年份 {explanation.year_score:.1f}"
                f" / 引用 {explanation.citation_score:.1f}"
                f" / 来源 {explanation.database_score:.1f}）"
            )
            reasons = "；".join(explanation.reasons) if explanation.reasons else "—"
            lines.append(f"- 推荐理由：{reasons}")
        # 可选字段（存在时才输出）
        if r.get("venue"):
            lines.append(f"- 会议/期刊：{r['venue']}")
        if r.get("doi"):
            lines.append(f"- DOI：{r['doi']}")
        if r.get("keywords"):
            lines.append(f"- 关键词：{_join_list(r.get('keywords'))}")
        url = r.get("detail_url")
        if url:
            lines.append(f"- 链接：{url}")
        else:
            lines.append("- 链接：—")
        abstract = r.get("abstract")
        if abstract:
            lines.append("")
            lines.append(f"> {abstract}")
        lines.append("")

    return "\n".join(lines)


def write_markdown(content: str, path: str | Path) -> None:
    """以 UTF-8 写出 Markdown 报告文件。"""
    Path(path).write_text(content, encoding="utf-8")
