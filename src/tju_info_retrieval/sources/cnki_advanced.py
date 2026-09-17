"""CNKI 高级检索页面操作封装（v0.13 Phase 7）。

只负责 Playwright 页面操作，不编译检索条件、不构造任何隐藏 API。
选择器基于 Phase 2.2 实机取证（kns8s/AdvSearch）：

- 检索条件行：div.input-box（默认三行：主题 / 作者 / 文献来源）
- 字段下拉触发器：行内 .sort* 元素（点击展开 li 选项，含"作者单位"）
- 日期：#datebox0/#datebox1（readonly，弹出 xdsoft_datetimepicker 日历，日精度）
- 提交按钮：.btn-search

风控处理（尽力而为，不绕过验证码）：
- ECP 遮罩（IP 超范围提示）尝试关闭
- 字段/日期不存在 → 抛 SearchError，由 CNKIAdapter 回退普通搜索
- 所有页面等待带超时
"""
from __future__ import annotations

import calendar
import logging
import time

from poc_cnki_search import RESULT_TABLE_SELECTOR, is_cnki_page

from tju_info_retrieval.browser.session import BrowserSession
from tju_info_retrieval.sources.base import SearchError

logger = logging.getLogger(__name__)

ADV_SEARCH_URL = "https://kns.cnki.net/kns8s/AdvSearch"

# 行定位
ROW_SELECTOR = "div.input-box"
# 行内可编辑输入框：真实 DOM 审计（Phase 8.3）确认每行可编辑元素是
# 可见的 input[type="text"]（无 id/class/placeholder），前面还有 4 个 hidden input
# （showField 等）。旧选择器 "div.input-box input" 会命中 hidden #showField。
# Playwright 的 :visible 排除 offsetParent=null 的隐藏模板行。
ROW_INPUT_SELECTOR = 'div.input-box input[type="text"]:visible'
# 字段下拉触发器（行内当前字段名区域）
FIELD_TRIGGER_SELECTOR = "[class*='sort']"
# 提交按钮（PoC 实测）
SUBMIT_SELECTOR = ".btn-search"

# xdsoft 日历月份中文名（与 .xdsoft_label.xdsoft_month span 文本一致）
_CN_MONTHS = (
    "一月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "十一月", "十二月",
)


