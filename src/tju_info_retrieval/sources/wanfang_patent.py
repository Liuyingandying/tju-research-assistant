"""万方专利数据源 Adapter（v0.17 Phase 2.2-C）。

依据：Phase 2.2-B 真机 PoC（docs/v0.17_patent_source_probe.md、
runtime/v0.17_patent_source_probe/*.json）——全部 selector 与流程均经真机取证，
禁止无证据替换。

能力：
- topic only → 专利普通检索（landing #search-input → /paper → 切「专利」页签）
- person / affiliation 任一出现 → /advanced-search/patent 高级检索：
  - person → 专利-发明/设计人（模糊即可，PoC 59 条）
  - affiliation → 专利-申请/专利权人（模糊即可，PoC 74842 条）
  - 组合按行规划并存（row0 默认「主题」）
- 列表页取 title / applicant / publication_number / application_number /
  date / abstract / 法律状态；inventors + detail_url 由 bounded resolver
  经详情页 og: 前缀 meta 补齐（不伪造缺失字段）。
- readiness：提交后 XHR 内联渲染（URL 不变），goto 时 0 条、无陈旧渲染
  （PoC 13 json）——等待 titles>0 或 结果统计条出现 + loading 消失 + 连续稳定。

与 FilterService 契约（Phase 2.2-A）对齐：inventors = artifact_metadata.inventors
为正式人物字段；SearchResult.authors 仅作镜像（禁止 author=applicant）。
"""
from __future__ import annotations

import logging
import re
import time

from tju_info_retrieval.browser.portal import PortalNavigator
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.sources.base import SearchError, SourceAdapter

logger = logging.getLogger(__name__)

# 数据源身份：与 registry source 名一致（citation_selector 按 database 分组）
DATABASE_NAME = "万方专利"

# ---------- 列表页 selector（PoC 04/12 json 取证） ----------
_TITLE_SEL = ".title-area span.title"
_STATUS_SEL = ".pre-publish, .sync-publish"   # 发明公开 / 发明授权
_TML6_SEL = "span.t-ML6"                       # [0]=专利类型, 后续=号码
_AUTHORS_SEL = "span.authors"                  # 申请人（专利行语义）
_APPLYDATE_SEL = "span.applyDate"              # 申请日：…  公开日：…
_ABSTRACT_SEL = ".abstract-area"
_ID_SEL = ".title-id-hidden"                   # patent_ZL_申请号_公开号_日期
_PATENT_TAB_SEL = "[class*='tab']:text-is('专利')"
_SEARCH_BTN = "button:has-text('检索')"

# ---------- 高级检索（PoC 10 json：字段选项/行结构） ----------
_ADVANCED_URL_PATH = "/advanced-search/patent"
_FIELD_INVENTOR = "专利-发明/设计人"
_FIELD_APPLICANT = "专利-申请/专利权人"
_ROW_FIELD_SEL = ".search-option.field"
_ROW_ITEM_SEL = ".ivu-select-item"
_ROW_INPUT_SEL = "input.ivu-input:visible"
_SUBMIT_SEL = "span.submit-btn"
# 可见行字段（与论文表单同家族结构，PoC 10 json：row0 默认「主题」）
_ROWS_JS = r"""() => {
  const rows = [];
  for (const inp of document.querySelectorAll('input.ivu-input')) {
    if (inp.offsetParent === null) continue;
    let node = inp, field = '';
    for (let k = 0; k < 6 && node; k++) {
      node = node.parentElement;
      if (!node) break;
      const mark = node.querySelector('.search-option.field input[type=hidden][value]');
      if (mark && !/dropdown|not-found/.test(String(node.className || ''))) {
        field = mark.value || '';
        break;
      }
    }
    rows.push({field: field, value: inp.value});
  }
  return rows;
}"""

# ---------- 详情页结构化字段（PoC 15 json 取证：og: 前缀 meta） ----------
_META_INVENTORS = "meta[property='og:article:author']"
_META_TYPE = "meta[property='og:article:section']"
_META_IPC = "meta[property='og:article:tag']"
_META_PUBLISHED = "meta[property='og:article:published_time']"

