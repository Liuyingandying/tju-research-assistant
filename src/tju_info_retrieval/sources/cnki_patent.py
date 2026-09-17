"""CNKI 专利数据源 Adapter（v0.17 Phase 2.2-F）。

依据：Phase 2.2-E 真机 PoC + 2.2-F 预编码探针（docs/v0.17_cnki_patent_source_probe.md、
runtime/v0.17_cnki_patent_probe/*.json）——URL 参数、selector 均经真机取证。

搜索语义（URL 字段编码 korder，真机验证）：
- topic      → korder=SU
- inventor   → korder=AU（结果发明人列可用于本地验证）
- applicant  → korder=SQR（结果申请人列可用于本地验证）

组合语义（探针 14-16 json：无官方低成本多条件入口；"结果中检索"输入框与
主搜索框同 id，自动化不可稳定区分）→ **bounded local intersection**：
以最选择性字段为基搜索，再用列表页元数据（标题/发明人/申请人列，均列表可得、
无需开详情）本地验证其余条件——真实 AND，不伪装服务端 AND，有硬上限。

列表页 5 核心字段（探针 05 json）：title(inventors)/applicant/申请日/公开日/
detail_url 均列表可得；publication_number/abstract/IPC 由 bounded 详情
resolver 补齐（详情 selector 探针 17 json：span.rowtit 标签 + 摘要 body 文本）。

CAPTCHA/安全验证（探针 06/17 json：详情页含"安全验证/拖动"元素，未阻断内容）：
详情打开发现该元素时停止该条 enrichment、保留列表数据、日志记录，不交互不绕过。
"""
from __future__ import annotations

import logging
import re
import time
import urllib.parse

from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.sources.base import SearchError, SourceAdapter

logger = logging.getLogger(__name__)

DATABASE_NAME = "CNKI专利"
_SEARCH_URL = ("https://kns.cnki.net/kns8s/search?classid=VUDIXAIY"
               "&kw={kw}&korder={korder}&language=CHS&uniplatform=NZKPT")
_KORDER_TOPIC = "SU"
_KORDER_INVENTOR = "AU"
_KORDER_APPLICANT = "SQR"

# 列表页 selector（探针 05 json 取证）
_ROW_SELECTOR = "table.result-table-list tr"
_TITLE_SEL = "td.name a.fz14"
_STATUS_SEL = "td.name b.marktip"
_INVENTOR_SEL = "td.inventor a"
_APPLICANT_SEL = "td.applicant"
_DATE_SEL = "td.date"

# 详情页字段（探针 17 json：span.rowtit 行标签，值在同容器文本）
_ROW_LABELS = ("申请(专利)号", "授权公告号", "公开(公告)号", "主分类号")
_CAPTCHA_MARKERS = ("安全验证", "拖动", "验证码")
_ABSTRACT_RE = re.compile(r"摘要[:：]\s*([^\n]{10,})")

# resolver 参数
_DETAIL_MAX_ITEMS = 8
_DETAIL_PER_ITEM_S = 5.0
_DETAIL_BUDGET_S = 30.0
_CACHE_MAX = 300
_GLOBAL_CACHE: dict[str, dict] = {}
_RESULT_TIMEOUT_S = 30.0
_STABLE_SAMPLES = 2
_STABLE_INTERVAL_MS = 250
_COUNT_RE = re.compile(r"(\d[\d,]*)\s*条")
_LIST_PAGE_SIZE = 20
_INTERSECTION_MAX_PAGES = 5
_INTERSECTION_MAX_CANDIDATES = 100


def parse_detail_row(text: str) -> str:
    """span.rowtit 行容器文本 → 值（"授权公告号： CN118081733B" → "CN118081733B"）。

    按行锚定（re.MULTILINE）：详情页内每字段自成一行（真机 06 json 取证），
    值取标签后至行尾，避免跨行吞并后续字段。
    """
    m = re.search(r"[:：]\s*([^\n]+?)\s*$", (text or "").strip(), re.MULTILINE)
    return m.group(1).strip() if m else ""


