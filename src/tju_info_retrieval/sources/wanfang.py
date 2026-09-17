"""万方数据知识服务平台 Adapter（v1，实验性）。

通过 PortalNavigator 获取代理落地页，使用 #search-input 搜索，
结果解析采用"正文正则兜底 + DOM 链接回填"双通道：
- 结果条目仍由 _RESULT_RE 基于 body 文本启发式解析（数字编号/类型/来源/年份）；
- v0.13 补充：从结果页 DOM 提取 <a> 标题链接，按归一化标题回填
  detail_url（缺失时保持 None），使详情页多窗口可打开万方全文。
若解析失败，请启用诊断日志查看实际页面内容。

边界：单次搜索、单页结果、低频、不下载全文。
"""
from __future__ import annotations

import re
import time

from tju_info_retrieval.browser.portal import PortalNavigator
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.sources.base import SearchError, SourceAdapter
from tju_info_retrieval.sources.wanfang_metadata_resolver import WanfangMetadataResolver

import logging

logger = logging.getLogger(__name__)

# 结果项正则：数字.标题 | [类型]作者-《来源》年份
# 来源后可能有额外标识（如 CSTPCD、北大核心等），用 .*? 跳过
# v0.11: 末尾追加可选的引用数（数字）
_RESULT_RE = re.compile(
    r"(\d+)\.\s*([^|]+?)\s*(?:\|\s*)?\[([^\]]*?)\]\s*([^《]+?)-《([^》]+?)》\s*.*?(\d{4})(?:\s+(\d+))?",
)

# 搜索按钮两个相同文本的 button，取第一个
_SEARCH_BTN = "button:has-text('检索')"

# v0.16 Phase B：高引用模式原生被引降序参数（docs/v0.16_wanfang_native_citation_sort_probe.md）
# o={"field":"被引频次","order":1} 的 URL 编码（探针实测：普通检索结果页
# 点击 .sort-area .sort-item「被引频次」即追加此参数，结果按被引降序）。
_CITATION_SORT_PARAM = (
    "&o=%7B%22field%22%3A%22%E8%A2%AB%E5%BC%95%E9%A2%91%E6%AC%A1%22%2C%22order%22%3A1%7D"
)


def _wants_citation_sort(query_context) -> bool:
    """v0.16 Phase B：query_context 是否要求原生被引降序（仅 CITATION 模式）。"""
    return str(
        getattr(query_context, "ranking_mode", "") or ""
    ).upper() == "CITATION"

# 结果页 DOM 链接提取 JS：收集所有 a[href] 的绝对 URL 与可见文本
_LINKS_JS = """() => Array.from(document.querySelectorAll('a[href]'))
    .map(a => ({
        href: a.href || '',
        text: (a.innerText || a.textContent || '').trim(),
    }))
    .filter(item => item.href && item.text)"""

# 前缀匹配兜底参数：归一化标题至少 6 字才允许前缀匹配（防短标题误配）；
# 链接文本超出标题的归一化长度上限（如链接内含 "[期刊论文]" 类型徽标）
_PREFIX_MATCH_MIN_CHARS = 6
_PREFIX_MATCH_MAX_EXTRA = 16