# ---------- readiness / resolver 参数 ----------
_RESULT_TIMEOUT_S = 30.0
_STABLE_SAMPLES = 2
_STABLE_INTERVAL_MS = 250
_LOADING_JS = """() => {
    const v = el => !!(el && (el.offsetWidth || el.offsetHeight
        || el.getClientRects().length));
    return Array.from(document.querySelectorAll(
        '.ivu-spin, .ivu-spin-fix, .loading, [class*="loading"], [class*="Loading"]'))
        .some(v);
}"""
# resolver 上限：不随 CITATION candidate_quota(≤50) 放大（任务 §七）
_DETAIL_MAX_ITEMS = 8
_DETAIL_PER_ITEM_S = 5.0
_DETAIL_BUDGET_S = 30.0
_CACHE_MAX = 500
_GLOBAL_CACHE: dict[str, dict] = {}


def parse_patent_list_row(row: dict) -> dict:
    """把列表页一行 DOM 取证结果映射为专利字段（纯函数，字符串容错）。"""
    title = str(row.get("title") or "").strip()
    id_hidden = str(row.get("id_hidden") or "").strip()
    # id_hidden: patent_ZL_申请号_公开号_日期（PoC 04 json 取证）
    parts = id_hidden.split("_")
    application_number = parts[2] if len(parts) >= 5 else None
    publication_number = parts[3] if len(parts) >= 5 else None
    date_code = parts[4] if len(parts) >= 5 else None
    tml6 = [str(t).strip() for t in (row.get("tml6") or [])]
    applicant = str(row.get("applicant") or "").strip() or None
    # applyDate 文本："申请日：2026-04-23   公开日：2026-07-17"
    apply_text = str(row.get("apply_date") or "")
    m_app = re.search(r"申请日[:：]\s*(\d{4}[-\d]*)", apply_text)
    m_pub = re.search(r"公开日[:：]\s*(\d{4}[-\d]*)", apply_text)
    raw_date = (m_pub.group(1) if m_pub else None) or (m_app.group(1) if m_app else None)
    if not raw_date and date_code:
        raw_date = date_code
    abstract = str(row.get("abstract") or "")
    if abstract.startswith("摘要："):
        abstract = abstract[3:].strip()
    return {
        "title": title,
        "document_type": str(row.get("status") or "").strip() or (
            tml6[0] if tml6 else None),
        "applicant": applicant,
        "publication_number": publication_number or (tml6[1] if len(tml6) > 1 else None),
        "application_number": application_number,
        "date": raw_date,
        "abstract": abstract or None,
    }


def parse_patent_detail_fields(page) -> dict:
    """从专利详情页解析结构化字段（og: 前缀 meta，PoC 15 json 取证）。

    任何字段失败均为 None/空，不抛出；inventors 为逗号分隔值拆分。
    """
    out: dict = {
        "inventors": [], "patent_type": None, "ipc": [],
        "published_time": None,
    }
    try:
        content = page.get_attribute(_META_INVENTORS, "content")
        if content:
            out["inventors"] = [n.strip() for n in content.split(",") if n.strip()]
    except Exception:  # noqa: BLE001
        pass
    try:
        t = page.get_attribute(_META_TYPE, "content")
        out["patent_type"] = (t or "").strip() or None
    except Exception:  # noqa: BLE001
        pass
    try:
        tag = page.get_attribute(_META_IPC, "content")
        if tag:
            out["ipc"] = [c.strip() for c in tag.split(",") if c.strip()]
    except Exception:  # noqa: BLE001
        pass
    try:
        p = page.get_attribute(_META_PUBLISHED, "content")
        out["published_time"] = (p or "").strip() or None
    except Exception:  # noqa: BLE001
        pass
    return out


