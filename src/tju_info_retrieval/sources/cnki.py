"""CNKI 数据源适配器。

复用 PoC-1 已真实验证的 selector 与解析逻辑（import 自 poc_cnki_search，
不复制一份），并补充 document_type 提取与 SearchResult 转换。

v0.13 Phase 7：普通/高级双路径。
- 无高级条件（author_name / author_affiliation / start_date / end_date 全空）
  → 保持原普通搜索，行为不变。
- 存在任一高级条件 → 进入高级检索（CNKIAdvancedQueryBuilder 页面操作）；
  高级检索失败不阻断整体搜索，自动回退普通搜索。
"""
from __future__ import annotations

import logging
import re
import time

from poc_cnki_search import (
    RESULT_TABLE_SELECTOR,
    SEARCH_BUTTON_SELECTORS,
    SEARCH_INPUT_SELECTORS,
    extract_row,
    wait_for_result_page,
)

from tju_info_retrieval.browser.session import BrowserSession
from tju_info_retrieval.models.query import RankingMode
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.sources.base import SearchError, SourceAdapter
from tju_info_retrieval.sources.cnki_advanced import (
    CNKIAdvancedQueryBuilder,
    wait_for_advanced_result_page,
)

logger = logging.getLogger(__name__)

# 触发高级检索的 QueryRequest 字段
_ADVANCED_CONDITION_FIELDS = (
    "author_name",
    "author_affiliation",
    "start_date",
    "end_date",
)


def _has_advanced_conditions(query_context) -> bool:
    """QueryRequest 是否携带任一高级检索条件（空值/None 均视为无）。"""
    if query_context is None:
        return False
    return any(
        str(getattr(query_context, field, "") or "").strip()
        for field in _ADVANCED_CONDITION_FIELDS
    )


def _wants_citation_sort(query_context) -> bool:
    """v0.16 Phase B：query_context 是否要求原生被引降序（仅 CITATION 模式）。"""
    return str(
        getattr(query_context, "ranking_mode", "") or ""
    ).upper() == RankingMode.CITATION.value


