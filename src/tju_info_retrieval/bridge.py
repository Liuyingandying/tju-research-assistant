"""Minimal bridge CLI for external hosts (Firefly plugin adapter).

Purpose: expose the EXISTING retrieval capability (SearchService, browser-driven
with the TJU library authorization) through a tiny, stable JSON CLI. This is a
thin entry — it reuses the production SearchService/BrowserSession; no retrieval
logic is reimplemented here.

Usage:
    python -m tju_info_retrieval.bridge health
    python -m tju_info_retrieval.bridge auth_status
    python -m tju_info_retrieval.bridge auth [--timeout_s 300]
    python -m tju_info_retrieval.bridge search --query "研究方向" [--top_k 5] [--sources CNKI,Wanfang]

Both commands print one JSON object to stdout (UTF-8). A non-zero exit code is
never produced for business failures — the JSON carries {"ok": false, "error": ...}
so the caller can degrade gracefully.

This entry does NOT start a GUI, does NOT create a second Playwright runtime, and
does NOT modify any data. It runs the same search pipeline the desktop app runs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

# The bridge lives at src/tju_info_retrieval/bridge.py; add src to sys.path so it
# runs as `python -m tju_info_retrieval.bridge` from the project root OR from
# anywhere (the Firefly plugin passes the project root as cwd / PYTHONPATH).
_SRC = str(Path(__file__).resolve().parents[1])
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _out(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _health() -> dict:
    import tju_info_retrieval.version as ver
    from tju_info_retrieval.app_paths import user_data_root
    from tju_info_retrieval.browser.session import find_edge_executable

    edge = find_edge_executable()
    profile = user_data_root() / "edge_profile"
    return {
        "ok": bool(edge),
        "version": ver.VERSION,
        "edge": bool(edge),
        "auth_profile_present": profile.exists(),
        "user_data_root": str(user_data_root()),
        "error": None if edge else "Microsoft Edge 未检测到",
    }


def _search(query: str, top_k: int, sources: list[str]) -> dict:
    from tju_info_retrieval.app_paths import ensure_user_dirs
    from tju_info_retrieval.browser.session import BrowserSession
    from tju_info_retrieval.models.query import QueryRequest
    from tju_info_retrieval.services.search_service import SearchService

    ensure_user_dirs()
    session = BrowserSession()
    session.start()
    try:
        service = SearchService(session)
        request = QueryRequest(
            research_direction=query,
            sources=sources or ["CNKI"],
            result_count=top_k,
            candidate_count=max(top_k * 3, 10),
            expansion_direction="综合",
        )
        results = service.search(request)
        return {
            "ok": True,
            "query": query,
            "count": len(results),
            "results": [_result_to_dict(r) for r in results][:top_k],
            "error": None,
        }
    finally:
        # One-shot subprocess: stop the browser this session started (never
        # touches any external Edge instance — only our playwright runtime).
        playwright = getattr(session, "_playwright", None)
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass


def _auth_status() -> dict:
    """Probe the CURRENT TJU authorization state (uses the SAME controlled
    Edge + persistent profile as the GUI / SearchService). Read-only: opens the
    portal and reads the page; never fills credentials."""
    from tju_info_retrieval.app_paths import ensure_user_dirs
    from tju_info_retrieval.browser.session import BrowserSession

    ensure_user_dirs()
    session = BrowserSession()
    session.start()
    try:
        state = session.check_tju_auth(timeout_ms=20000)
        return {
            "ok": True,
            "auth_ok": state == "logged_in",
            "state": state,
            "error": None,
        }
    finally:
        playwright = getattr(session, "_playwright", None)
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass


def _auth(timeout_s: int) -> dict:
    """Open the controlled Edge at the TJU portal for a MANUAL login.

    The user completes their school login (统一身份认证/二维码) in the visible
    controlled Edge — no credential is ever read, typed or stored here. Polls
    the page read-only (never navigates, so the login form is not disrupted);
    returns when logged in, when the browser is closed, or on timeout. After a
    successful login, ``auth_status`` / ``search`` reuse the persisted profile
    session.
    """
    from tju_info_retrieval.app_paths import ensure_user_dirs
    from tju_info_retrieval.browser.session import BrowserSession

    ensure_user_dirs()
    session = BrowserSession()
    session.start()
    try:
        last_state = "unknown"
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            state = session.check_tju_auth(timeout_ms=8000)
            last_state = state or last_state
            if state == "logged_in":
                return {
                    "ok": True,
                    "auth_ok": True,
                    "state": state,
                    "error": None,
                }
            try:
                browser_closed = not session.is_browser_open()
            except Exception:  # noqa: BLE001 - best-effort liveness read
                browser_closed = False
            if browser_closed:
                return {
                    "ok": False,
                    "auth_ok": False,
                    "state": last_state,
                    "error": "受控浏览器已关闭，未检测到登录完成。",
                }
            time.sleep(3)
        return {
            "ok": False,
            "auth_ok": False,
            "state": last_state,
            "error": "登录超时，请在受控 Edge 中完成天津大学登录后重试。",
        }
    finally:
        playwright = getattr(session, "_playwright", None)
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass


def _result_to_dict(result) -> dict:
    """Normalize one SearchResult into a JSON-safe dict (never mutates it)."""
    return {
        "rank": getattr(result, "rank", None),
        "title": getattr(result, "title", "") or "",
        "authors": list(getattr(result, "authors", []) or []),
        "source": getattr(result, "source", None),
        "year": getattr(result, "year", None),
        "detail_url": getattr(result, "detail_url", None),
        "document_type": getattr(result, "document_type", None),
        "database": getattr(result, "database", "") or "",
        "abstract": getattr(result, "abstract", None),
        "doi": getattr(result, "doi", None),
        "keywords": list(getattr(result, "keywords", []) or []),
        "venue": getattr(result, "venue", None),
        "citation_count": getattr(result, "citation_count", None),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tju_info_retrieval.bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health", help="环境健康检查（Edge/授权 profile/版本）")
    sub.add_parser("auth_status", help="探测当前天津大学授权状态（只读）")
    auth_p = sub.add_parser("auth", help="打开受控 Edge 供人工完成天津大学登录")
    auth_p.add_argument("--timeout_s", type=int, default=300, help="等待登录的秒数")
    search_p = sub.add_parser("search", help="执行一次真实检索")
    search_p.add_argument("--query", required=True, help="研究方向/检索词")
    search_p.add_argument("--top_k", type=int, default=5, help="返回条数")
    search_p.add_argument(
        "--sources", default="CNKI", help="数据源逗号分隔（CNKI/Wanfang/IEEE）"
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "health":
            _out(_health())
        elif args.command == "auth_status":
            _out(_auth_status())
        elif args.command == "auth":
            _out(_auth(args.timeout_s))
        else:
            sources = [s.strip() for s in args.sources.split(",") if s.strip()]
            _out(_search(args.query, args.top_k, sources))
    except Exception as exc:  # noqa: BLE001 - business failures go in the JSON
        _out(
            {
                "ok": False,
                # human-readable message only — no exception-type prefix, no
                # traceback (the caller renders this in user-facing chat)
                "error": str(exc).strip(),
                "error_type": type(exc).__name__,
                "cause": str(exc)[:500],
            }
        )
        return 0  # never a hard crash — the caller reads the JSON
    return 0


if __name__ == "__main__":
    sys.exit(main())