def parse_detail_fields(page_text: str) -> dict:
    """详情页文本 → {application_number, publication_number, ipc}（容错）。

    字段行经 span.rowtit 取证后以其文本特征匹配；任何字段缺失为空，不抛出。
    """
    out: dict = {"application_number": "", "publication_number": "", "ipc": ""}
    lines = re.split(r"\n+", page_text)
    for idx, line in enumerate(lines):
        line = line.strip()
        if line.startswith("申请(专利)号") and not out["application_number"]:
            out["application_number"] = parse_detail_row(line)
        elif line.startswith(("授权公告号", "公开(公告)号")) \
                and not out["publication_number"]:
            out["publication_number"] = parse_detail_row(line)
        elif line.startswith("主分类号") and not out["ipc"]:
            out["ipc"] = parse_detail_row(line)
    return out


def parse_detail_abstract(page_text: str) -> str:
    m = _ABSTRACT_RE.search(page_text)
    if not m:
        return ""
    return m.group(1).strip() or ""


class CnkiPatentMetadataResolver:
    """CNKI 专利详情 bounded resolver（公开号/申请号/IPC/摘要）。

    直接按 detail_url goto（列表链接直取，无需点击捕获）；CAPTCHA 停止该条。
    """

    def __init__(
        self,
        context,
        cache: dict | None = None,
        max_items: int = _DETAIL_MAX_ITEMS,
        per_item_timeout_s: float = _DETAIL_PER_ITEM_S,
        total_budget_s: float = _DETAIL_BUDGET_S,
    ) -> None:
        self._context = context
        self._cache = cache if cache is not None else _GLOBAL_CACHE
        self._max_items = max_items
        self._per_item = per_item_timeout_s
        self._budget = total_budget_s

    def enrich(self, results: list[SearchResult]) -> int:
        """为前 max_items 个缺详情字段的结果补公开号/申请号/IPC/摘要。
        返回成功条数；CAPTCHA/失败降级，不抛出。"""
        opened = 0
        deadline = time.monotonic() + self._budget
        for i, result in enumerate(results):
            if i >= self._max_items:
                logger.warning("CNKI专利详情 enrichment 达到上限 %s", self._max_items)
                break
            if time.monotonic() >= deadline:
                logger.warning("CNKI专利详情 enrichment 预算耗尽")
                break
            url = getattr(result, "detail_url", None)
            if not url:
                continue
            if url in self._cache:
                self._apply(result, self._cache[url])
                opened += 1
                continue
            meta = self._resolve_one(url)
            if meta:
                if len(self._cache) > _CACHE_MAX:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[url] = meta
                self._apply(result, meta)
                opened += 1
        return opened

    def _resolve_one(self, url: str) -> dict | None:
        page = None
        try:
            page = self._context.new_page()
            page.goto(url, wait_until="domcontentloaded",
                      timeout=self._per_item * 1000)
            page.wait_for_timeout(2000)
            text = page.inner_text("body") or ""
            meta = parse_detail_fields(text)
            meta["abstract"] = parse_detail_abstract(text)
            # 安全验证/拖动为休眠腾讯验证码元素（探针 18 json：每详情页均存在
            # id=captcha-element，但内容完全可读；从不与其交互/绕过）。
            # 仅当所需字段不可得 且 标记存在 → 判定真阻断（降级）。
            if not (meta.get("publication_number") or meta.get("abstract")):
                if any(marker in text for marker in _CAPTCHA_MARKERS):
                    logger.warning(
                        "CNKI专利详情 enrichment blocked（安全验证元素，字段不可得）")
                return None
            return meta
        except Exception as exc:  # noqa: BLE001 - 详情失败降级
            logger.warning("CNKI专利详情解析失败（降级）: %s", exc)
            return None
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _apply(result: SearchResult, meta: dict) -> None:
        art = dict(result.artifact_metadata or {})
        for key, field in (("publication_number", "publication_number"),
                           ("application_number", "application_number"),
                           ("ipc", "ipc")):
            if not art.get(key) and meta.get(field):
                art[key] = meta[field]
        result.artifact_metadata = art
        # abstract 进入 SearchResult.abstract（现有 OfflineSummaryEvidence 读取该字段）
        if meta.get("abstract") and not result.abstract:
            result.abstract = meta["abstract"]


