"""IEEE Xplore 数据源 Adapter（v1）。

通过 PortalNavigator 获取代理落地页，使用 input[type=search] 搜索，
解析第一页结果：title / authors / year / venue / url / document_type。

边界：单次搜索、单页结果、低频、不下载全文、不进入详情页。
"""
from __future__ import annotations

import re
import time

from tju_info_retrieval.browser.portal import PortalNavigator
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.sources.base import SearchError, SourceAdapter

# 年份正则
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
# 文献类型匹配（含期刊页使用的 Magazine Article 标签）
_DOC_TYPE_RE = re.compile(
    r"Conference Paper|Magazine Article|Journals? & Magazines|Early Access|Standards|Books|Courses"
)
# 会议/期刊名称（年份到元数据标签之间的文本，仅作回退）
_VENUE_RE = re.compile(r"\d{4}\s+(.+?)(?:\s*(?:Year:|Conference:|Journal:|$))")
# IEEE document id：匹配 /document/{纯数字id}（相对路径或任意代理前缀均可）
_DOCUMENT_ID_RE = re.compile(r"/document/(\d+)")


def compose_ieee_query(keyword: str, author_name: str) -> str:
    """组合 IEEE 检索串：author 字段语法 + 主题 Boolean（v0.17 Phase 1）。

    真机取证（runtime/v017_probe/ieee_author_probe_report.json、
    ieee_author_combo_probe.json）：官方搜索框原生解释 fielded 语法——
    - 仅作者：``("Authors":"Xiaodong Feng")`` → 结果按作者过滤（3/3 命中）；
    - 作者+主题：``("Authors":"Xiaodong Feng") AND (terahertz OR THz)``
      → 作者与主题双条件同时生效（4/4 命中）。
    中文姓名不擅自拼音化：原样进入 author 语法，无命中即诚实降级
    （真机验证中文姓名返回 0 条）。
    """
    person = (author_name or "").strip().replace('"', " ").strip()
    topic = (keyword or "").strip()
    if not person:
        return topic
    author_part = f'("Authors":"{person}")'
    if not topic:
        return author_part
    return f"{author_part} AND {topic}"


def parse_document_id(detail_url: str | None) -> str | None:
    """从 IEEE detail_url 提取 document id；相对路径与完整代理 URL 均支持。

    无法解析（空值、非 IEEE 路径、id 非数字）时返回 None。
    """
    if not detail_url:
        return None
    matched = _DOCUMENT_ID_RE.search(detail_url)
    return matched.group(1) if matched else None


def ieee_original_url(detail_url: str | None) -> str | None:
    """根据 detail_url 重建 IEEE 原始站论文页 URL；无法解析时返回 None。

    校园代理页面可能因 AWS WAF 白屏，原始站 URL 提供稳定的备用入口。
    """
    document_id = parse_document_id(detail_url)
    if document_id is None:
        return None
    return f"https://ieeexplore.ieee.org/document/{document_id}/"


