"""天津大学新闻网数据源 Adapter（v0.17 Phase 2.3-B）。

依据：Phase 2.3-A 真机 PoC（docs/v0.17_news_source_probe.md、
runtime/v0.17_news_source_probe/*.json）——全部 selector 与流程均经真机取证。

能力：
- topic only → 新闻网智能搜索（原始中文 research_direction，不做英文扩展）
- person only → 同一全文搜索框按人名检索（PoC：田震 → 当选会士新闻）
- topic + person → **交集方案**：站点多词查询为短语匹配（"太赫兹 田震"→0 条，
  PoC 08 json），故分别执行两次单关键词搜索、按归一化标题取交集——
  结果同时满足 topic 与 person（真实 AND 语义，不悄悄退化为单条件）。
- 公开站点：无需天津大学授权会话；复用 session.context 同槽开页（生命周期约束）。

字段：
- 列表页：title / summary（p.desc）/ 媒体+发布时间（createDate_style 行）；
- 详情页（bounded enrichment，cap≤8）：detail_url（点击标题捕获）/
  authors（arc-info 作者署名）/ media 兜底。
- artifact_metadata 严格遵循 NewsMetadata schema（media/publish_time/authors/
  related_person）；media 不伪装成 authors。
- related_person：仅当 person 条件真实出现在标题/摘要文本中才写入
  （无 NER，不凭查询条件假定）。
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.parse

from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.sources.base import SearchError, SourceAdapter

logger = logging.getLogger(__name__)

# 数据源身份：与 registry source 名一致（citation_selector 按 database 分组）
DATABASE_NAME = "天津大学新闻网"

_NEWS_HOME = "https://news.tju.edu.cn/"
_SEARCH_INPUT_SEL = "input.qwss"
_RESULT_TIMEOUT_S = 30.0
_STABLE_SAMPLES = 2
_STABLE_INTERVAL_MS = 250
_COUNT_RE = re.compile(r"相关结果约为?\s*(\d+)\s*个")

# 列表条目（PoC 03/04/08 json 取证）
_ITEM_SEL = "div.syqbwzzs_templ"
_TITLE_SEL = "a.title"
_DESC_SEL = "p.desc"
_DATELINE_SEL = "div.createDate_style"

# 详情页（PoC 08 json：div.arc-info = "日期\n\n作者：…编辑：…来源：…"）
_ARC_INFO_SEL = "div.arc-info"
_RE_AUTHORS = re.compile(r"作者：\s*(.*?)\s*(?:编辑：|来源：|$)")
_RE_MEDIA = re.compile(r"来源：\s*([^\n]+?)\s*$")
_RE_DATELINE = re.compile(r"(.*?)-(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?)\s*$")
_RE_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")

# resolver 参数（与专利 resolver 同约束：不随 candidate_quota 放大）
_DETAIL_MAX_ITEMS = 8
_DETAIL_PER_ITEM_S = 5.0
_DETAIL_BUDGET_S = 30.0
_CACHE_MAX = 500
_GLOBAL_CACHE: dict[str, dict] = {}


def norm_title(text: str | None) -> str:
    """标题归一（交集/缓存键）：去空白、去标点、小写（对齐 ResultMerger 语义）。"""
    s = re.sub(r"[\s\u3000]+", "", (text or ""))
    s = re.sub(r"[^\w]+", "", s, flags=re.UNICODE)
    return s.lower()


def parse_dateline(text: str) -> tuple[str, str, str]:
    """解析列表条目媒体行「<分类> <媒体>-<发布时间>」→ (category, media, time)。

    真机形态（PoC 04/05 json）："媒体聚焦 健康报-2025-07-17 15:54:40"。
    """
    text = re.sub(r"\s+", " ", (text or "")).strip()
    m = _RE_DATELINE.search(text)
    if not m:
        return (text, "", "")
    left = m.group(1).strip()
    # 真机形态："媒体聚焦 健康报-2025-07-17 15:54:40" → 分类=媒体聚焦，媒体=健康报
    parts = left.rsplit(None, 1)
    if len(parts) == 2:
        category, media = parts[0], parts[1]
    else:
        category, media = "", left
    return (category, media, m.group(2))


def parse_arc_info(text: str) -> tuple[list[str], str]:
    """解析详情页 arc-info 文本 → (authors, media)。

    真机形态："2025/07/17\n\n作者：李哲 赵晖编辑：张华 殷琪来源：健康报"。
    字段间无换行分隔，用非贪婪正则；任何字段缺失为空，不抛出。
    """
    text = (text or "").strip()
    authors: list[str] = []
    media = ""
    m = _RE_AUTHORS.search(text)
    if m and m.group(1).strip():
        authors = [a for a in re.split(r"[\s、,，]+", m.group(1).strip()) if a]
    m = _RE_MEDIA.search(text)
    if m:
        media = m.group(1).strip()
    return (authors, media)


def build_search_url(keyword: str) -> str:
    """构造新闻网智能搜索 URL（base64 JSON payload，token=tourist 匿名）。

    备用说明：主入口为 UI 页面提交（_open_results）；本函数为文档化
    的等价直达 URL（PoC 已验证），仅用于调试/备用，不作为 Adapter 主路径。
    """
    payload = {"keyWord": keyword, "owner": "1854779116", "token": "tourist",
               "urlPrefix": "/aop_component/"}
    encoded = urllib.parse.quote(
        base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
    )
    return (
        "https://news.tju.edu.cn/aop_views/search/modules/resultpc/"
        f"soso.html?query={encoded}"
    )



class TjuNewsAdapter(SourceAdapter):
    """天津大学新闻网数据源适配器（v0.17 Phase 2.3-B）。"""

    database_name = DATABASE_NAME

    def __init__(self, session) -> None:
        self._session = session

    # ---------- 入口 ----------

    def search(self, keyword: str, count: int = 10,
               query_context=None) -> list[SearchResult]:
        context = self._session.context  # 复用受控会话（同槽生命周期）
        original = str(
            getattr(query_context, "research_direction", "") or ""
        ).strip() or keyword
        person = str(
            getattr(query_context, "author_name", "") or ""
        ).strip()

        results: list[SearchResult] = []
        pages: list = []
        need_topic_filter = False
        try:
            if original and person:
                # 组合语义（PoC 09 json：站点多词查询=短语匹配，"太赫兹 田震"
                # → 0 条，AND/OR 不可用；且 topic 搜索不命中正文层——田震
                # Fellow 文章正文含"太赫兹"却不在 topic 结果中）：
                # 以 person 结果为基（数量通常少），bounded 打开详情后验证
                # 标题/摘要/正文含 topic 关键词者保留——真实 AND，宁缺毋假。
                page_person, list_person = self._open_results(context, person)
                pages.append(page_person)
                results = self._map_rows(list_person, count)
                enrich_page = page_person
                need_topic_filter = True
            elif person:
                page_p, list_p = self._open_results(context, person)
                pages.append(page_p)
                results = self._map_rows(list_p, count)
                enrich_page = page_p
            elif original:
                page_t, list_t = self._open_results(context, original)
                pages.append(page_t)
                results = self._map_rows(list_t, count)
                enrich_page = page_t
            else:
                raise SearchError("天津大学新闻网：未提供检索词")

            if not results:
                raise SearchError("天津大学新闻网：检索结果为空")

            # bounded detail enrichment（先于组合 topic 过滤：正文在详情页）
            try:
                TjuNewsDetailResolver(enrich_page, context).enrich(results)
            except Exception as exc:  # noqa: BLE001 - enrichment 失败不影响结果
                logger.warning("天津大学新闻网 enrichment 异常（降级）: %s", exc)

            if need_topic_filter:
                kw = original.lower()
                results = [
                    r for r in results
                    if kw in (f"{r.title}{r.abstract or ''}"
                              f"{getattr(r, '_news_content_head', '')}").lower()
                ]
                for i, r in enumerate(results, 1):
                    r.rank = i

            if not results:
                raise SearchError(
                    "天津大学新闻网：检索结果为空（组合条件下无同时命中项）")

            # related_person：仅当 person 真实出现在标题/摘要文本（无 NER）
            if person:
                for r in results:
                    haystack = f"{r.title}{r.abstract or ''}"
                    if person in haystack:
                        art = dict(r.artifact_metadata or {})
                        art["related_person"] = [person]
                        r.artifact_metadata = art
            return results
        finally:
            for pg in pages:
                try:
                    pg.close()
                except Exception:  # noqa: BLE001
                    pass

    # ---------- 搜索页打开与解析 ----------

    def _open_results(self, context, keyword: str):
        """新闻网首页输入关键词 → Enter → 搜索结果页（正常页面提交）。"""
        if not keyword.strip():
            raise SearchError("天津大学新闻网：检索词为空")
        home = context.new_page()
        try:
            home.goto(_NEWS_HOME, wait_until="domcontentloaded", timeout=30000)
            home.wait_for_selector(_SEARCH_INPUT_SEL, timeout=20000)
            home.locator(_SEARCH_INPUT_SEL).first.fill(keyword.strip())
            results_page = None
            try:
                with home.context.expect_page(timeout=15000) as page_info:
                    home.locator(_SEARCH_INPUT_SEL).first.press("Enter")
                results_page = page_info.value
                results_page.wait_for_load_state(
                    "domcontentloaded", timeout=30000)
            except Exception:  # noqa: BLE001 - 站点可能当前页跳转
                if _COUNT_RE.search(home.inner_text("body") or ""):
                    results_page = home
                else:
                    raise
            results_page.wait_for_timeout(5000)
            try:
                results_page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            self._wait_ready(results_page)
            rows = self._parse_list(results_page)
            return results_page, rows
        except SearchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SearchError(f"天津大学新闻网：搜索失败（{exc}）") from exc

    def _wait_ready(self, page) -> tuple[int, int]:
        """readiness：结果统计条出现（真实 0 条也带统计）+ loading 消失 + 连续稳定。"""
        deadline = time.monotonic() + _RESULT_TIMEOUT_S
        stable = 0
        last: tuple | None = None
        while time.monotonic() < deadline:
            try:
                body = page.inner_text("body") or ""
                m = _COUNT_RE.search(body)
                count_text = m.group(0) if m else ""
                titles = page.locator(_TITLE_SEL).count()
                if count_text:  # 统计条 = 搜索已提交并渲染（0 条也有）
                    sig = (titles, count_text)
                    stable = stable + 1 if sig == last else 1
                    last = sig
                    if stable >= _STABLE_SAMPLES:
                        return titles, int(m.group(1))
            except Exception:  # noqa: BLE001 - 页面瞬时不可用
                stable = 0
            page.wait_for_timeout(_STABLE_INTERVAL_MS)
        raise SearchError("天津大学新闻网：搜索结果未就绪（超时）")

    def _parse_list(self, page) -> list[dict]:
        raw = page.evaluate(
            """(n) => Array.from(document.querySelectorAll('div.syqbwzzs_templ'))
            .slice(0, n).map(item => ({
                title: (item.querySelector('a.title') || {}).innerText || '',
                summary: (item.querySelector('p.desc') || {}).innerText || '',
                dateline: (item.querySelector('div.createDate_style') || {})
                    .innerText || '',
            }))""",
            50,
        )
        return raw if isinstance(raw, list) else []

    def _map_rows(self, rows: list[dict], count: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        for row in rows:
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            category, media, publish_time = parse_dateline(row.get("dateline"))
            year = None
            if publish_time:
                ym = _RE_YEAR.search(publish_time)
                year = ym.group(1) if ym else None
            if len(results) >= count:
                break
            results.append(
                SearchResult(
                    rank=len(results) + 1,
                    title=title,
                    authors=[],  # 详情署名由 resolver 镜像；媒体名绝不入 authors
                    source=media,  # 来源列显示真实媒体（source 无内部 grouping 语义）
                    year=year,
                    detail_url=None,  # 点击标题捕获（resolver）
                    document_type=category or None,  # 媒体聚焦/综合新闻（展示）
                    database=DATABASE_NAME,
                    abstract=str(row.get("summary") or "").strip() or None,
                    artifact_type="news",
                    artifact_metadata={
                        "media": media,
                        "publish_time": publish_time,
                        "authors": [],
                        "related_person": [],
                    },
                    citation_count=None,
                )
            )
        return results


class TjuNewsDetailResolver:
    """新闻详情 bounded enrichment（detail_url + authors）。

    与专利/万方 resolver 同约束：缓存 / 单项超时 / 总预算 / 硬上限 / 失败降级。
    """

    def __init__(
        self,
        results_page,
        context,
        cache: dict | None = None,
        max_items: int = _DETAIL_MAX_ITEMS,
        per_item_timeout_s: float = _DETAIL_PER_ITEM_S,
        total_budget_s: float = _DETAIL_BUDGET_S,
    ) -> None:
        self._page = results_page
        self._context = context
        self._cache = cache if cache is not None else _GLOBAL_CACHE
        self._max_items = max_items
        self._per_item = per_item_timeout_s
        self._budget = total_budget_s

    @staticmethod
    def _cache_key(result: SearchResult) -> str:
        return norm_title(result.title)

    def enrich(self, results: list[SearchResult]) -> int:
        opened = 0
        deadline = time.monotonic() + self._budget
        for i, result in enumerate(results):
            if i >= self._max_items:
                logger.warning("天津大学新闻网详情 enrichment 达到上限 %s", self._max_items)
                break
            if time.monotonic() >= deadline:
                logger.warning("天津大学新闻网详情 enrichment 预算耗尽")
                break
            key = self._cache_key(result)
            if key in self._cache:
                self._apply(result, self._cache[key])
                opened += 1
                continue
            meta = self._resolve_one(i)
            if meta:
                if len(self._cache) > _CACHE_MAX:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = meta
                self._apply(result, meta)
                opened += 1
        return opened

    def _resolve_one(self, index: int) -> dict | None:
        try:
            with self._page.context.expect_page(timeout=15000) as page_info:
                self._page.locator(_TITLE_SEL).nth(index).click()
            detail = page_info.value
            try:
                detail.wait_for_load_state(
                    "domcontentloaded", timeout=self._per_item * 1000)
            except Exception:  # noqa: BLE001
                pass
            detail.wait_for_timeout(1200)
            meta: dict = {"detail_url": detail.url, "authors": [], "media": "",
                          "content_head": (detail.inner_text("body") or "")[:1500]}
            try:
                arc = detail.locator(_ARC_INFO_SEL).first
                if arc.count() > 0:
                    authors, media = parse_arc_info(arc.inner_text())
                    meta["authors"] = authors
                    meta["media"] = media
            except Exception:  # noqa: BLE001 - 字段缺失降级
                pass
            try:
                detail.close()
            except Exception:  # noqa: BLE001
                pass
            return meta
        except Exception as exc:  # noqa: BLE001 - 详情失败降级
            logger.warning("天津大学新闻网详情解析失败（降级）: %s", exc)
            return None

    @staticmethod
    def _apply(result: SearchResult, meta: dict) -> None:
        if meta.get("detail_url") and not result.detail_url:
            result.detail_url = meta["detail_url"]
        art = dict(result.artifact_metadata or {})
        if meta.get("authors") and not art.get("authors"):
            art["authors"] = meta["authors"]
            if not result.authors:
                result.authors = list(meta["authors"])  # UI 通用列镜像
        if not art.get("media") and meta.get("media"):
            art["media"] = meta["media"]  # 列表媒体缺失时的兜底
        result.artifact_metadata = art
        # 组合条件 topic 过滤用（瞬态属性；不入 to_dict / artifact_metadata）
        result._news_content_head = meta.get("content_head") or ""