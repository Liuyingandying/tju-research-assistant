#!/usr/bin/env python3
"""PoC-1：在已获授权的受控 Edge 会话中，对 CNKI 执行一次正常搜索并读取前 10 条元数据。

流程（全自动，无人工导航 gate）：
1. 使用 PoC-0 的专用 Profile 启动受控 Edge，打开天津大学电子资源平台并检查登录状态。
2. 若未登录：提示用户先完成 TJU 认证，停止（不自动登录）。
3. 若已登录：在同一个 persistent context 中自动打开 https://www.cnki.net/。
4. 验证 CNKI 页面与搜索区域；检测机构授权（不作为硬性条件）。
5. 输入关键词“太赫兹”，点击检索，等待结果页。
6. 读取当前第一页前 10 条元数据（标题/作者/来源/年份/详情链接）。

边界：
- 单次搜索、单页前 10 条；无翻页、无下载、无并发、无授权绕过。
- 不读取/打印 Cookie、token、认证 header。
- 不读取结果行 seq 复选框中的会话标识。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - 依赖缺失时的降级提示
    sync_playwright = None

from poc_browser import (
    DEFAULT_URL,
    EDGE_PROFILE_DIR,
    RUNTIME_DIR,
    build_launch_kwargs,
    find_edge_executable,
    is_login_url,
)

SEARCH_KEYWORD = "太赫兹"
MAX_RESULTS = 10
RESULTS_JSON = RUNTIME_DIR / "poc1_cnki_results.json"
CNKI_HOME_URL = "https://www.cnki.net/"

# 已通过真实页面验证的稳定选择器（避免 nth-child / 长 XPath / 动态 class）
SEARCH_INPUT_SELECTORS = ("#txt_SearchText", "textarea.search-input")
SEARCH_BUTTON_SELECTORS = ("div.search-btn", "input.search-btn")
RESULT_TABLE_SELECTOR = "table.result-table-list"


def is_cnki_page(url: str) -> bool:
    """判断 URL 是否属于真实 CNKI 站点（host 以 cnki.net 结尾）。"""
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except Exception:
        return False
    return host == "cnki.net" or host.endswith(".cnki.net")


def classify_login_state(url: str, body_text: str) -> str:
    """根据 TJU 平台 URL 与正文判断登录状态：login_page / logged_in / unknown。"""
    if is_login_url(url):
        return "login_page"
    if "已授权" in (body_text or ""):
        return "logged_in"
    return "unknown"


def detect_institution(page) -> str | None:
    """检测 CNKI 页面是否展示机构标识（仅作提示，不作硬性条件）。"""
    try:
        body = page.inner_text("body") or ""
    except Exception:
        return None
    if "天津大学" in body:
        return "天津大学"
    return None


def normalize_result(raw: dict) -> dict:
    """规范化单条结果：缺失字段保持 None / []，标题必须存在。"""
    title = (raw.get("title") or "").strip() or None
    authors = raw.get("authors") or []
    if not isinstance(authors, list):
        authors = [authors]
    authors = [str(a).strip() for a in authors if str(a).strip()]
    return {
        "rank": raw.get("rank"),
        "title": title,
        "authors": authors,
        "source": raw.get("source") or None,
        "year": raw.get("year") or None,
        "detail_url": raw.get("detail_url") or None,
    }


def cap_results(results: list, max_results: int = MAX_RESULTS) -> list:
    """最多只取前 max_results 条（本阶段固定 10）。"""
    return results[:max_results]


def build_payload(keyword: str, results: list) -> dict:
    """构造输出 JSON 结构。"""
    return {"keyword": keyword, "count": len(results), "results": results}


def extract_row(row) -> dict:
    """从单行结果提取元数据（不读取 seq 复选框的会话标识）。"""
    name_a = row.query_selector("td.name a.fz14")
    title = name_a.inner_text().strip() if name_a else None
    detail_url = name_a.get_attribute("href") if name_a else None

    author_td = row.query_selector("td.author")
    authors: list = []
    if author_td:
        raw = author_td.inner_text() or ""
        authors = [a.strip() for a in raw.split(";") if a.strip()]

    source_td = row.query_selector("td.source")
    source = source_td.inner_text().strip() if source_td else None

    date_td = row.query_selector("td.date")
    year = None
    if date_td:
        d = (date_td.inner_text() or "").strip()
        if len(d) >= 4 and d[:4].isdigit():
            year = d[:4]

    return {
        "title": title,
        "authors": authors,
        "source": source,
        "year": year,
        "detail_url": detail_url,
    }


def extract_results(page, max_results: int = MAX_RESULTS) -> list:
    """从结果页提取前 max_results 条（跳过表头行）。"""
    rows = page.query_selector_all(f"{RESULT_TABLE_SELECTOR} tr")
    results: list = []
    for row in rows[1:]:  # 跳过表头
        if len(results) >= max_results:
            break
        raw = extract_row(row)
        if not raw.get("title"):
            continue
        results.append(raw)
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return results


def wait_for_result_page(context, timeout_s: float = 30.0):
    """等待结果页出现（kns.cnki.net .../defaultresult/...）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for page in context.pages:
            try:
                if "defaultresult" in page.url:
                    return page
            except Exception:
                continue
        time.sleep(1)
    return None