class IeeeAdapter(SourceAdapter):
    database_name = "IEEE Xplore"

    def __init__(self, session) -> None:
        self._session = session
        self._portal = PortalNavigator(session)

    def search(self, keyword: str, count: int = 10, query_context=None) -> list[SearchResult]:
        page = self._portal.open_portal()
        page = self._portal.open_resource("IEEE/IET Electronic Library", "外文资源")
        page.wait_for_timeout(5000)

        # v0.17 Phase 1：author_name 非空时使用官方 author 字段语法
        # （("Authors":"姓名")），人员姓名不当普通全文关键词。
        author_name = ""
        if query_context is not None:
            author_name = str(
                getattr(query_context, "author_name", "") or ""
            ).strip()
        keyword = compose_ieee_query(keyword, author_name)

        search_input = page.locator("input[type=search]").first
        search_input.clear()
        search_input.fill(keyword)
        search_input.press("Enter")

        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(5000)

        # v0.16 Phase B：高引用模式原生被引降序
        # （docs/v0.16_ieee_native_citation_sort_probe.md：Sort By →
        # "Most Cited By Papers"，JS 点击绕过 Angular 弹层可见性问题）
        if str(
            getattr(query_context, "ranking_mode", "") or ""
        ).upper() == "CITATION":
            self._apply_native_citation_sort(page)

        results = self._parse_results(page, count)
        if not results:
            raise SearchError(
                f"IEEE Xplore 检索结果为空（关键词：{keyword}）。"
                "可能原因：页面未正常加载或验证码。"
            )
        return results

    # v0.16 Phase B：原生被引降序（探针实测：Sort By 下拉选择
    # "Most Cited By Papers" 后结果严格按 Cited by 降序，匿名可用）
    _CITATION_TOGGLE_JS = """() => {
        const btn = document.querySelector(
            'div[ngbdropdown] button[ngbdropdowntoggle]');
        if (!btn) return false;
        btn.click();
        return true;
    }"""

    _CITATION_PICK_JS = """() => {
        for (const b of document.querySelectorAll(
            'button.dropdown-item.filter-popover-option')) {
            if ((b.innerText || '').trim() === 'Most Cited By Papers') {
                b.click();
                return true;
            }
        }
        return false;
    }"""

    def _apply_native_citation_sort(self, page) -> None:
        """选择官方 "Most Cited By Papers" 排序；失败静默（保持相关性序候选）。"""
        try:
            opened = page.evaluate(self._CITATION_TOGGLE_JS)
            if not opened:
                return
            page.wait_for_timeout(1500)
            picked = page.evaluate(self._CITATION_PICK_JS)
            if picked:
                page.wait_for_timeout(5000)  # 等待服务端重查 + 列表重渲染
        except Exception as exc:  # noqa: BLE001 - 原生排序失败不影响检索
            logger.warning("IEEE 原生被引排序失败（保持默认序候选）: %s", exc)

    @staticmethod
    def _parse_results(page, count: int = 10) -> list[SearchResult]:
        # 结构化 DOM 提取 + 整段文本正则回退：
        # - venue：优先 .description 内首个 <a>；缺失时回退整段正则
        # - year / document_type：优先 .publisher-info-container；缺失时回退整段正则
        raw = page.evaluate(
            """(maxCount) => {
            const items = document.querySelectorAll('.List-results-items .result-item');
            return Array.from(items).slice(0, maxCount).map(item => {
                const titleEl = item.querySelector('h3 a.fw-bold');
                const authorEls = item.querySelectorAll('.author a');
                const allText = item.innerText || '';

                // venue：结构化优先
                const descEl = item.querySelector('.description');
                const descVenueEl = descEl ? descEl.querySelector('a') : null;
                let venue = descVenueEl ? descVenueEl.innerText.trim() : null;
                if (!venue) {
                    const venueMatch = allText.match(/\\d{4}\\s+(.+?)(?:\\s*(?:Year:|Conference:|Journal:|$))/);
                    venue = venueMatch ? venueMatch[1].trim() : null;
                }

                // year / document_type：结构化优先
                const pubInfo = item.querySelector('.publisher-info-container');
                let year = null;
                let document_type = null;
                if (pubInfo) {
                    const pubText = pubInfo.innerText || '';
                    const yearMatch = pubText.match(/Year:\\s*(20\\d{2})/);
                    year = yearMatch ? yearMatch[1] : null;
                    const docMatch = pubText.match(
                        /Conference Paper|Magazine Article|Journals? & Magazines|Early Access|Standards|Books|Courses/
                    );
                    document_type = docMatch ? docMatch[0] : null;
                }
                if (!year) {
                    const yearMatch = allText.match(/\\b(20\\d{2})\\b/);
                    year = yearMatch ? yearMatch[1] : null;
                }
                if (!document_type) {
                    const docMatch = allText.match(
                        /Conference Paper|Magazine Article|Journals? & Magazines|Early Access|Standards|Books|Courses/
                    );
                    document_type = docMatch ? docMatch[0] : null;
                }

                // citation（v0.16 Phase A4）：语义匹配 item 文本中的
                // "Cited by ... Papers (N)"（真实 DOM：Cited by: Papers (329)）。
                // 不依赖 class（Angular 渲染 class 易变）；限窗 80 字符并锚定
                // "Papers (" 后缀，避免误取 item 内其它括号数字。
                let citation = null;
                const citeMatch = allText.match(
                    /Cited\\s+by[\\s\\S]{0,80}?Papers\\s*\\((\\d+)\\)/i
                );
                if (citeMatch) citation = parseInt(citeMatch[1], 10);

                return {
                    title: titleEl ? titleEl.innerText.trim() : null,
                    url: titleEl ? titleEl.href : null,
                    authors: Array.from(authorEls).map(a => a.innerText.trim()),
                    year: year,
                    venue: venue,
                    document_type: document_type,
                    citation: citation,
                };
            });
        }""",
            count,
        )
        results: list[SearchResult] = []
        for i, r in enumerate(raw):
            if not r.get("title"):
                continue
            raw_citation = r.get("citation")
            results.append(
                SearchResult(
                    rank=i + 1,
                    title=r["title"],
                    authors=r.get("authors") or [],
                    source=r.get("venue"),
                    year=r.get("year"),
                    detail_url=r.get("url"),
                    document_type=r.get("document_type"),
                    database="IEEE Xplore",
                    venue=r.get("venue"),
                    citation_count=(
                        int(raw_citation) if raw_citation is not None else None
                    ),
                )
            )
        return results