class CNKIAdapter(SourceAdapter):
    database_name = "CNKI"

    def __init__(self, session: BrowserSession) -> None:
        self._session = session
        # v0.13 Phase 8：最近一次成功高级检索中、来源（CNKI 服务端）已验证
        # 单位条件的 result_id 集合；供 SearchService 写入 source_verified 标记。
        self.source_verified_ids: set[str] = set()

    def search(
        self,
        keyword: str,
        count: int = 10,
        query_context=None,
    ) -> list[SearchResult]:
        """带高级条件的检索入口：有高级条件走高级检索，失败回退普通搜索。

        旧调用 ``search(keyword, count)`` 行为不变（query_context=None
        → 普通搜索）。
        """
        self.source_verified_ids = set()
        citation_sort = _wants_citation_sort(query_context)
        if _has_advanced_conditions(query_context):
            try:
                results = self._advanced_search(
                    keyword, count, query_context, citation_sort=citation_sort
                )
                # 高级检索成功且携带单位条件 → CNKI 已在服务端按 author_affiliation
                # 过滤；记录这些结果的来源验证标记（供 FilterService strict 信任）
                if results and (query_context.author_affiliation or "").strip():
                    self.source_verified_ids = {
                        rid for r in results if (rid := (r.detail_url or r.doi))
                    }
                    # 查询级来源验证状态（QueryRequest 字段，v0.13 Phase 8）
                    query_context.affiliation_source_verified = True
                return results
            except Exception as exc:  # noqa: BLE001 - 高级失败不阻断主流程
                logger.warning("CNKI 高级检索失败，回退普通搜索: %s", exc)
        return self._existing_search(keyword, count, citation_sort=citation_sort)

    def _existing_search(
        self, keyword: str, count: int = 10, citation_sort: bool = False
    ) -> list[SearchResult]:
        """普通搜索（原 search 行为，保持不变）。"""
        page = self._session.page()
        search_input = self._find_search_input(page)
        if search_input is None:
            raise SearchError("未检测到 CNKI 搜索区域（可能登录失效/无授权/验证码/风控）。")
        search_button = self._find_search_button(page)
        if search_button is None:
            raise SearchError("未找到 CNKI 检索按钮。")

        search_input.fill(keyword)
        search_button.click()

        result_page = wait_for_result_page(self._session.context)
        if result_page is None:
            raise SearchError("未检测到 CNKI 结果页。")
        time.sleep(2)
        if citation_sort:
            self._apply_native_citation_sort(result_page)
        results = self.parse_results(result_page, count)
        if not results:
            raise SearchError("结果为空。")
        return results

    def _advanced_search(
        self,
        keyword: str,
        count: int,
        query_context,
        citation_sort: bool = False,
    ) -> list[SearchResult]:
        """高级检索：页面操作由 CNKIAdvancedQueryBuilder 封装。

        v0.13 Phase 8.6：kns8s 高级检索提交成功后 URL 不变（结果在
        AdvSearch SPA 内渲染 table.result-table-list），结果页判定不依赖
        defaultresult URL，改用 wait_for_advanced_result_page（A/B/C
        优先级：结果表格 → 有效数据行 → 统计条状态变化）。
        """
        page = self._session.page()
        builder = CNKIAdvancedQueryBuilder(self._session)
        builder.build(page, keyword, query_context)
        result_page = wait_for_advanced_result_page(self._session.context)
        if result_page is None:
            raise SearchError("未检测到 CNKI 高级检索结果页。")
        time.sleep(2)
        if citation_sort:
            self._apply_native_citation_sort(result_page)
        results = self.parse_results(result_page, count)
        if not results:
            raise SearchError("高级检索结果为空。")
        return results

    def parse_results(self, page, count: int = 10) -> list[SearchResult]:
        """从结果页解析前 count 条（跳过表头行）。"""
        rows = page.query_selector_all(f"{RESULT_TABLE_SELECTOR} tr")
        results: list[SearchResult] = []
        for row in rows[1:]:
            if len(results) >= count:
                break
            data = extract_row(row)
            if not data.get("title"):
                continue
            results.append(
                SearchResult(
                    rank=len(results) + 1,
                    title=data["title"],
                    authors=data["authors"],
                    source=data["source"],
                    year=data["year"],
                    detail_url=data["detail_url"],
                    document_type=self._extract_document_type(row),
                    database=self.database_name,
                    citation_count=self._extract_citation_count(row),
                )
            )
        return results

    @staticmethod
    def _extract_document_type(row) -> str | None:
        td = row.query_selector("td.data")
        if td:
            text = td.inner_text().strip()
            return text or None
        return None

    @staticmethod
    def _extract_citation_count(row) -> int | None:
        """从 CNKI 结果行的被引列提取引用数。

        v0.16 真机校准（docs/v0.16_cnki_citation_probe.md）：被引列 td
        class 为 ``quote``（th 文本「被引」与 td.quote 逐列对位，普通/高级
        两路径一致）；旧实现假设的 ``td.citation`` 在真实 DOM 中不存在。
        值形态：纯文本数字（无链接包裹）；匿名态/无数据时 cell 为空文本。
        """
        td = row.query_selector("td.quote")
        if td:
            text = td.inner_text().strip()
            if text:
                # 兼容可能出现的占位符（"--" 等）与链接包裹形态
                match = re.search(r"(\d+)", text)
                if match:
                    return int(match.group(1))
        return None

    @staticmethod
    def _find_search_input(page):
        for sel in SEARCH_INPUT_SELECTORS:
            loc = page.query_selector(sel)
            if loc:
                return loc
        return None

    @staticmethod
    def _find_search_button(page):
        for sel in SEARCH_BUTTON_SELECTORS:
            loc = page.query_selector(sel)
            if loc:
                return loc
        return None

    # v0.16 Phase B：原生被引降序（docs/v0.16_cnki_native_citation_sort_probe.md）。
    # 排序栏 li（父容器 class 含 "DESC order"），li.DESC.cur 标记当前降序字段；
    # 点击在 升/降序 间 toggle，故先读状态、最多点 2 次。
    _CITE_SORT_STATE_JS = """() => {
        for (const li of document.querySelectorAll('li')) {
            if ((li.innerText || '').trim() !== '被引') continue;
            const cls = String(li.className || '');
            if (cls.includes('cur')) return cls.includes('DESC') ? 'desc' : 'asc';
        }
        return 'none';
    }"""

    _CITE_SORT_CLICK_JS = """() => {
        for (const li of document.querySelectorAll('li')) {
            if ((li.innerText || '').trim() !== '被引') continue;
            if (li.closest('table')) continue;
            li.click();
            return true;
        }
        return false;
    }"""

    def _apply_native_citation_sort(self, page) -> None:
        """确保结果页排序为「被引 降序」（仅 CITATION 模式调用）。

        SPA 原地重查（URL 不变）；失败静默降级（保持默认序候选，
        来源均衡选择器仍按已提取的 citation_count 本地排序兜底）。
        """
        try:
            for _ in range(2):
                state = page.evaluate(self._CITE_SORT_STATE_JS)
                if state == "desc":
                    return
                clicked = page.evaluate(self._CITE_SORT_CLICK_JS)
                if not clicked:
                    return
                page.wait_for_timeout(4000)  # 等待服务端重查 + 表格重渲染
        except Exception as exc:  # noqa: BLE001 - 原生排序失败不影响检索
            logger.warning("CNKI 原生被引排序失败（保持默认序候选）: %s", exc)