def dump_search_diagnostics(page) -> None:
    """定位失败时输出有限的 DOM 诊断信息（不含认证信息）。"""
    print("页面 input/textarea：")
    inputs = page.eval_on_selector_all(
        "input, textarea",
        """els => els.map(e => ({
            tag: e.tagName.toLowerCase(),
            type: e.type || '',
            id: e.id || '',
            placeholder: e.placeholder || '',
            cls: (e.className || '').toString().slice(0, 40),
        })).slice(0, 15)""",
    )
    for i in inputs:
        print(f"  {i}")
    print("含 检索/搜索 的元素：")
    btns = page.eval_on_selector_all(
        "button, div, span",
        """els => els.map(e => ({
            tag: e.tagName.toLowerCase(),
            text: (e.innerText || '').trim().slice(0, 10),
            cls: (e.className || '').toString().slice(0, 40),
        })).filter(x => /检索|搜索/.test(x.text)).slice(0, 10)""",
    )
    for b in btns:
        print(f"  {b}")


def main() -> int:
    if sync_playwright is None:
        print("错误：未安装 playwright。请先执行：python -m pip install -r requirements.txt", file=sys.stderr)
        return 2

    edge_path = find_edge_executable()
    if edge_path is None:
        print("错误：未找到 Microsoft Edge。", file=sys.stderr)
        return 2

    EDGE_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 64)
    print("PoC-1：CNKI 单次检索 + 前 10 条元数据读取")
    print("=" * 64)
    print(f"Edge: {edge_path}")
    print(f"专用 Profile: {EDGE_PROFILE_DIR}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(**build_launch_kwargs(edge_path))
        try:
            page = context.pages[0] if context.pages else context.new_page()

            # 1) 打开 TJU 平台并检查登录状态
            try:
                page.goto(DEFAULT_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
            except Exception as exc:
                print(f"警告：TJU 平台加载未完成（{exc}）")
            title, url, body = "", "", ""
            try:
                title = page.title()
                url = page.url
                body = page.inner_text("body") or ""
            except Exception:
                pass
            print("-" * 64)
            print(f"TJU title: {title}")
            print(f"TJU url: {url}")
            state = classify_login_state(url, body)
            if state == "login_page":
                print("未检测到天津大学登录状态（当前处于登录页）。")
                print("本程序不会自动登录。请先在受控浏览器中完成天津大学认证后重新运行。")
                return 1
            if state == "logged_in":
                print("TJU 登录状态：已登录（检测到“已授权”）。")
            else:
                print("TJU 登录状态：无法明确判断，继续尝试 CNKI（由 CNKI 授权检测兜底）。")
            print("-" * 64)

            # 2) 自动打开 CNKI 首页
            print(f"正在自动打开 CNKI 首页：{CNKI_HOME_URL}")
            page.goto(CNKI_HOME_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            if not is_cnki_page(page.url):
                print(f"CNKI 页面未正常打开（hostname 非 cnki.net）：{page.url}")
                return 1
            print(f"CNKI 页面已打开: {page.title()!r} {page.url[:120]}")

            institution = detect_institution(page)
            if institution:
                print(f"CNKI 机构授权：检测到 {institution}")
            else:
                print("CNKI 机构授权：未检测到机构标识（不作为硬性条件，继续检查搜索区域）。")

            # 3) 定位搜索框与检索按钮
            search_input = None
            for sel in SEARCH_INPUT_SELECTORS:
                if page.query_selector(sel):
                    search_input = page.query_selector(sel)
                    break
            if search_input is None:
                print("未检测到 CNKI 搜索区域。")
                print("可能原因：登录失效 / 无授权 / 验证码 / 风控。")
                print("停止搜索，不绕过。页面诊断（不含认证信息）：")
                dump_search_diagnostics(page)
                return 1

            search_button = None
            for sel in SEARCH_BUTTON_SELECTORS:
                if page.query_selector(sel):
                    search_button = page.query_selector(sel)
                    break
            if search_button is None:
                print("未找到 CNKI 检索按钮。")
                return 1

            # 4) 执行一次搜索
            search_input.fill(SEARCH_KEYWORD)
            search_button.click()
            print(f"已输入关键词“{SEARCH_KEYWORD}”并点击检索，等待结果页...")

            result_page = wait_for_result_page(context)
            if result_page is None:
                print("未检测到结果页（kns.cnki.net .../defaultresult/...），请人工确认。")
                return 1

            time.sleep(2)
            results = extract_results(result_page, MAX_RESULTS)
            results = cap_results(results, MAX_RESULTS)

            # 5) 输出 + 保存
            print(f"\n检索关键词：{SEARCH_KEYWORD}")
            print(f"结果页读取：{len(results)} 条\n")
            for r in results:
                print(f"[{r['rank']}]")
                print(f"标题：{r['title']}")
                print(f"作者：{'；'.join(r['authors']) if r['authors'] else 'null'}")
                print(f"来源：{r['source']}")
                print(f"年份：{r['year']}")
                print(f"详情链接：{r['detail_url']}")
                print()

            payload = build_payload(SEARCH_KEYWORD, results)
            RESULTS_JSON.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"已保存: {RESULTS_JSON}")
        except KeyboardInterrupt:
            print("\n检测到 Ctrl+C，正在关闭浏览器。")
        finally:
            try:
                context.close()
            except Exception:
                pass

    print("本次浏览器会话已正常结束，专用 Profile 已保留。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