class WanfangPatentMetadataResolver:
    """专利发明人/详情页 bounded resolver（v0.17 Phase 2.2-C）。

    模式对齐 WanfangMetadataResolver：缓存 / 单项超时 / 总预算 / 失败降级。
    专利字段独立（inventors / ipc / detail_url），不写入论文 MetadataRecord。

    约束（任务 §七）：
    - 硬上限 max_items（默认 8，不随 candidate_quota 放大）；
    - 单项 ≤5s、整体 ≤30s；
    - 详情失败跳过，不导致整次搜索失败；
    - 未解析 inventors → 保持缺失/空，不伪造。
    """

    def __init__(
        self,
        page,
        cache: dict | None = None,
        max_items: int = _DETAIL_MAX_ITEMS,
        per_item_timeout_s: float = _DETAIL_PER_ITEM_S,
        total_budget_s: float = _DETAIL_BUDGET_S,
    ) -> None:
        self._page = page
        self._cache = cache if cache is not None else _GLOBAL_CACHE
        self._max_items = max_items
        self._per_item = per_item_timeout_s
        self._budget = total_budget_s

    @staticmethod
    def _cache_key(result: SearchResult) -> str:
        return re.sub(r"[\s\u3000]+", "", (result.title or "")).lower()

    def enrich(self, results: list[SearchResult]) -> int:
        """逐条打开详情补齐 inventors/ipc/detail_url；返回成功条数，不抛出。"""
        opened = 0
        deadline = time.monotonic() + self._budget
        for i, result in enumerate(results):
            if i >= self._max_items:
                break
            if time.monotonic() >= deadline:
                logger.warning("万方专利详情 enrichment 预算耗尽，跳过剩余")
                break
            cache_key = self._cache_key(result)
            if cache_key in self._cache:
                self._apply(result, self._cache[cache_key])
                opened += 1
                continue
            meta = self._resolve_one(i)
            if meta:
                self._cache[cache_key] = meta
                if len(self._cache) > _CACHE_MAX:
                    self._cache.pop(next(iter(self._cache)))
                self._apply(result, meta)
                opened += 1
        return opened

    def _resolve_one(self, index: int) -> dict | None:
        """点击第 index 条标题 → 新标签 → 解析 → 关闭；失败返回 None。"""
        try:
            import time as _t

            with self._page.context.expect_page(timeout=15000) as page_info:
                self._page.locator(_TITLE_SEL).nth(index).click()
            detail = page_info.value
            try:
                detail.wait_for_load_state(
                    "domcontentloaded", timeout=self._per_item * 1000)
            except Exception:  # noqa: BLE001
                pass
            detail.wait_for_timeout(1500)
            meta = parse_patent_detail_fields(detail)
            meta["detail_url"] = detail.url
            _t.sleep(0)
            try:
                detail.close()
            except Exception:  # noqa: BLE001
                pass
            return meta if (meta["inventors"] or meta["ipc"]) else None
        except Exception as exc:  # noqa: BLE001 - 详情失败降级
            logger.warning("万方专利详情解析失败（降级）: %s", exc)
            return None

    @staticmethod
    def _apply(result: SearchResult, meta: dict) -> None:
        art = dict(result.artifact_metadata or {})
        if meta.get("inventors") and not art.get("inventors"):
            art["inventors"] = meta["inventors"]
        if not art.get("ipc") and meta.get("ipc"):
            art["ipc"] = meta["ipc"]
        if not art.get("date") and meta.get("published_time"):
            art["date"] = meta["published_time"]
        result.artifact_metadata = art
        if meta.get("detail_url") and not result.detail_url:
            result.detail_url = meta["detail_url"]
        if meta.get("inventors") and not result.authors:
            # authors 仅作 UI 通用列镜像（正式语义在 artifact_metadata.inventors）
            result.authors = list(meta["inventors"])


