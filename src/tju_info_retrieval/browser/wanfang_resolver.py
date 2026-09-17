"""万方详情按需解析器（v0.13 B′ v1）。

依据真实探针结论（docs/v0.13_wanfang_detail_resolver_probe.md）：
- 完整标题检索 10/10 首条精确命中，标题即查询键；
- 结果页无 <a href>，详情 URL 只能通过真实鼠标 position 点击捕获；
- 点击成败取决于水合：渲染后 ≥15s 的可信点击 5/5 成功，<5s 0/4；
- 成功导航 100% 为 new_tab，需 adopt 新页面为会话活动页；
- JS el.click() / focus+Enter 均无效。

流程（必须在创建会话的线程栈内运行，v0.13 生命周期约束）：
  门户 → 万方资源 → 表单搜完整标题 → 水合稳定等待 → 归一化 exact@1 校验
  → position 点击阶梯 + expect_page → session.adopt(详情新标签)
失败抛 ResolverError，由调用方降级提示并同栈释放会话。
"""
from __future__ import annotations

import re
import time

from tju_info_retrieval.browser.portal import PortalNavigator

WANFANG_RESOURCE = "万方数据知识服务平台"

# 点击偏移阶梯（相对 .title-area 左上角；左侧复选框约占 30-60px，
# 偏移须落在标题纯文本区域，避开高亮子元素与复选框）
_CLICK_OFFSETS = ((300, 12), (160, 12))
# 水合等待：结果渲染后至少稳定等待的秒数（探针实测 <5s 点击 0/4、≥15s 5/5）
HYDRATION_MIN_SECONDS = 15.0
# 结果计数稳定判定窗口
_STABLE_SECONDS = 3.0
# 点击后导航观测超时（new_tab 形态 ~1s 内出现）
_NAV_TIMEOUT_MS = 10_000


class ResolverError(RuntimeError):
    """万方详情定位失败（调用方应降级提示并同栈释放会话）。"""


def _normalize_title(text: str | None) -> str:
    """标题归一（匹配用）：去空白与标点，转小写。"""
    if not text:
        return ""
    s = re.sub(r"[\s\u3000]+", "", text)
    return re.sub(r"[^\w]+", "", s, flags=re.UNICODE).lower()


def match_top_title(top1_title: str | None, target_title: str) -> bool:
    """归一化精确匹配 top-1 结果与目标标题（严谨模式：仅接受 exact@1）。"""
    target = _normalize_title(target_title)
    if not target or not top1_title:
        return False
    return _normalize_title(top1_title) == target


def _areas_count(page) -> int:
    try:
        return page.locator(".title-area").count()
    except Exception:  # noqa: BLE001
        return -1


def _titles(page) -> list[str]:
    try:
        return page.evaluate(
            "() => Array.from(document.querySelectorAll('.title-area span.title'))"
            ".map(el => (el.innerText || '').trim()).filter(t => t)"
        )
    except Exception:  # noqa: BLE001
        return []


def _wait_results_stable(page, timeout_s: float = 25.0) -> bool:
    """等待结果渲染、计数稳定，且保证 ≥HYDRATION_MIN_SECONDS 水合。"""
    deadline = time.monotonic() + timeout_s
    rendered_at = None
    stable_since = None
    last_count = -1
    while time.monotonic() < deadline:
        now = time.monotonic()
        count = _areas_count(page)
        if count >= 1:
            if rendered_at is None:
                rendered_at = now
            if count != last_count:
                stable_since = now
            hydrated = now - rendered_at >= HYDRATION_MIN_SECONDS
            stable = stable_since is not None and now - stable_since >= _STABLE_SECONDS
            if hydrated and stable:
                return True
        else:
            rendered_at = None
            stable_since = None
        last_count = count
        time.sleep(1.0)
    return False


def _click_and_adopt(landing, session, emit) -> None:
    """position 点击阶梯：捕获 new_tab 详情页并 adopt 为会话活动页。"""
    area = landing.locator(".title-area").first
    last_err: Exception | None = None
    for x, y in _CLICK_OFFSETS:
        before = {p.url for p in landing.context.pages}
        try:
            with landing.context.expect_page(timeout=_NAV_TIMEOUT_MS) as page_info:
                area.click(position={"x": x, "y": y}, timeout=5000)
            detail = page_info.value
        except Exception as exc:  # noqa: BLE001 - 无导航：换下一个偏移
            last_err = exc
            time.sleep(3.0)
            continue
        try:
            detail.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(4.0)  # 详情页渲染
        session.adopt(detail)
        emit("已定位万方全文页面")
        return
    raise ResolverError(f"点击结果标题未产生详情页面（最后错误：{last_err!r}）")


def resolve_detail_page(session, title: str, status=None) -> None:
    """按标题在万方定位并打开详情页；成功后详情页已被 session.adopt。

    session: DetailBrowserSession（需已 start()；鸭子类型兼容
    PortalNavigator 所需的 open_tju/page/context 协议）。
    """
    def emit(msg: str) -> None:
        if status:
            status(msg)

    target = (title or "").strip()
    if not target:
        raise ResolverError("标题为空，无法定位万方全文页")

    emit("正在打开天津大学门户...")
    portal = PortalNavigator(session)
    portal.open_portal()
    emit("正在进入万方数据知识服务平台...")
    landing = portal.open_resource(WANFANG_RESOURCE, "中文资源")

    emit("正在万方检索论文标题...")
    landing.locator("#search-input").clear()
    landing.fill("#search-input", target)
    landing.locator("button:has-text('检索')").first.click()
    if not _wait_results_stable(landing):
        raise ResolverError("万方结果页未在时限内渲染")

    titles = _titles(landing)
    top1 = titles[0] if titles else ""
    if not match_top_title(top1, target):
        raise ResolverError(f"万方首条结果与论文标题不匹配（top1: {top1[:40]}）")

    emit("正在打开匹配的全文页面...")
    _click_and_adopt(landing, session, emit)