# ---- v0.13 高级检索（作者单位过滤）参数 ----
# 高级检索页路径（与结果页同域；探针确认 docs/v0.13_wanfang_advanced_search_poc.md）
_ADVANCED_SEARCH_PATH = "/advanced-search/paper"
# 高级检索结果等待上限（秒）
_ADVANCED_RESULT_TIMEOUT_S = 30.0
# 原生被引排序后的 SPA hydration 等待上限；禁止仅靠固定 sleep 判定就绪。
_CITATION_RESULT_TIMEOUT_S = 30.0
_CITATION_STABLE_INTERVAL_MS = 250
_CITATION_STABLE_SAMPLES = 2
# iview 输入框逐键延迟（ms；fill 一次设值不注册 v-model，必须逐键）
_ADVANCED_TYPE_DELAY_MS = 20
_ADVANCED_FIELD_ORG = "作者单位"
# v0.17 Phase 1：作者字段（真机取证 runtime/v017_probe/wanfang_adv_dom_diag.json：
# 下拉选项含 作者/第一作者/作者单位/期刊-通讯作者，必须 :text-is 精确匹配）
_ADVANCED_FIELD_AUTHOR = "作者"
# 关键词行字段候选（默认行为"题名或关键词"）
_ADVANCED_FIELD_KEYWORDS = ("关键词", "主题", "题名或关键词")
# 关键词行字段切换目标（作者/单位占满前两行时把空闲行切到该字段）
_ADVANCED_FIELD_KEYWORD_ROW = "题名或关键词"
# 每行字段下拉触发器（真机 DOM：class 含 search-option field；探针验证 nth(i)）
_ADVANCED_FIELD_SELECT_ROW = ".search-option.field"
# 行内 精确/模糊 下拉（与字段下拉同为 .search-option；row0 的精确/模糊 =
# nth(1)，真机验证唯一使用形态——作者行固定占用 row0）
_ADVANCED_MATCH_SELECT = ".search-option"
_ADVANCED_FIELD_ITEM = ".ivu-select-item"
_ADVANCED_INPUT = ".ivu-input"
_ADVANCED_SUBMIT = "span.submit-btn"
# 发表时间行：容器（含“发表时间：”标签）内两个年份下拉（起始/结束，年粒度）
_ADV_TIME_ROW = "div.time-select:has(span.title:text-is('发表时间：'))"
_ADV_TIME_SELECT = ".ivu-select"
_ADV_TIME_ITEM = ".ivu-select-dropdown:visible li.ivu-select-item"


def _date_to_year_str(date_str: str) -> str:
    """从 YYYY.MM/YYYY 提取年份字符串；失败抛 SearchError。"""
    m = re.search(r"(19|20)\d{2}", (date_str or "").strip())
    if not m:
        raise SearchError(
            f"万方高级检索：无法解析时间条件 {date_str!r}（应为 YYYY.MM）"
        )
    return m.group(0)


def _normalize_year(value: str | None) -> str:
    """年份归一：去首尾空白、去尾部“年”——“2020年”与“2020”视为同一值。

    万方行内隐藏 input 的回读值可能带或不带“年”后缀（真机实测两种形态
    均出现），校验必须按归一化比较，否则会误判设置失败。
    """
    s = (value or "").strip()
    return s[:-1].strip() if s.endswith("年") else s


def _set_publish_time_range(page, start_date: str, end_date: str) -> None:
    """设置高级检索“发表时间”行（原生起始/结束年份下拉，年粒度）。

    行结构（探针实证 runtime/wanfang_adv_time_probe/time_row.json）：
    div.time-select 行容器内 SPAN.hrafwidth > SPAN.title“发表时间：”+
    两个 ivu-select（起始默认“不限”、结束默认“至今”，选项为
    “2005年…2026年”列表，选中值同步至行内隐藏 input）。
    """
    row = page.locator(_ADV_TIME_ROW)
    if row.count() == 0:
        raise SearchError("万方高级检索：未找到“发表时间”行控件")
    selects = row.locator(_ADV_TIME_SELECT)
    if selects.count() < 2:
        raise SearchError("万方高级检索：发表时间行控件异常")
    sy = _date_to_year_str(start_date)
    ey = _date_to_year_str(end_date)
    if int(sy) > int(ey):
        raise SearchError(f"万方高级检索：时间范围无效（{sy} > {ey}）")
    for nth, year in ((0, sy), (1, ey)):
        selects.nth(nth).click(timeout=5000)
        page.locator(f"{_ADV_TIME_ITEM}:has-text('{year}年')").first.click(timeout=5000)
        page.wait_for_timeout(800)
    # 回读校验（行内隐藏 input 携带选中值；读不到则跳过校验）
    try:
        vals = row.locator("input[type=hidden]").evaluate_all(
            "els => els.map(e => e.value)"
        )
    except Exception:  # noqa: BLE001
        vals = []
    if len(vals) >= 2:
        for want, got in ((f"{sy}年", vals[0]), (f"{ey}年", vals[1])):
            if _normalize_year(want) != _normalize_year(got):
                raise SearchError(
                    f"万方高级检索：发表时间设置未生效（期望 {want}，实际 {got!r}）"
                )
