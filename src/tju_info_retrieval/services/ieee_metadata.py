"""IEEE 详情页二次增强元数据服务（v0.13 Phase 2.4）。

从 IEEE Xplore 原始站详情页提取作者单位（affiliation）。
只做"SearchResult → IEEE 详情页 → affiliation"这一步：

- 不修改 SearchResult 与现有检索流程（普通搜索不访问详情页）；
- 不接入 FilterService / UI（下一阶段再解决"如何利用单位筛选"）；
- 所有失败静默降级：返回 [] 并记录日志，不影响主流程。

实现要点（与 Phase 2.3 PoC 实测结论一致）：
- 必须访问原始站 ieeexplore.ieee.org/document/{id}/（代理隧道详情页会
  卡在 anti-bot interstitial，实测 30s 不跳转）；
- 首次访问可能返回 HTTP 202 "Loading..."，需轮询等待 JS 校验自动跳转；
- 作者单位在点击 "All Authors"（<a> 链接，非 button）展开后出现于
  .authors-accordion-container .author-card 内；
- 单位文本与作者名在同一 author-card 的 innerText 中，解析时剔除作者名。
"""
from __future__ import annotations

import logging
import re
import time

from tju_info_retrieval.browser.session import BrowserSession
from tju_info_retrieval.sources.ieee import ieee_original_url, parse_document_id

logger = logging.getLogger(__name__)

# 机构关键词（无作者链接卡片时的兜底判断，避免把作者名当单位）
_INSTITUTION_RE = re.compile(
    r"Universit|Institute|Laboratory|\bLab\b|College|Department|School\s+of|"
    r"Research|Center|Centre|大学|学院|研究院|实验室"
)


class IeeeMetadataFetcher:
    """IEEE 详情页元数据获取器（当前仅 affiliation）。"""

    # 反爬 interstitial 等待参数（测试可调小）
    SETTLE_TIMEOUT_SECONDS = 45.0
    SETTLE_POLL_SECONDS = 2.0

    def __init__(self, session: BrowserSession) -> None:
        self._session = session

    # ---------- 公共接口 ----------

    def fetch_affiliations(self, detail_url: str) -> list[str]:
        """从 IEEE 详情页提取作者单位列表；任何失败静默返回 []。"""
        try:
            doc_id = parse_document_id(detail_url)
            if not doc_id:
                logger.warning("无法解析 IEEE document id: %r", detail_url)
                return []
            url = ieee_original_url(detail_url)
            if not url:
                logger.warning("无法构造 IEEE 原始站 URL: %r", detail_url)
                return []

            page, owned = self._open_page()
            if page is None:
                logger.warning("无法获取浏览器页面，放弃 affiliation 获取")
                return []
            try:
                if not self._settle(page, url):
                    return []
                self._expand_authors(page)
                return self._extract_affiliations(page)
            finally:
                if owned:
                    try:
                        page.close()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001 - 任何异常静默降级
            logger.exception("IEEE affiliation 获取失败 detail_url=%r", detail_url)
            return []

    # ---------- 内部流程 ----------

    def _open_page(self) -> tuple:
        """打开专用标签页访问详情页；失败回退主页面。返回 (page, owned)。"""
        if not self._session.is_alive():
            try:
                self._session.start()
            except Exception:  # noqa: BLE001
                logger.warning("浏览器会话启动失败")
                return None, False
        try:
            ctx = self._session.context
            return ctx.new_page(), True
        except Exception:  # noqa: BLE001
            logger.warning("无法打开新标签页，回退使用主页面")
            try:
                return self._session.page(), False
            except Exception:  # noqa: BLE001
                return None, False

    def _settle(self, page, url: str) -> bool:
        """访问详情页并等待 anti-bot interstitial 跳过；返回是否就绪。

        页面初始可能返回 202 "Loading..."，JS 校验后自动跳转；
        轮询等待标题不含 "Loading" 且正文就绪，超时返回 False。
        """
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:  # noqa: BLE001 - interstitial 阶段 goto 可能报错，继续轮询
            logger.warning("IEEE 详情页 goto 异常（可能处于 interstitial）")
        try:
            page.wait_for_timeout(4000)
        except Exception:  # noqa: BLE001
            pass
        deadline = time.monotonic() + self.SETTLE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                title = page.title() or ""
                body_len = len(page.inner_text("body") or "")
            except Exception:  # noqa: BLE001
                title, body_len = "", 0
            if "Loading" not in title and body_len > 200:
                return True
            time.sleep(self.SETTLE_POLL_SECONDS)
        logger.warning(
            "IEEE 详情页 interstitial 等待超时（%.1f 秒），放弃", self.SETTLE_TIMEOUT_SECONDS
        )
        return False

    def _expand_authors(self, page) -> None:
        """点击 "All Authors" 展开作者区（<a> 链接，非 button）。"""
        try:
            loc = page.locator("a:has-text('All Authors')").first
            if loc.count() > 0:
                loc.click(timeout=5000)
                page.wait_for_timeout(2500)
                logger.debug("已点击 All Authors")
        except Exception:  # noqa: BLE001
            logger.debug("展开 All Authors 失败（可能无作者区），继续尝试提取")

    def _extract_affiliations(self, page) -> list[str]:
        try:
            cards = page.query_selector_all(".authors-accordion-container .author-card")
            if not cards:
                cards = page.query_selector_all(".author-card")
        except Exception:  # noqa: BLE001
            logger.warning("读取 author-card 失败")
            return []
        affiliations: list[str] = []
        for card in cards:
            aff = self._parse_affiliation(card)
            if aff:
                affiliations.append(aff)
        return affiliations

    # ---------- 解析 ----------

    @classmethod
    def _parse_affiliation(cls, card) -> str | None:
        """从单个 author-card 提取单位文本；无法解析返回 None。"""
        text = ""
        try:
            text = (card.inner_text() or "").strip()
        except Exception:  # noqa: BLE001
            return None
        if not text:
            return None

        has_name_link = False
        try:
            name_link = card.query_selector("a[href*='/author/']")
            if name_link is not None:
                has_name_link = True
                name = (name_link.inner_text() or "").strip()
                if name:
                    if text.startswith(name):
                        text = text[len(name):]
                    else:
                        text = text.replace(name, "", 1)
        except Exception:  # noqa: BLE001
            pass

        text = re.sub(r"\s+", " ", text).strip(" ;；,，")
        if not text:
            return None
        # 无作者链接的卡片：仅当文本含机构关键词时才视为单位，避免把作者名当单位
        if not has_name_link and not _INSTITUTION_RE.search(text):
            return None
        return text
