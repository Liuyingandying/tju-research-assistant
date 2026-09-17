"""天津大学图书馆资源门户导航器。

管理门户页面、资源列表发现、资源代理入口跳转。
使用 ``context.expect_page()`` 等待代理隧道建立（新标签打开），
必须配合 Playwright sync API 使用（``time.sleep`` 轮询无法捕获事件）。

典型用法：:

    from tju_info_retrieval.browser.session import BrowserSession
    from tju_info_retrieval.browser.portal import PortalNavigator

    session = BrowserSession()
    session.start()
    portal = PortalNavigator(session)
    portal.open_portal()
    names = portal.list_resources("中文资源")
    landing = portal.open_resource("万方数据知识服务平台", "中文资源")
    # landing 是落地页 Page，可在该会话中搜索
    session.shutdown()
"""
from __future__ import annotations

import re

from tju_info_retrieval.browser.session import BrowserSession


class PortalNavigator:
    """天津大学图书馆资源门户导航器。"""

    PORTAL_READY_TIMEOUT_MS = 120000
    _PORTAL_URL_PATTERN = re.compile(
        r"^https?://p\.lib\.tju\.edu\.cn(?:[/:?#]|$)", re.IGNORECASE
    )
    _RESOURCE_NAME_SELECTOR = ".database-list .name, .common-resource-box .name"

    def __init__(self, session: BrowserSession) -> None:
        self._session = session
        self._portal = None

    def open_portal(self):
        """打开天津大学电子资源平台并返回门户页。"""
        self._session.open_tju()
        self._portal = self._session.page()
        self._wait_for_portal_ready()
        return self._portal

    def _wait_for_portal_ready(self) -> None:
        """等待登录回跳结束且 Angular 门户完成首轮资源渲染。"""
        if self._portal is None:
            raise RuntimeError("请先调用 open_portal()")

        try:
            self._portal.wait_for_url(
                self._PORTAL_URL_PATTERN,
                wait_until="domcontentloaded",
                timeout=self.PORTAL_READY_TIMEOUT_MS,
            )
            for category in ("中文资源", "外文资源"):
                self._portal.get_by_text(category, exact=True).first.wait_for(
                    state="visible", timeout=self.PORTAL_READY_TIMEOUT_MS
                )
            self._portal.wait_for_selector(
                self._RESOURCE_NAME_SELECTOR,
                state="attached",
                timeout=self.PORTAL_READY_TIMEOUT_MS,
            )
        except Exception as exc:
            raise RuntimeError(
                "等待天津大学电子资源门户初始化超时"
                f"（当前 URL: {self._portal.url}）"
            ) from exc

    def list_resources(self, category: str = "中文资源") -> list[str]:
        """列出指定分类下的资源名称（去重保序）。"""
        if self._portal is None:
            raise RuntimeError("请先调用 open_portal()")
        self._wait_for_portal_ready()
        self._portal.get_by_text(category, exact=True).first.click()
        names = self._portal.eval_on_selector_all(
            self._RESOURCE_NAME_SELECTOR,
            """els => els.map(e => (e.innerText || '').trim())
                 .filter(t => t && t.length <= 50)""",
        )
        seen: set[str] = set()
        result: list[str] = []
        for n in names:
            if n not in seen:
                seen.add(n)
                result.append(n)
        return result

    def open_resource(self, resource_name: str, category: str = "中文资源"):
        """打开指定资源，返回落地页 Page 对象。

        流程：点击分类 tab → 找到资源卡片 → 点击“进入”
        → ``expect_page`` 等待代理隧道建立 → 处理 502 (reload) → 返回落地页。

        如果资源不存在，抛出 ``ValueError``。
        """
        if self._portal is None:
            raise RuntimeError("请先调用 open_portal()")
        self._wait_for_portal_ready()
        self._portal.get_by_text(category, exact=True).first.click()

        # 检查资源是否存在并获取其名称（含订购后缀等附加信息）
        clicked_name = self._portal.evaluate(
            """(target) => {
                const boxes = document.querySelectorAll('.database-list, .common-resource-box');
                for (const box of boxes) {
                    const nameEl = box.querySelector('.name');
                    const name = (nameEl && nameEl.innerText || '').trim();
                    if (name.includes(target) || name.startsWith(target)) {
                        const span = box.querySelector('span.intoitem2, span[ng-click*="hrefDetail"]');
                        if (span) { span.click(); return name; }
                    }
                }
                return null;
            }""",
            resource_name,
        )
        if not clicked_name:
            raise ValueError(f"资源不存在或未找到: {resource_name}")

        # 等待代理隧道建立（新标签）
        context = self._session.context
        try:
            with context.expect_page(timeout=35000) as page_info:
                pass  # 点击已在 evaluate 中完成
            landing = page_info.value
        except Exception as exc:
            raise RuntimeError(f"等待资源“{resource_name}”新标签超时: {type(exc).__name__}") from exc

        try:
            landing.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        landing.wait_for_timeout(5000)

        # 代理偶发 502，重载一次
        try:
            title = landing.title()
        except Exception:
            title = ""
        if "502" in title:
            try:
                landing.reload(wait_until="domcontentloaded", timeout=30000)
                landing.wait_for_timeout(5000)
            except Exception:
                pass

        return landing