# 可见行输入（input.ivu-input 非 textarea/隐藏）及其所在行的字段名：
# 行容器内 `.search-option.field` 的隐藏 input[value] 即字段名。
# 行字段=作者单位/关键词 → 对应可见输入（避免 .first/nth 命中隐藏组件）。
_ADVANCED_ROWS_JS = r"""() => {
  const rows = [];
  for (const inp of document.querySelectorAll('input.ivu-input')) {
    if (inp.offsetParent === null) continue;  // 仅可见输入（隐藏组件/隐藏标签页排除）
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


class WanfangAdapter(SourceAdapter):
    database_name = "万方"

    def __init__(self, session) -> None:
        self._session = session
        self._portal = PortalNavigator(session)

    def search(self, keyword: str, count: int = 10, query_context=None) -> list[SearchResult]:
        page = self._portal.open_portal()
        page = self._portal.open_resource("万方数据知识服务平台", "中文资源")
        page.wait_for_timeout(3000)

        # v0.16：万方所有页面检索（普通/高级/高引用原生排序）统一使用
        # 用户原始关键词（query_context.research_direction）——万方普通
        # 搜索框与高级表单都不接受 OR Boolean 语法，上游扩展表达式
        # （如 "(太赫兹 OR THz OR …)"）会被当作字面文本检索导致 0 结果。
        # query_context 缺失（旧调用）时安全回退到传入的 keyword。
        # 注意：RankingMode.CITATION 只影响排序/候选策略，不改关键词。
        original_keyword = str(
            getattr(query_context, "research_direction", "") or ""
        ).strip() or keyword

        # v0.17 Phase 1：author_name OR author_affiliation 任一非空 →
        # 走官方高级检索（作者/作者单位字段）。纯人名不再退化为主题关键词。
        affiliation = ""
        person = ""
        if query_context is not None:
            affiliation = str(
                getattr(query_context, "author_affiliation", "") or ""
            ).strip()
            person = str(
                getattr(query_context, "author_name", "") or ""
            ).strip()
        if affiliation or person:
            return self._advanced_search(
                keyword, affiliation, count, page, original_keyword=original_keyword,
                person=person,
                start_date=str(getattr(query_context, "start_date", "") or "").strip() or None,
                end_date=str(getattr(query_context, "end_date", "") or "").strip() or None,
                citation_sort=_wants_citation_sort(query_context),
            )

        page.locator("#search-input").clear()
        page.fill("#search-input", original_keyword)
        page.wait_for_timeout(500)
        page.locator(_SEARCH_BTN).first.click()
        # 等待结果页渲染（Vue SPA，需等待 AJAX 加载）
        time.sleep(4)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        # v0.16 Phase B：高引用模式原生被引降序（docs/v0.16_wanfang_native_citation_sort_probe.md）
        if _wants_citation_sort(query_context):
            self._apply_native_citation_sort(page)

        results = self._parse_results(page, count)
        self._enrich_metadata(page, results)
        if not results:
            raise SearchError(
                f"万方检索结果为空（关键词：{original_keyword}）。"
                "可能原因：页面未正常加载，请检查 body 文本诊断。"
            )
        return results

    def _apply_native_citation_sort(self, page, advanced: bool = False) -> None:
        """v0.16 Phase B：确保结果页按被引降序（仅 CITATION 模式调用）。

        - 普通检索：URL 参数 `o={"field":"被引频次","order":1}` 直接导航
          （确定性降序，不受 SPA 排序态残留影响）；
        - 高级检索：结果页排序栏 `.sort-area .sort-item`「被引频次」点击
          一次（高级表单每次全新提交 → 默认综合排序 → 单击即降序）。
        普通检索失败时保持默认序候选并由本地 citation_count 排序兜底；
        高级检索若无法确认新结果 hydration，则显式失败，避免解析空/旧 DOM。
        """
        try:
            if advanced:
                before_signature = self._citation_result_state(page).get("signature")
                page.locator(".sort-area .sort-item", has_text="被引").first.click(
                    timeout=5000
                )
                self._wait_for_citation_results(
                    page,
                    before_signature=before_signature,
                )
            else:
                current = page.url
                if "o=%7B" in current or "o={" in current:
                    return  # 已带排序参数，避免重复追加
                page.goto(current + _CITATION_SORT_PARAM, timeout=45000)
                page.wait_for_timeout(4000)
        except Exception as exc:  # noqa: BLE001 - 普通检索仍允许本地排序降级
            if advanced:
                raise SearchError(f"万方高级检索：被引排序结果未就绪（{exc}）") from exc
            logger.warning("万方原生被引排序失败（保持默认序候选）: %s", exc)

    @staticmethod
    def _citation_result_state(page) -> dict:
        """读取被引排序后的可验证状态（active、卡片、loading、内容签名）。"""
        state = page.evaluate(
            """() => {
            const visible = el => !!(el && (
                el.offsetWidth || el.offsetHeight || el.getClientRects().length
            ));
            const titles = Array.from(document.querySelectorAll(
                '.title-area span.title'
            )).map(el => (el.innerText || '').trim()).filter(Boolean);
            const citations = Array.from(document.querySelectorAll(
                '.title-area .stat-item.quote'
            )).map(el => (el.innerText || '').trim());
            const citeSort = Array.from(document.querySelectorAll(
                '.sort-area .sort-item'
            )).find(el => (el.innerText || '').includes('被引'));
            const active = !!citeSort && /(^|\\s)(active|down|up)(\\s|$)/.test(
                String(citeSort.className || '')
            );
            const loading = Array.from(document.querySelectorAll(
                '.ivu-spin, .ivu-spin-fix, .loading, '
                + '[class*="loading"], [class*="Loading"]'
            )).some(visible);
            return {
                title_count: titles.length,
                active,
                loading,
                signature: JSON.stringify([titles.slice(0, 3), citations.slice(0, 5)]),
            };
            }"""
        )
        return state if isinstance(state, dict) else {}

    def _wait_for_citation_results(
        self,
        page,
        *,
        before_signature: str | None,
    ) -> None:
        """等待高级结果在被引排序后完成 SPA 重挂载并保持稳定。"""
        deadline = time.monotonic() + _CITATION_RESULT_TIMEOUT_S
        stable = 0
        last_signature = None
        last_state: dict = {}
        while time.monotonic() < deadline:
            state = self._citation_result_state(page)
            last_state = state
            signature = state.get("signature")
            # 高级页会先更新 URL/排序 active，再异步替换结果卡片；因此 URL
            # 变化不能证明 hydration 完成，必须看到标题/被引内容签名变化。
            transitioned = signature != before_signature
            ready = (
                bool(state.get("active"))
                and int(state.get("title_count") or 0) > 0
                and not bool(state.get("loading"))
                and transitioned
            )
            if ready and signature == last_signature:
                stable += 1
            elif ready:
                stable = 1
            else:
                stable = 0
            if stable >= _CITATION_STABLE_SAMPLES:
                return
            last_signature = signature
            page.wait_for_timeout(_CITATION_STABLE_INTERVAL_MS)
        raise SearchError(
            "排序后结果 hydration 超时"
            f"（active={last_state.get('active')}, "
            f"titles={last_state.get('title_count')}, "
            f"loading={last_state.get('loading')}）"
        )

    def _enrich_metadata(self, page, results) -> None:
        """v0.14：metadata resolver 补齐 year/authors/doi/citation/detail_url。

        失败降级：任何异常仅记录 warning，不影响搜索结果返回；
        单条超时 ≤5s、整体预算 ≤30s（缓存命中直接应用，不打开页面）。
        """
        if not results:
            return
        try:
            resolver = WanfangMetadataResolver(page=page)
            resolver.enrich(results)
        except Exception as exc:  # noqa: BLE001 - 失败降级，不影响搜索
            logger.warning("万方 metadata 补全失败（降级保留原字段）: %s", exc)

    @staticmethod
    def _switch_row_field(page, row_index: int, field: str) -> None:
        """切换第 row_index 行的字段下拉（.search-option.field，真机取证）。

        选项用 :text-is 精确匹配——下拉同时含 第一作者/期刊-通讯作者 等
        含"作者"子串的选项，has-text 会误选（选项全表见
        runtime/v017_probe/wanfang_adv_dom_diag.json）。选项渲染在下拉组件
        内部，按 trigger 域定位，避免命中其它行的隐藏同名选项。
        """
        trigger = page.locator(_ADVANCED_FIELD_SELECT_ROW).nth(row_index)
        try:
            trigger.click(timeout=5000)
            page.wait_for_timeout(1000)
            option = trigger.locator(f"{_ADVANCED_FIELD_ITEM}:text-is('{field}')").first
            if option.count() == 0:
                raise SearchError(f"万方高级检索：字段下拉无选项: {field}")
            option.click(timeout=5000)
            page.wait_for_timeout(800)
        except SearchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SearchError(f"万方高级检索：字段切换为 {field} 失败（{exc}）") from exc

    @staticmethod
    def _set_author_row_exact(page) -> None:
        """作者行（固定 row0）的 精确/模糊 下拉切到「精确」。

        真机证据（runtime/v017_probe/wanfang_author_network_diag.json）：
        作者模糊查询（作者:(张伟)）服务端恒返回 0（常见姓名亦然）；
        精确查询（作者:("吴晓东")）返回真实结果。row0 的精确/模糊下拉 =
        .search-option nth(1)（field 下拉在前），探针验证切换后查询串带引号。
        """
        trigger = page.locator(_ADVANCED_MATCH_SELECT).nth(1)
        try:
            trigger.click(timeout=5000)
            page.wait_for_timeout(800)
            option = trigger.locator(f"{_ADVANCED_FIELD_ITEM}:text-is('精确')").first
            if option.count() == 0:
                raise SearchError("万方高级检索：精确/模糊下拉无「精确」选项")
            option.click(timeout=5000)
            page.wait_for_timeout(500)
        except SearchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SearchError(f"万方高级检索：精确模式切换失败（{exc}）") from exc

    def _advanced_search(
        self,
        keyword: str,
        affiliation: str,
        count: int,
        page,
        original_keyword: str | None = None,
        person: str = "",
        start_date: str | None = None,
        end_date: str | None = None,
        citation_sort: bool = False,
    ) -> list[SearchResult]:
        """官方高级检索：作者/作者单位/关键词 按行填充 + 原始用户词。

        original_keyword：用户原始关键词（research_direction）；缺省回退
        到 keyword。万方高级表单不接受查询扩展的 OR 表达式，故不使用
        keyword（上游可能已是扩展产物）。

        v0.17 Phase 1 行规划（真机取证：可见行 0-2，默认字段
        主题/题名或关键词/题名，见 wanfang_adv_dom_diag.json）：
        - 作者行固定占用 row0 并切「精确」（模糊作者查询服务端恒 0）；
        - 作者单位：无作者条件时占用 row0（v0.13 既有行为不变），
          有作者条件时占用 row1；
        - 关键词行：按行内当前字段名定位（题名或关键词/主题/关键词），
          前两行被占满时把下一空闲行切换为「题名或关键词」。
        纯人名（无主题）时导航词使用人名（仅用于确定智搜域名）。

        失败不降级普通搜索：需求明确"高级检索失败即失败"，抛 SearchError。
        """
        # 禁止自动扩展：高级检索始终以原始关键词提交（"太赫兹"而非 OR 表达式）
        topic = (original_keyword or "").strip() or keyword
        # 1) 普通搜索导航（确定智搜域名/校验可用性；空主题时用人名导航）
        nav_kw = topic or person or affiliation
        page.locator("#search-input").clear()
        page.fill("#search-input", nav_kw)
        page.locator(_SEARCH_BTN).first.click()
        page.wait_for_timeout(4000)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        if "/paper" not in page.url:
            raise SearchError("万方高级检索：未能进入检索结果页（智搜域名未知）")
        host_base = page.url.split("/paper")[0]

        # 2) 进入官方高级检索页
        page.goto(
            f"{host_base}{_ADVANCED_SEARCH_PATH}",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        page.wait_for_timeout(4000)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(2000)

        # 3) 行字段规划与切换（作者行固定 row0；其后作者单位）
        if person:
            self._switch_row_field(page, 0, _ADVANCED_FIELD_AUTHOR)
            self._set_author_row_exact(page)
        row_cursor = 1 if person else 0
        if affiliation:
            self._switch_row_field(page, row_cursor, _ADVANCED_FIELD_ORG)
            row_cursor += 1

        # 4) 逐键输入：按"字段所在行"定位可见输入框
        #    （真实页面存在多个隐藏 .ivu-input，.first/.nth 会命中隐藏组件，
        #     须以行容器内 .search-option.field 的字段名为准选取）
        try:
            rows = page.evaluate(_ADVANCED_ROWS_JS) or []
        except Exception as exc:  # noqa: BLE001
            raise SearchError(f"万方高级检索：行输入定位失败（{exc}）") from exc

        # (rows_idx, 字段, 值)——仅收集非空条件
        filled: list[tuple[int, str, str]] = []
        for field_name, value in (
            (_ADVANCED_FIELD_AUTHOR, person),
            (_ADVANCED_FIELD_ORG, affiliation),
        ):
            if not value:
                continue
            idx = next(
                (i for i, r in enumerate(rows) if r.get("field") == field_name),
                None,
            )
            if idx is None:
                raise SearchError(
                    f"万方高级检索：未定位到 {field_name} 行"
                    f"（可见行字段: {[r.get('field') for r in rows]}）"
                )
            filled.append((idx, field_name, value))

        if topic:
            kw_idx = next(
                (i for i, r in enumerate(rows)
                 if (r.get("field") or "") in _ADVANCED_FIELD_KEYWORDS),
                None,
            )
            if kw_idx is None:
                # 作者/单位占满前两行 → 把下一空闲行切换为题名或关键词
                self._switch_row_field(page, row_cursor, _ADVANCED_FIELD_KEYWORD_ROW)
                try:
                    rows = page.evaluate(_ADVANCED_ROWS_JS) or []
                except Exception as exc:  # noqa: BLE001
                    raise SearchError(f"万方高级检索：行输入定位失败（{exc}）") from exc
                kw_idx = next(
                    (i for i, r in enumerate(rows)
                     if (r.get("field") or "") in _ADVANCED_FIELD_KEYWORDS),
                    None,
                )
                if kw_idx is None:
                    raise SearchError(
                        "万方高级检索：未定位到关键词行"
                        f"（可见行字段: {[r.get('field') for r in rows]}）"
                    )
            filled.append((kw_idx, _ADVANCED_FIELD_KEYWORD_ROW, topic))

        inputs = page.locator(f"input{_ADVANCED_INPUT}:visible")
        for idx, _field_name, value in filled:
            inputs.nth(idx).press_sequentially(value, delay=_ADVANCED_TYPE_DELAY_MS)

        # 5) 提交前回读校验：断言已填条件行的值已同步（防空表单提交）
        try:
            rows = page.evaluate(_ADVANCED_ROWS_JS) or []
        except Exception as exc:  # noqa: BLE001
            raise SearchError(f"万方高级检索：输入回读失败（{exc}）") from exc
        if filled and len(rows) <= max(idx for idx, _f, _v in filled):
            raise SearchError("万方高级检索输入未就绪（iview 模型未同步），已中止提交")
        for idx, _field_name, value in filled:
            if value not in (rows[idx].get("value") or ""):
                raise SearchError(
                    "万方高级检索输入未就绪（iview 模型未同步），已中止提交"
                )

        # 5) 发表时间（原生行控件：起始/结束年份下拉，年粒度；无时间条件不触碰）
        if start_date or end_date:
            _set_publish_time_range(page, start_date, end_date)

        # 6) 提交并等待真实结果页
        page.locator(_ADVANCED_SUBMIT).first.click(timeout=5000)
        self._wait_advanced_results(page)

        # v0.16 Phase B：高引用模式原生被引降序（条件构建未改动，仅结果页排序）
        if citation_sort:
            self._apply_native_citation_sort(page, advanced=True)

        results = self._parse_results(page, count)
        self._enrich_metadata(page, results)
        if not results:
            raise SearchError("万方高级检索结果为空（结果页已渲染但无可解析条目）")
        return results

    def _wait_advanced_results(self, page) -> None:
        """提交后等待真实结果页渲染（标题非空、loading 消失并稳定）。"""
        deadline = time.monotonic() + _ADVANCED_RESULT_TIMEOUT_S
        stable = 0
        while time.monotonic() < deadline:
            try:
                on_result_route = "/paper" in page.url
                titles_ready = page.locator(".title-area span.title").count() > 0
                loading = self._has_visible_loading(page)
                if on_result_route and titles_ready and not loading:
                    stable += 1
                else:
                    stable = 0
                if stable >= _CITATION_STABLE_SAMPLES:
                    return
            except Exception:
                stable = 0
            page.wait_for_timeout(_CITATION_STABLE_INTERVAL_MS)
        raise SearchError("万方高级检索未返回结果（提交或渲染超时）")

    @staticmethod
    def _has_visible_loading(page) -> bool:
        """检测结果区可见 loading；假页/旧测试无法求值时按未加载处理。"""
        try:
            value = page.evaluate(
                """() => {
                const visible = el => !!(el && (
                    el.offsetWidth || el.offsetHeight || el.getClientRects().length
                ));
                return Array.from(document.querySelectorAll(
                    '.ivu-spin, .ivu-spin-fix, .loading, '
                    + '[class*="loading"], [class*="Loading"]'
                )).some(visible);
                }"""
            )
        except Exception:  # noqa: BLE001 - 假页/异常按无可见 loading 处理
            return False
        return value is True

    @staticmethod
    def _parse_results(page, count: int = 10) -> list[SearchResult]:
        # v0.13：优先 DOM 标题（.title-area span.title）。高级检索结果内联
        # 渲染在 advanced-search 页时，正文正则会被表单/摘要内嵌数字污染，
        # 以 DOM 标题为准；正则条目仅用于给归一化匹配的 DOM 标题补充
        # 作者/来源/年份。无 DOM 标题时保持原正文正则解析（fallback）。
        dom_titles = WanfangAdapter._extract_dom_titles(page)
        regex_rows = WanfangAdapter._extract_regex_rows(page)
        link_map = WanfangAdapter._extract_title_links(page)
        # v0.16 Phase A3：被引数按结果卡片直读（.stat-item.quote 同源提取，
        # 普通检索与高级检索结果页结构一致）；卡片缺失时回退正则通道。
        card_citations = WanfangAdapter._extract_card_citations(page)
        if dom_titles:
            results: list[SearchResult] = []
            for rank, title in enumerate(dom_titles[:count], 1):
                norm = WanfangAdapter._normalize_title(title)
                row = next(
                    (r for r in regex_rows
                     if WanfangAdapter._normalize_title(r["title"]) == norm),
                    None,
                )
                citation = card_citations.get(norm)
                if citation is None and row:
                    citation = row["citation_count"]
                results.append(
                    SearchResult(
                        rank=rank,
                        title=title,
                        authors=(
                            [a.strip() for a in re.split(r"[;；,，、\s]+", row["authors_raw"]) if a.strip()]
                            if row else []
                        ),
                        source=row["source"] if row else None,
                        year=row["year"] if row else None,
                        detail_url=WanfangAdapter._match_link(link_map, norm),
                        document_type=row["document_type"] if row else None,
                        database="万方",
                        citation_count=citation,
                    )
                )
            return results
        # 无 DOM 标题：原正文正则解析（行为不变）
        results = []
        for row in regex_rows[:count]:
            results.append(
                SearchResult(
                    rank=row["rank"],
                    title=row["title"],
                    authors=[
                        a.strip()
                        for a in re.split(r"[;；,，、\s]+", row["authors_raw"])
                        if a.strip()
                    ],
                    source=row["source"],
                    year=row["year"],
                    detail_url=WanfangAdapter._match_link(
                        link_map, WanfangAdapter._normalize_title(row["title"])
                    ),
                    document_type=row["document_type"],
                    database="万方",
                    citation_count=row["citation_count"],
                )
            )
        return results

    @staticmethod
    def _extract_dom_titles(page) -> list[str]:
        """结果项 DOM 标题（.title-area span.title，按文档序）。"""
        try:
            raw = page.evaluate(
                "() => Array.from(document.querySelectorAll('.title-area span.title'))"
                ".map(el => (el.innerText || '').trim()).filter(t => t)"
            )
        except Exception:  # noqa: BLE001 - 假页/异常返回空 → 正则回退
            return []
        if not isinstance(raw, list):
            return []  # Mock/空值兜底（假页测试路径）
        return [t for t in raw if isinstance(t, str) and t.strip()]

    @staticmethod
    def _extract_card_citations(page) -> dict[str, int]:
        """v0.16 Phase A3：按结果卡片直读被引数（归一化标题 → 被引）。

        万方结果行 DOM 在 .title-area 内嵌隐藏统计块（display:none，
        视觉不可见但 DOM 存在），其中 `.stat-item.quote` 文本形如"被引 N"。
        title 与 quote 取自同一 .title-area，杜绝跨卡片错配；普通检索与
        高级检索结果页结构一致（runtime/wanfang_metadata_probe/
        result_rows.json 高级流程取证 3/3 行均含该块）。整页 inner_text
        正则看不见该块（隐藏元素不进 innerText），必须按卡片 DOM 提取。
        """
        try:
            cards = page.evaluate(
                "() => Array.from(document.querySelectorAll('.title-area'))"
                ".map(a => ({"
                "t: (a.querySelector('span.title') || {}).innerText || '',"
                "q: (a.querySelector('.stat-item.quote') || {}).innerText || ''}"
                ")).filter(x => (x.t || '').trim())"
            )
        except Exception:  # noqa: BLE001 - 假页/异常 → 空表（回退正则通道）
            return {}
        if not isinstance(cards, list):
            return {}  # Mock/空值兜底（假页测试路径）
        out: dict[str, int] = {}
        for card in cards:
            if not isinstance(card, dict):
                continue
            match = re.search(r"被引\s*(\d+)", card.get("q") or "")
            if not match:
                continue
            norm = WanfangAdapter._normalize_title(card.get("t"))
            if norm and norm not in out:  # 同名卡首现为准（按文档序）
                out[norm] = int(match.group(1))
        return out

    @staticmethod
    def _extract_regex_rows(page) -> list[dict]:
        """正文正则条目（数字.标题 [类型]作者-《来源》年份）。"""
        body = page.inner_text("body") or ""
        rows: list[dict] = []
        for m in _RESULT_RE.finditer(body):
            rows.append({
                "rank": int(m.group(1)),
                "title": m.group(2).strip(),
                "document_type": m.group(3).strip(),
                "authors_raw": m.group(4).strip(),
                "source": m.group(5).strip(),
                "year": m.group(6).strip(),
                "citation_count": int(m.group(7)) if m.group(7) else None,
            })
        return rows

    @staticmethod
    def _normalize_title(text: str | None) -> str:
        """标题归一（DOM 链接匹配用）：去空白与标点，转小写。"""
        if not text:
            return ""
        s = re.sub(r"[\s\u3000]+", "", text)
        return re.sub(r"[^\w]+", "", s, flags=re.UNICODE).lower()

    @staticmethod
    def _extract_title_links(page) -> dict[str, str]:
        """从结果页 DOM 提取 归一化标题 → 链接 映射。

        万方结果项标题渲染为 <a>；代理环境下 href 为代理包装的详情页
        URL，可在受控会话中直接打开。提取失败（页面异常/假页对象）时
        返回空映射，正则兜底行为不变。仅保留 http(s) 链接，
        同文本多条链接取首个。
        """
        links: dict[str, str] = {}
        try:
            anchors = page.evaluate(_LINKS_JS) or []
            for item in anchors:
                href = str(item.get("href") or "").strip()
                text = WanfangAdapter._normalize_title(item.get("text"))
                if href.startswith("http") and text:
                    links.setdefault(text, href)
        except Exception:  # noqa: BLE001 - DOM 提取失败不阻断正则兜底
            return {}
        return links

    @staticmethod
    def _match_link(links: dict[str, str], norm_title: str) -> str | None:
        """按归一化标题匹配链接：精确匹配优先，前缀匹配兜底。

        前缀兜底覆盖"链接文本 = 标题 + 类型徽标"的渲染形态
        （如 "…研究进展[期刊论文]"），要求标题归一化后不少于
        _PREFIX_MATCH_MIN_CHARS 且超长部分不超过 _PREFIX_MATCH_MAX_EXTRA，
        多条命中取超出最短的一条；无匹配返回 None（detail_url 保持空）。
        """
        if not norm_title:
            return None
        href = links.get(norm_title)
        if href:
            return href
        if len(norm_title) < _PREFIX_MATCH_MIN_CHARS:
            return None
        best_text: str | None = None
        best_extra: int | None = None
        for text in links:
            if text.startswith(norm_title):
                extra = len(text) - len(norm_title)
                if extra <= _PREFIX_MATCH_MAX_EXTRA and (
                    best_extra is None or extra < best_extra
                ):
                    best_text, best_extra = text, extra
        return links[best_text] if best_text else None