class CnkiPatentAdapter(SourceAdapter):
    """CNKI 专利数据源适配器（v0.17 Phase 2.2-F）。"""

    database_name = DATABASE_NAME

    def __init__(self, session) -> None:
        self._session = session

    # ---------- 入口 ----------

    def search(self, keyword: str, count: int = 10,
               query_context=None) -> list[SearchResult]:
        page = self._session.page()
        original = str(
            getattr(query_context, "research_direction", "") or ""
        ).strip() or keyword
        person = str(
            getattr(query_context, "author_name", "") or ""
        ).strip()
        affiliation = str(
            getattr(query_context, "author_affiliation", "") or ""
        ).strip()

        # 基准字段选择（v0.17 2.2-F Hotfix，人工 GUI 验收根因修正）：
        # 组合检索必须以"能在 bounded 候选内保持合理召回"为优先——
        # person（发明人，通常最窄）> topic（主题）> affiliation（申请人）。
        # - topic+applicant → base=SU(topic)，保证候选先与主题相关，
        #   申请人再本地严格验证；禁止 base=SQR 从海量单位专利前 N 条找主题；
        # - person+applicant → base=AU(person)，applicant 本地验证；
        # - applicant only → base=SQR。
        if person:
            korder, base_kw = _KORDER_INVENTOR, person
        elif original:
            korder, base_kw = _KORDER_TOPIC, original
        elif affiliation:
            korder, base_kw = _KORDER_APPLICANT, affiliation
        else:
            korder, base_kw = _KORDER_TOPIC, original
        if not base_kw:
            raise SearchError("CNKI专利：未提供检索词")

        page.goto(_SEARCH_URL.format(
            kw=urllib.parse.quote(base_kw), korder=korder),
            wait_until="domcontentloaded", timeout=45000)
        self._wait_ready(page)
        rows = self._parse_list(page)
        is_intersection = sum(bool(v) for v in (original, person, affiliation)) > 1
        if is_intersection:
            # Hotfix-2：requested output 只截断最终 matches；组合查询独立使用
            # 5 页/100 candidate hard budget，并逐页验证、够数即停。
            results = self._intersect_pages(
                page, rows, count, original, person, affiliation)
        else:
            # 单条件保持既有语义：只为 requested count 补页，不扫描 intersection
            # candidate budget（例如 count=5 时仅使用第一页）。
            if len(rows) < count:
                rows = self._collect_pages(page, rows, count)
            results = self._map_rows(rows, count)
            try:
                CnkiPatentMetadataResolver(page.context).enrich(results)
            except Exception as exc:  # noqa: BLE001 - enrichment 失败不影响结果
                logger.warning("CNKI专利 enrichment 异常（降级）: %s", exc)
            self._last_search_stats = {
                "requested_count": count,
                "pages_fetched": min(max(1, (len(rows) + 19) // 20), 5),
                "candidate_count": len(rows),
                "match_count": len(results),
                "detail_candidates": min(len(results), _DETAIL_MAX_ITEMS),
                "intersection": False,
            }
        for i, r in enumerate(results, 1):
            r.rank = i

        if not results and not is_intersection:
            raise SearchError(
                "CNKI专利：检索结果为空")
        return results

    # ---------- readiness / 列表解析 ----------

    def _wait_ready(self, page) -> None:
        deadline = time.monotonic() + _RESULT_TIMEOUT_S
        stable = 0
        last: tuple | None = None
        while time.monotonic() < deadline:
            try:
                body = page.inner_text("body") or ""
                m = _COUNT_RE.search(body)
                count_text = m.group(0) if m else ""
                titles = page.locator(_TITLE_SEL).count()
                if count_text:
                    sig = (titles, count_text)
                    stable = stable + 1 if sig == last else 1
                    last = sig
                    if stable >= _STABLE_SAMPLES:
                        return
            except Exception:  # noqa: BLE001 - 页面瞬时不可用
                stable = 0
            page.wait_for_timeout(_STABLE_INTERVAL_MS)
        raise SearchError("CNKI专利：结果未就绪（超时）")

    def _parse_list(self, page) -> list[dict]:
        raw = page.evaluate(
            """() => Array.from(
                document.querySelectorAll('table.result-table-list tr'))
            .slice(1).map(tr => ({
                title: ((tr.querySelector('td.name a.fz14') || {}).innerText
                    || '').trim(),
                url: (tr.querySelector('td.name a.fz14') || {}).href || '',
                status: ((tr.querySelector('td.name b.marktip') || {}).innerText
                    || '').trim(),
                inventors: Array.from(tr.querySelectorAll('td.inventor a'))
                    .map(a => (a.innerText || '').trim().replace(/;$/, ''))
                    .filter(Boolean),
                applicant: ((tr.querySelector('td.applicant') || {}).innerText
                    || '').trim(),
                dates: Array.from(tr.querySelectorAll('td.date'))
                    .map(td => (td.innerText || '').trim()),
            }))"""
        )
        return raw if isinstance(raw, list) else []

    def _collect_pages(self, page, rows: list[dict], count: int) -> list[dict]:
        """bounded 分页：点「下一页」收集候选，去重，≤5 页（100 条）。

        真机取证：kns8s 结果页每页 20 条，「下一页」可点击加载后续页
        （探针 19 json：第 2 页含已知组合条件命中）。
        """
        max_pages = min((count + _LIST_PAGE_SIZE - 1) // _LIST_PAGE_SIZE,
                        _INTERSECTION_MAX_PAGES)
        pages_fetched = 1
        seen = {self._row_key(r) for r in rows if self._row_key(r)}
        while pages_fetched < max_pages:
            more = self._next_page_rows(page, pages_fetched + 1)
            if more is None:
                break
            added = 0
            for r in more:
                key = self._row_key(r)
                if key and key not in seen:
                    seen.add(key)
                    rows.append(r)
                    added += 1
            pages_fetched += 1
            if not added:
                break
            if len(rows) >= count:
                break
        return rows

    def _intersect_pages(
        self,
        page,
        first_rows: list[dict],
        requested_count: int,
        topic: str,
        person: str,
        applicant: str,
    ) -> list[SearchResult]:
        """逐页执行 bounded local AND；output count 与 candidate budget 解耦。"""
        matches: list[SearchResult] = []
        seen: set[tuple] = set()
        page_rows = first_rows
        pages_fetched = 0
        candidate_count = 0
        detail_candidates = 0
        detail_deadline = time.monotonic() + _DETAIL_BUDGET_S

        while (pages_fetched < _INTERSECTION_MAX_PAGES
               and candidate_count < _INTERSECTION_MAX_CANDIDATES
               and len(matches) < requested_count):
            pages_fetched += 1
            unique_rows: list[dict] = []
            for row in page_rows:
                key = self._row_key(row)
                if not key or key in seen:
                    continue
                seen.add(key)
                unique_rows.append(row)
                candidate_count += 1
                if candidate_count >= _INTERSECTION_MAX_CANDIDATES:
                    break

            if not unique_rows:
                break

            candidates = self._map_rows(unique_rows, len(unique_rows))
            # 列表字段先行：applicant/person 不匹配或缺失时无需打开详情。
            if person:
                candidates = [r for r in candidates
                              if self._inventors_contain(r, person)]
            if applicant:
                candidates = [r for r in candidates
                              if self._applicant_matches(r, applicant)]

            if topic:
                needs_abstract = [r for r in candidates
                                  if topic not in (r.title or "")]
                remaining_details = _DETAIL_MAX_ITEMS - detail_candidates
                remaining_seconds = detail_deadline - time.monotonic()
                if needs_abstract and remaining_details > 0 and remaining_seconds > 0:
                    batch = needs_abstract[:remaining_details]
                    # 计入送交 resolver 的候选，确保异常/无 URL/失败时也不会在
                    # 后续页面重置 cap；实际详情页打开数只会更少。
                    detail_candidates += len(batch)
                    try:
                        CnkiPatentMetadataResolver(
                            page.context,
                            max_items=len(batch),
                            total_budget_s=remaining_seconds,
                        ).enrich(batch)
                    except Exception as exc:  # noqa: BLE001 - enrichment 失败降级
                        logger.warning("CNKI专利 intersection enrichment 异常（降级）: %s",
                                       exc)
                candidates = [r for r in candidates
                              if self._topic_matches(r, topic)]

            for result in candidates:
                matches.append(result)
                if len(matches) >= requested_count:
                    break

            if (len(matches) >= requested_count
                    or pages_fetched >= _INTERSECTION_MAX_PAGES
                    or candidate_count >= _INTERSECTION_MAX_CANDIDATES):
                break
            more = self._next_page_rows(page, pages_fetched + 1)
            if more is None or not more:
                break
            page_rows = more

        matches = matches[:requested_count]
        self._last_search_stats = {
            "requested_count": requested_count,
            "pages_fetched": pages_fetched,
            "candidate_count": candidate_count,
            "match_count": len(matches),
            "detail_candidates": detail_candidates,
            "intersection": True,
        }
        logger.info("CNKI专利 bounded intersection: %s", self._last_search_stats)
        return matches

    def _next_page_rows(self, page, page_number: int) -> list[dict] | None:
        """单次、无重试地获取下一页；不可用/失败即诚实停止。"""
        try:
            nxt = page.locator("a:has-text('下一页')").first
            if nxt.count() == 0 or not nxt.is_visible():
                return None
            before_url = page.url
            nxt.click(timeout=5000)
            page.wait_for_timeout(3000)
            if page.url == before_url:
                page.wait_for_timeout(2000)
            return self._parse_list(page)
        except Exception as exc:  # noqa: BLE001 - 翻页失败停止收集
            logger.warning("CNKI专利分页停止（第 %s 页）: %s", page_number, exc)
            return None

    @staticmethod
    def _row_key(row: dict) -> tuple | None:
        """跨页稳定去重：优先详情 URL；缺失时退化为列表元数据复合键。"""
        title = str(row.get("title") or "").strip()
        if not title:
            return None
        url = str(row.get("url") or "").strip()
        if url:
            return ("url", url)
        return (
            "meta", title, str(row.get("applicant") or "").strip(),
            tuple(str(v).strip() for v in (row.get("inventors") or [])),
            tuple(str(v).strip() for v in (row.get("dates") or [])),
        )

    def _map_rows(self, rows: list[dict], count: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        for row in rows:
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            inventors = [i for i in (row.get("inventors") or []) if i]
            dates = list(row.get("dates") or [])
            date = dates[1] if len(dates) > 1 else (dates[0] if dates else "")
            year = None
            if date:
                ym = re.search(r"\b((?:19|20)\d{2})\b", date)
                year = ym.group(1) if ym else None
            if len(results) >= count:
                break
            results.append(
                SearchResult(
                    rank=len(results) + 1,
                    title=title,
                    authors=list(inventors),  # 发明人镜像（通用列兼容）
                    source=DATABASE_NAME,     # 展示语义（本阶段不另设显示名）
                    year=year,
                    detail_url=str(row.get("url") or "") or None,
                    document_type=str(row.get("status") or "") or None,
                    database=DATABASE_NAME,
                    abstract=None,  # 详情 resolver 补齐
                    artifact_type="patent",
                    artifact_metadata={
                        "inventors": inventors,
                        "applicant": str(row.get("applicant") or "").strip() or None,
                        "publication_number": None,
                        "application_number": None,
                        "date": date or None,
                    },
                    citation_count=None,
                )
            )
        return results

    @staticmethod
    def _inventors_contain(result: SearchResult, person: str) -> bool:
        inventors = ((result.artifact_metadata or {}).get("inventors") or [])
        return any(person in inv for inv in inventors)

    @staticmethod
    def _topic_matches(result: SearchResult, topic: str) -> bool:
        """topic 本地验证（v0.17 2.2-F Hotfix）。

        证据：标题 OR 摘要（详情 enrichment 已提前补齐 abstract）。
        UNKNOWN（标题与摘要均无 topic 证据）→ False（intersection 删除）。
        不得用 作者/申请人/年份 推断 topic。
        """
        if topic in (result.title or ""):
            return True
        return bool(result.abstract and topic in result.abstract)

    @staticmethod
    def _applicant_matches(result: SearchResult, applicant: str) -> bool:
        """applicant 本地验证：申请人字段明确包含单位名；缺失 → False。"""
        got = ((result.artifact_metadata or {}).get("applicant") or "").strip()
        if not got:
            return False  # UNKNOWN → intersection 删除
        return applicant in got