class CNKIAdvancedQueryBuilder:
    """kns8s 高级检索表单填充器（纯 Playwright 操作）。"""

    def __init__(self, session: BrowserSession) -> None:
        self._session = session

    # ---------- 公共入口 ----------

    def build(self, page, keyword: str, query) -> None:
        """进入高级检索页并填充全部条件后点击检索。

        ``keyword`` 为检索词（主题字段内容），必须为 str；
        ``query`` 为 QueryRequest，提供作者/单位/日期等高级条件。
        形参顺序与调用方保持一致（cnki.py 传 build(page, keyword, query)）。

        v0.17 Phase 1：主题为空（纯作者检索）时跳过主题行——真机探针
        （runtime/v017_probe/cnki_author_only_report.json）证实仅作者单条件
        提交有效且结果作者全部命中。
        """
        theme = (keyword or "").strip() or (query.research_direction or "").strip()
        self._goto(page)
        self._dismiss_ecp(page)
        if theme:
            self._fill_row(page, 0, theme)  # 主题（第一行）
        author = (query.author_name or "").strip()
        if author:
            self._fill_row(page, 1, author)  # 作者（默认第二行）
        affiliation = (query.author_affiliation or "").strip()
        if affiliation:
            self._switch_field(page, 2, "作者单位")
            self._fill_row(page, 2, affiliation)
        self._fill_dates(page, query.start_date, query.end_date)
        self._click_search(page)

    # ---------- 页面进入 / 风控 ----------

    def _goto(self, page) -> None:
        try:
            page.goto(ADV_SEARCH_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
        except Exception as exc:
            raise SearchError(f"无法打开 CNKI 高级检索页: {exc}") from exc

    def _dismiss_ecp(self, page) -> None:
        """关闭可能的 ECP 遮罩（IP 超范围/跨境提示）；尽力而为。"""
        for sel in (
            "div[class*='ecpover'] .close",
            ".ecpover-close",
            "[class*='ecpover'] [class*='close']",
        ):
            try:
                close = page.locator(sel).first
                if close.count() > 0 and close.is_visible():
                    close.click(timeout=3000)
                    page.wait_for_timeout(400)
                    return
            except Exception:
                continue
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        except Exception:
            pass

    # ---------- 行填充 ----------

    def _fill_row(self, page, row_index: int, value: str) -> None:
        """填充第 row_index 行（0 基）的输入框。

        部分 Vue 行需先点击激活再填充；读回为空时改用逐键输入。
        """
        row_input = page.locator(ROW_INPUT_SELECTOR).nth(row_index)
        try:
            if row_input.count() == 0:
                raise SearchError(f"高级检索未找到第 {row_index + 1} 行输入框")
            row_input.click(timeout=5000)
            page.wait_for_timeout(300)
            row_input.fill(value, timeout=5000)
            page.wait_for_timeout(200)
            if not (row_input.input_value() or "").strip():
                row_input.press_sequentially(value, delay=20)
        except SearchError:
            raise
        except Exception as exc:
            raise SearchError(
                f"高级检索第 {row_index + 1} 行填充失败: {exc}"
            ) from exc

    def _switch_field(self, page, row_index: int, field_name: str) -> None:
        """点击第 row_index 行的字段下拉，选择 field_name（如 作者单位）。

        DOM 审计（Phase 8.3）：下拉选项为 <li><a title="作者单位" ...>
        （<a> 的 title 唯一标识字段名）；不能用 get_by_text(exact=True)，
        会命中页面其他隐藏"作者单位"文本。用 a[title=...]:visible 精确定位。
        """
        row = page.locator(ROW_SELECTOR).nth(row_index)
        try:
            trigger = row.locator(FIELD_TRIGGER_SELECTOR).first
            if trigger.count() == 0:
                raise SearchError(f"第 {row_index + 1} 行无字段触发器")
            trigger.click(timeout=5000)
            page.wait_for_timeout(600)
            option = page.locator(f'a[title="{field_name}"]:visible').first
            if option.count() == 0:
                raise SearchError(f"字段下拉无选项: {field_name}")
            option.click(timeout=5000)
            page.wait_for_timeout(500)
        except SearchError:
            raise
        except Exception as exc:
            raise SearchError(f"字段切换为 {field_name} 失败: {exc}") from exc

    # ---------- 日期（YYYY.MM → xdsoft 日历） ----------

    def _fill_dates(self, page, start_date: str, end_date: str) -> None:
        """按 YYYY.MM 填写起止日期（开始=当月1日，结束=当月最后一天）。

        策略（v0.13 Phase 8.5）：
        - 首选直接注入 input 值（真实会话验证可行：CNKI readonly datebox
          接受原生 value setter + change 事件，读回正确）。
        - 注入失败（读回不一致）→ 回退 xdsoft 日历交互（箭头/年月下拉）。
        """
        start = (start_date or "").strip()
        end = (end_date or "").strip()
        if start:
            year, month = self._parse_ym(start)
            if not self._inject_date(page, 0, year, month, 1):
                self._pick_date(page, 0, year, month, 1)  # 起始：当月 1 日
        if end:
            year, month = self._parse_ym(end)
            last_day = calendar.monthrange(year, month)[1]
            if not self._inject_date(page, 1, year, month, last_day):
                self._pick_date(page, 1, year, month, last_day)  # 结束：当月最后一天

    def _inject_date(self, page, datebox_index: int, year: int, month: int, day: int) -> bool:
        """直接写入 #datebox{index} 的值；成功且读回一致返回 True。

        使用原生 HTMLInputElement value setter（绕开 readonly）+ 派发
        input/change 事件，触发 CNKI 前端状态更新。
        """
        value = f"{year:04d}-{month:02d}-{day:02d}"
        loc = page.locator(f"#datebox{datebox_index}")
        try:
            if loc.count() == 0:
                return False
            loc.evaluate(
                """(el, v) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype, 'value').set;
                    setter.call(el, v);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                value,
            )
            page.wait_for_timeout(300)
            return (loc.input_value() or "").strip() == value
        except Exception:  # noqa: BLE001 - 注入失败回退日历交互
            return False

    @staticmethod
    def _parse_ym(value: str) -> tuple[int, int]:
        parts = value.split(".")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise SearchError(f"日期格式应为 YYYY.MM: {value}")
        year, month = int(parts[0]), int(parts[1])
        if not 1 <= month <= 12:
            raise SearchError(f"月份越界: {value}")
        return year, month

    def _pick_date(self, page, datebox_index: int, year: int, month: int, day: int) -> None:
        """点击 #datebox{index} 弹出日历并选择指定年月日。

        v0.13 Phase 8.5 跨年策略：
        - 当前与目标月差 |diff| <= 24 → 箭头逐步导航（原逻辑）
        - |diff| > 24 → 优先 xdsoft 年/月下拉快速跳转；失败回退箭头
        """
        datebox = page.query_selector(f"#datebox{datebox_index}")
        if datebox is None:
            raise SearchError(f"未找到日期控件 #datebox{datebox_index}")
        try:
            datebox.click(timeout=5000)
            page.wait_for_timeout(800)
            current = self._current_calendar(page)
            if current is not None:
                diff = self._month_diff(current, year, month)
                if abs(diff) <= 24:
                    self._navigate_by_arrows(page, current, year, month)
                elif not self._pick_by_select(page, year, month):
                    # 大跨度且下拉跳转失败 → 回退箭头（>24 时由 _navigate_by_arrows 报错兜底）
                    self._navigate_by_arrows(page, current, year, month)
            elif not self._pick_by_select(page, year, month):
                raise SearchError("无法读取日历当前年月且年月下拉跳转失败")
            cell = page.locator(
                f"td.xdsoft_date[data-date='{day}']:not(.xdsoft_other_month):visible"
            ).first
            if cell.count() == 0:
                raise SearchError(f"日历未找到 {year}-{month:02d}-{day:02d}")
            cell.click(timeout=5000)
            page.wait_for_timeout(300)
        except SearchError:
            raise
        except Exception as exc:
            raise SearchError(f"日期选择失败 #{datebox_index}: {exc}") from exc

    @staticmethod
    def _month_diff(current: tuple[int, int], year: int, month: int) -> int:
        """当前年月到目标年月的月数差（正=未来，负=过去）。"""
        cy, cm = current
        return (year * 12 + (month - 1)) - (cy * 12 + (cm - 1))

    def _current_calendar(self, page) -> tuple[int, int] | None:
        """读取日历当前年月（.xdsoft_label 的 span 文本，仅可见实例）。

        页面存在桌面/移动两套 xdsoft 实例，必须用 :visible 限定；
        注意 :visible 仅在 page.locator（Playwright 扩展 CSS）中生效，
        不能用 page.query_selector（原生 querySelector 不支持）。
        """
        try:
            year_el = page.locator(".xdsoft_label.xdsoft_year span:visible").first
            month_el = page.locator(".xdsoft_label.xdsoft_month span:visible").first
            if year_el.count() == 0 or month_el.count() == 0:
                return None
            year = int((year_el.inner_text() or "").strip())
            month_text = (month_el.inner_text() or "").strip()
            return year, _CN_MONTHS.index(month_text) + 1
        except Exception:  # noqa: BLE001
            return None

    def _navigate_by_arrows(self, page, current: tuple[int, int], year: int, month: int) -> None:
        """用 .xdsoft_prev/.xdsoft_next 按月步进到目标年月（有界，仅可见实例）。"""
        diff = self._month_diff(current, year, month)
        if diff == 0:
            return
        if abs(diff) > 24:
            raise SearchError(f"日期跨度过大，放弃日历步进: {diff} 个月")
        btn_sel = ".xdsoft_next:visible" if diff > 0 else ".xdsoft_prev:visible"
        btn = page.locator(btn_sel).first
        if btn.count() == 0:
            raise SearchError("日历缺少导航按钮")
        for _ in range(abs(diff)):
            btn.click(timeout=3000)
            page.wait_for_timeout(150)

    def _pick_by_select(self, page, year: int, month: int) -> bool:
        """通过 xdsoft 年/月下拉快速跳转；成功返回 True，任一失败返回 False。

        DOM 审计（Phase 8.5）：点击 .xdsoft_label.xdsoft_year span 展开年选项
        （data-value=真实年份），点击 .xdsoft_label.xdsoft_month span 展开月选项
        （data-value 0 基，一月=0）。页面存在桌面/移动两套实例，全部加
        :visible 限定（必须用 page.locator，query_selector 不支持 :visible）；
        跳转后校验当前年月。
        """
        try:
            year_label = page.locator(".xdsoft_label.xdsoft_year span:visible").first
            if year_label.count() == 0:
                return False
            year_label.click()
            page.wait_for_timeout(300)
            year_opt = page.locator(
                f".xdsoft_yearselect [data-value='{year}']:visible"
            ).first
            if year_opt.count() == 0:
                return False
            year_opt.click()
            page.wait_for_timeout(300)

            month_label = page.locator(".xdsoft_label.xdsoft_month span:visible").first
            if month_label.count() == 0:
                return False
            month_label.click()
            page.wait_for_timeout(300)
            month_opt = page.locator(
                f".xdsoft_monthselect [data-value='{month - 1}']:visible"
            ).first
            if month_opt.count() == 0:
                return False
            month_opt.click()
            page.wait_for_timeout(300)

            # 校验跳转结果（页面可能存在桌面/移动两套日历，需确认命中可见实例）
            now = self._current_calendar(page)
            return now is not None and now == (year, month)
        except Exception:  # noqa: BLE001 - 任一失败回退箭头
            return False

    # ---------- 提交 ----------

    def _click_search(self, page) -> None:
        btn = page.locator(SUBMIT_SELECTOR).first
        if btn.count() == 0 or not btn.is_visible():
            raise SearchError("未找到高级检索按钮 .btn-search")
        btn.click(timeout=5000)
        page.wait_for_timeout(2500)


# ============================================================
# 高级检索结果页检测（v0.13 Phase 8.6 修复）
# ============================================================
# 已知事实（Phase 8.5 真实验证）：kns8s 高级检索提交成功后 **URL 不变**
# （停留 AdvSearch，结果在 SPA 内渲染 table.result-table-list），不会
# 跳转 defaultresult。旧逻辑 wait_for_result_page 只认 defaultresult URL，
# 对高级检索必然 30s 超时 → 误报"未检测到结果页"→ 回退普通搜索。
# 修复：独立检测函数，不依赖 URL，按 A/B/C 优先级判定成功。

# B 兜底：结果行标题链接（table 容器 class 变化时仍能命中有效数据行）
_RESULT_ROW_FALLBACK_SELECTOR = "tr td.name a"
# C 兜底：kns8s 提交后渲染的结果统计条/分页条（高级页状态变化信号）
_RESULT_STATE_SELECTORS = (
    ".pagerTitleBar",
    "div.pager",
    "[class*='pagerTitle']",
)


def _has_result_rows(page) -> bool:
    """结果区域出现有效 tr 数据行（B 判定：标题链接存在即有数据）。"""
    try:
        return page.query_selector(_RESULT_ROW_FALLBACK_SELECTOR) is not None
    except Exception:  # 页面正在导航/已关闭
        return False


def _has_result_state_change(page) -> bool:
    """高级页出现结果统计条/分页条（C 判定：提交后的状态变化）。"""
    for sel in _RESULT_STATE_SELECTORS:
        try:
            if page.query_selector(sel) is not None:
                return True
        except Exception:  # noqa: BLE001 - 单个 selector 失败继续兜底
            continue
    return False


def wait_for_advanced_result_page(
    context,
    timeout_s: float = 30.0,
    poll_interval: float = 0.5,
):
    """等待 kns8s 高级检索结果页（SPA 内渲染，URL 不变）。

    判定优先级（任一命中即返回承载结果的 page，超时返回 None）：

    - A: ``table.result-table-list`` 出现（已验证结果容器，主判定）
    - B: 结果区域出现有效 tr 数据行（``tr td.name a``，宽松兜底）
    - C: 结果统计条/分页条出现（高级页状态变化兜底）

    仅检查 CNKI 站点页面（host 以 cnki.net 结尾）；页面导航/关闭等
    瞬时异常按未命中处理，下一轮重试。
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for page in context.pages:
            try:
                if not is_cnki_page(page.url):
                    continue
                if page.query_selector(RESULT_TABLE_SELECTOR) is not None:
                    return page  # A
                if _has_result_rows(page):
                    return page  # B
                if _has_result_state_change(page):
                    return page  # C
            except Exception:  # noqa: BLE001 - 页面瞬时不可用，下轮重试
                continue
        time.sleep(poll_interval)
    return None