class WanfangPatentAdapter(SourceAdapter):
    """万方专利数据源适配器（v0.17 Phase 2.2-C）。"""

    database_name = DATABASE_NAME

    def __init__(self, session) -> None:
        self._session = session
        self._portal = PortalNavigator(session)

    # ---------- 入口 ----------

    def search(self, keyword: str, count: int = 10, query_context=None) -> list[SearchResult]:
        page = self._portal.open_portal()
        page = self._portal.open_resource("万方数据知识服务平台", "中文资源")
        try:
            page.wait_for_selector("#search-input", timeout=30000)
        except Exception as exc:  # noqa: BLE001
            raise SearchError(f"万方专利：资源页未就绪（{exc}）") from exc
        page.wait_for_timeout(2000)

        # 与论文 adapter 同源路由：原始关键词 = research_direction（OR 表达式
        # 对万方专利无意义）；Person/affiliation 任一出现 → 高级检索
        original = str(
            getattr(query_context, "research_direction", "") or ""
        ).strip() or keyword
        person = ""
        affiliation = ""
        if query_context is not None:
            person = str(
                getattr(query_context, "author_name", "") or ""
            ).strip()
            affiliation = str(
                getattr(query_context, "author_affiliation", "") or ""
            ).strip()

        if person or affiliation:
            results = self._advanced_search(
                page, original, person, affiliation, count,
            )
        else:
            results = self._plain_search(page, original, count)

        # inventors / detail_url bounded enrichment（仅前 max_items 条）
        try:
            WanfangPatentMetadataResolver(page).enrich(results)
        except Exception as exc:  # noqa: BLE001 - enrichment 失败不影响结果
            logger.warning("万方专利 enrichment 异常（降级）: %s", exc)
        return results

    # ---------- 普通检索（topic only） ----------

    def _plain_search(self, page, original: str, count: int) -> list[SearchResult]:
        if not original:
            raise SearchError("万方专利：未提供主题检索词")
        page.locator("#search-input").clear()
        page.fill("#search-input", original)
        page.locator(_SEARCH_BTN).first.click()
        self._wait_plain_results(page)
        # 切「专利」页签（PoC：landing 检索 → /paper → 专利页签 → /patent）
        try:
            page.locator(_PATENT_TAB_SEL).first.click(timeout=8000)
        except Exception as exc:  # noqa: BLE001
            raise SearchError(f"万方专利：结果页无「专利」页签（{exc}）") from exc
        self._wait_plain_results(page, patent_route=True)
        results = self._parse_list(page, count)
        if not results:
            raise SearchError(
                f"万方专利检索结果为空（关键词：{original}）。"
            )
        return results

    def _wait_plain_results(self, page, patent_route: bool = False) -> None:
        deadline = time.monotonic() + _RESULT_TIMEOUT_S
        stable = 0
        last: tuple | None = None
        while time.monotonic() < deadline:
            try:
                titles = page.locator(_TITLE_SEL).count()
                loading = page.evaluate(_LOADING_JS) is True
                on_route = "/patent" in page.url if patent_route else True
                if on_route and not loading and (titles > 0 or self._has_count_text(page)):
                    sig = (titles, self._count_text(page))
                    stable = stable + 1 if sig == last else 1
                    last = sig
                    if stable >= _STABLE_SAMPLES:
                        return
            except Exception:  # noqa: BLE001 - 页面瞬时不可用
                stable = 0
            page.wait_for_timeout(_STABLE_INTERVAL_MS)
        raise SearchError("万方专利：普通检索结果未就绪（超时）")

    # ---------- 高级检索（person / affiliation 任一出现） ----------

    def _advanced_search(
        self,
        page,
        original: str,
        person: str,
        affiliation: str,
        count: int,
    ) -> list[SearchResult]:
        # 域名发现：普通搜索导航（空主题时用人名/单位，仅定位智搜域名）
        nav_kw = original or person or affiliation
        page.locator("#search-input").clear()
        page.fill("#search-input", nav_kw)
        page.locator(_SEARCH_BTN).first.click()
        page.wait_for_timeout(4000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        if "/paper" not in page.url:
            raise SearchError("万方专利：未能进入检索结果页（智搜域名未知）")
        host_base = page.url.split("/paper")[0]

        page.goto(f"{host_base}{_ADVANCED_URL_PATH}",
                  wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(1500)

        # 行规划（PoC 10 json：row0 默认「主题」；发明/设计人、申请/专利权人
        # 模糊即可，无需论文作者那样切精确）
        row_cursor = 1 if original else 0
        if person:
            self._switch_row_field(page, row_cursor, _FIELD_INVENTOR)
            row_cursor += 1
        if affiliation:
            self._switch_row_field(page, row_cursor, _FIELD_APPLICANT)
            row_cursor += 1

        rows = page.evaluate(_ROWS_JS) or []
        filled: list[tuple[int, str, str]] = []
        if original:
            idx = self._row_by_field(rows, "主题")
            if idx is None:
                raise SearchError("万方专利：未定位到主题行")
            filled.append((idx, "主题", original))
        if person:
            idx = self._row_by_field(rows, _FIELD_INVENTOR)
            if idx is None:
                raise SearchError("万方专利：未定位到发明/设计人行")
            filled.append((idx, _FIELD_INVENTOR, person))
        if affiliation:
            idx = self._row_by_field(rows, _FIELD_APPLICANT)
            if idx is None:
                raise SearchError("万方专利：未定位到申请/专利权人行")
            filled.append((idx, _FIELD_APPLICANT, affiliation))

        inputs = page.locator(_ROW_INPUT_SEL)
        for idx, _f, value in filled:
            inputs.nth(idx).press_sequentially(value, delay=20)
        page.wait_for_timeout(400)

        # 提交前回读校验（防空表单）
        rows = page.evaluate(_ROWS_JS) or []
        if filled and len(rows) <= max(idx for idx, _f, _v in filled):
            raise SearchError("万方专利高级检索输入未就绪（iview 模型未同步），已中止提交")
        for idx, _f, value in filled:
            if value not in (rows[idx].get("value") or ""):
                raise SearchError("万方专利高级检索输入未就绪（iview 模型未同步），已中止提交")

        page.locator(_SUBMIT_SEL).first.click(timeout=5000)
        self._wait_advanced_results(page)
        results = self._parse_list(page, count)
        if not results:
            raise SearchError("万方专利高级检索结果为空（结果页已渲染但无可解析条目）")
        return results

    def _wait_advanced_results(self, page) -> None:
        """提交后等待内联渲染：titles>0 或 统计条出现 + loading 消失 + 连续稳定。

        PoC 13 json：goto 时 0 条、提交后 XHR 内联渲染（URL 不变）、无陈旧渲染。
        真实 0 条 = 统计条出现但 titles=0，不无限等待。
        """
        deadline = time.monotonic() + _RESULT_TIMEOUT_S
        stable = 0
        last: tuple | None = None
        while time.monotonic() < deadline:
            try:
                titles = page.locator(_TITLE_SEL).count()
                loading = page.evaluate(_LOADING_JS) is True
                count_text = self._count_text(page)
                rendered = (titles > 0 or bool(count_text)) and not loading
                if rendered:
                    sig = (titles, count_text)
                    stable = stable + 1 if sig == last else 1
                    last = sig
                    if stable >= _STABLE_SAMPLES:
                        return
            except Exception:  # noqa: BLE001
                stable = 0
            page.wait_for_timeout(_STABLE_INTERVAL_MS)
        raise SearchError(
            "万方专利高级检索：结果未就绪或渲染超时"
            f"（titles={titles}, loading={loading}）"
        )

    # ---------- 列表解析 ----------

    def _parse_list(self, page, count: int) -> list[SearchResult]:
        raw = page.evaluate(
            """(n) => Array.from(document.querySelectorAll('.normal-list'))
            .slice(0, n).map(row => ({
                title: (row.querySelector('.title-area span.title') || {})
                    .innerText || '',
                status: (row.querySelector('.pre-publish, .sync-publish') || {})
                    .innerText || '',
                tml6: Array.from(row.querySelectorAll('span.t-ML6'))
                    .map(e => (e.innerText || '').trim()),
                applicant: (row.querySelector('span.authors') || {})
                    .innerText || '',
                apply_date: (row.querySelector('span.applyDate') || {})
                    .innerText || '',
                abstract: (row.querySelector('.abstract-area') || {})
                    .innerText || '',
                id_hidden: (row.querySelector('.title-id-hidden') || {})
                    .innerText || '',
            }))""",
            count,
        )
        raw = raw if isinstance(raw, list) else []
        results: list[SearchResult] = []
        for i, row in enumerate(raw):
            fields = parse_patent_list_row(row)
            if not fields["title"]:
                continue
            year = None
            if fields["date"]:
                ym = re.search(r"\b((?:19|20)\d{2})\b", fields["date"])
                year = ym.group(1) if ym else None
            results.append(
                SearchResult(
                    rank=len(results) + 1,
                    title=fields["title"],
                    authors=[],  # inventors 由 resolver 镜像；不镜像 applicant
                    source=fields["applicant"],
                    year=year,
                    detail_url=None,  # 由 resolver 打开详情后回填
                    document_type=fields["document_type"],
                    database=DATABASE_NAME,
                    abstract=fields["abstract"],
                    artifact_type="patent",
                    artifact_metadata={
                        "inventors": [],
                        "applicant": fields["applicant"],
                        "publication_number": fields["publication_number"],
                        "application_number": fields["application_number"],
                        "date": fields["date"],
                    },
                    citation_count=None,
                )
            )
        return results

    # ---------- 表单 / 状态辅助 ----------

    @staticmethod
    def _switch_row_field(page, row_index: int, field: str) -> None:
        """切换第 row_index 个可见行的字段下拉。

        真机取证（runtime/v0.17_patent_source_probe/17_patent_field_visibility.json）：
        专利高级表单 DOM 中 .search-option.field 共 9 个——前 3 个可见
        （主题/题名/摘要），后 6 个为隐藏「推荐检索词」预设行。必须 :visible
        过滤后再按 nth(i) 定位，否则 nth(1)/nth(2) 会命中隐藏行。
        """
        trigger = page.locator(f"{_ROW_FIELD_SEL}:visible").nth(row_index)
        try:
            trigger.click(timeout=5000)
            page.wait_for_timeout(900)
            option = trigger.locator(
                f"{_ROW_ITEM_SEL}:text-is('{field}')"
            ).first
            if option.count() == 0:
                raise SearchError(f"万方专利：字段下拉无选项: {field}")
            option.click(timeout=5000)
            page.wait_for_timeout(700)
        except SearchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SearchError(f"万方专利：字段切换为 {field} 失败（{exc}）") from exc

    @staticmethod
    def _row_by_field(rows: list[dict], field: str) -> int | None:
        """按字段名定位可见行。

        真机取证的行标记存储形态不统一（16/18 json）：
        - 专利-发明/设计人 → 存「发明/设计人」；
        - 专利-申请/专利权人 → 存「专利权人」；
        - 主题/题名/摘要 → 原名。
        三级匹配：精确 → 去「专利-」前缀短名 → 双向包含（申请/专利权人 ↔ 专利权人）。
        """
        short = field.split("-", 1)[-1] if "-" in field else field
        for i, r in enumerate(rows):
            got = r.get("field") or ""
            if got == field or got == short:
                return i
        for i, r in enumerate(rows):
            got = r.get("field") or ""
            if got and (got in field or field in got):
                return i
        return None

    @staticmethod
    def _count_text(page) -> str:
        try:
            body = page.inner_text("body") or ""
        except Exception:  # noqa: BLE001
            return ""
        m = re.search(r"找到([\d,]+)条", body) or re.search(r"共\s*([\d,]+)\s*条", body)
        return m.group(0) if m else ""

    @staticmethod
    def _has_count_text(page) -> bool:
        return bool(WanfangPatentAdapter._count_text(page))