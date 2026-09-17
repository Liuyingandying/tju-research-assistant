"""Frozen 包内部诊断入口；只复用正式组件，不复制产品逻辑。"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from tju_info_retrieval.app_paths import (
    edge_profile_dir,
    is_frozen,
    logs_dir,
    user_data_root,
)

logger = logging.getLogger(__name__)


def _write_result(mode: str, payload: dict) -> None:
    out = logs_dir() / f"packaging_smoke_{mode}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _offline_smoke() -> dict:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from tju_info_retrieval.models.result import SearchResult
    from tju_info_retrieval.services.offline_summary import summarize_offline
    from tju_info_retrieval.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    window._on_load_demo()
    result_count = window.table.rowCount()
    if result_count <= 0:
        raise RuntimeError("demo mode 未生成结果")
    sample = SearchResult(
        rank=1,
        title="太赫兹检测方法",
        database="CNKI",
        abstract="本研究采用太赫兹光谱完成样品检测，并获得稳定的识别结果。",
    )
    summary = summarize_offline(sample)
    if not summary.research_content:
        raise RuntimeError("Offline Summary 未生成内容")
    window.resize(920, 560)
    app.processEvents()
    app.processEvents()
    narrow_two_rows = window._action_bar._two_rows
    narrow_row_counts = [
        window._action_bar._row_a.count(), window._action_bar._row_b.count()
    ]
    affiliation_overlap = window.input_author_affiliation.geometry().intersects(
        window.combo_affiliation_mode.geometry()
    )
    table_visible = (
        window.table.viewport().width() > 0
        and window.table.viewport().height() > 0
    )
    buttons_visible = all(
        button.isVisible() and button.width() >= button.minimumSizeHint().width()
        for button in window._action_bar._buttons
    )
    window.resize(1920, 1080)
    app.processEvents()
    app.processEvents()
    wide_single_row = not window._action_bar._two_rows
    if not all((narrow_two_rows, narrow_row_counts == [5, 5],
                not affiliation_overlap, table_visible, buttons_visible,
                wide_single_row)):
        raise RuntimeError("Responsive / HiDPI frozen UI geometry 检查失败")
    window.close()
    app.processEvents()
    return {"gui_constructed": True, "demo_results": result_count,
            "offline_summary": True, "clean_close": True,
            "responsive_ui": {
                "minimum_size": "920x560",
                "narrow_two_rows": narrow_two_rows,
                "narrow_row_counts": narrow_row_counts,
                "affiliation_overlap": affiliation_overlap,
                "table_visible": table_visible,
                "buttons_visible": buttons_visible,
                "wide_single_row": wide_single_row,
            }}


def _edge_smoke() -> dict:
    from tju_info_retrieval.browser.session import BrowserSession
    from poc_browser import find_edge_executable

    edge_path = find_edge_executable()
    if not edge_path:
        raise RuntimeError("Microsoft Edge 未检测到")
    session = BrowserSession()
    try:
        session.start()
        alive = session.is_browser_open()
        if not alive:
            raise RuntimeError("受控 Edge 启动后未保持存活")
    finally:
        session.shutdown()
    return {"edge_detected": True, "browser_started": True,
            "profile_created": edge_profile_dir().is_dir()}


def _sources_smoke() -> dict:
    from tju_info_retrieval.browser.session import BrowserSession
    from tju_info_retrieval.models.query import QueryRequest
    from tju_info_retrieval.services.offline_summary import summarize_offline
    from tju_info_retrieval.services.search_service import SearchService

    scenarios = (
        ("CNKI", "太赫兹"),
        ("CNKI专利", "太赫兹"),
        ("天津大学新闻网", "太赫兹"),
        ("IEEE Xplore", "terahertz"),
    )
    session = BrowserSession()
    results: dict[str, dict] = {}
    try:
        session.start()
        service = SearchService(session)
        for source, topic in scenarios:
            found = service.search(QueryRequest(
                research_direction=topic, sources=[source], result_count=1
            ), status=lambda message: None)
            results[source] = {"count": len(found),
                               "title": found[0].title if found else ""}
            if not found:
                raise RuntimeError(f"{source} frozen smoke 无结果")
        first = service.search(QueryRequest(
            research_direction="太赫兹", sources=["CNKI"], result_count=1
        ), status=lambda message: None)[0]
        summary = summarize_offline(first)
        results["Offline Summary"] = {
            "ok": bool(summary.research_content),
            "evidence_available": bool(first.abstract),
        }
    finally:
        session.shutdown()
    return results


def run_smoke(mode: str) -> int:
    """运行一个明确诊断并把无敏感信息的结果写入用户 logs 目录。"""
    payload = {
        "mode": mode,
        "frozen": is_frozen(),
        "user_data": str(user_data_root()),
        "python": sys.version.split()[0],
    }
    try:
        if mode == "offline":
            payload["result"] = _offline_smoke()
        elif mode == "edge":
            payload["result"] = _edge_smoke()
        elif mode == "sources":
            payload["result"] = _sources_smoke()
        else:
            raise ValueError(f"未知 packaging smoke: {mode}")
        payload["status"] = "PASS"
        _write_result(mode, payload)
        logger.info("packaging smoke PASS mode=%s", mode)
        return 0
    except Exception as exc:  # noqa: BLE001 - 诊断必须留下文件
        payload["status"] = "FAIL"
        payload["error"] = f"{type(exc).__name__}: {exc}"
        _write_result(mode, payload)
        logger.exception("packaging smoke FAIL mode=%s", mode)
        return 1
