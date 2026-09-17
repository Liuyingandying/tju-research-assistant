#!/usr/bin/env python3
"""正式测试：WanfangAdapter 解析逻辑（含 synthetic fixture + 真实搜索验证）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest
from playwright.sync_api import sync_playwright

from tju_info_retrieval.sources.base import SearchError
from tju_info_retrieval.sources.wanfang import (
    WanfangAdapter,
    _ADV_TIME_ROW,
    _RESULT_RE,
    _normalize_year,
    _set_publish_time_range,
)

# 模拟万方结果页 body 文本的 HTML fixture
FIXTURE = """<html><body>
<div class="list-wraper">
<span>1.基于被动态制备的星间太赫兹连续变量量子密钥分发方案 | [期刊论文]吴晓东黄端-《物理学报》CSTPCD北大核心EISCICSCD2026年12期 | 摘要：网络.鉴于上述分析…</span>
<span>2.VO2和GST相变材料的太赫兹波响应特性及其可重构超材料器件研究进展 | [期刊论文]罗兴张梦蛟唐露刘柳路学光-《工程科学与技术》2026年 | 摘要：…</span>
</div></body></html>"""

# v0.13：带 DOM 标题链接的 fixture（<a> 文本不影响 _RESULT_RE 条目解析）
LINK_FIXTURE = """<html><body>
<div class="list-wraper">
<span>1.基于被动态制备的星间太赫兹连续变量量子密钥分发方案 | [期刊论文]吴晓东黄端-《物理学报》2026年12期 | 摘要：网络.鉴于上述分析…</span>
<span>2.VO2和GST相变材料的太赫兹波响应特性及其可重构超材料器件研究进展 | [期刊论文]罗兴张梦蛟唐露刘柳路学光-《工程科学与技术》2026年 | 摘要：…</span>
</div>
<a href="https://d.wanfangdata.com.cn/periodical/A">基于被动态制备的星间太赫兹连续变量量子密钥分发方案</a>
<a href="https://d.wanfangdata.com.cn/periodical/B">VO2和GST相变材料的太赫兹波响应特性及其可重构超材料器件研究进展[期刊论文]</a>
<a href="javascript:void(0)">假链接</a>
</body></html>"""


@pytest.fixture(scope="module")
def pw_browser():
    # 单一 sync_playwright/browser 实例：同一线程内并存的第二个
    # sync_playwright 会因 asyncio 事件循环冲突启动失败
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def pw_page(pw_browser):
    page = pw_browser.new_page()
    page.set_content(FIXTURE)
    yield page
    page.close()


@pytest.fixture(scope="module")
def pw_link_page(pw_browser):
    page = pw_browser.new_page()
    page.set_content(LINK_FIXTURE)
    yield page
    page.close()


# v0.13 DOM 优先解析：高级检索内联渲染页（.title-area + 混杂表单/摘要数字正文）
ADV_DOM_FIXTURE = """<html><body>
<div class="advanced-form">
  <div>作者单位 天津大学</div>
  <div>温馨提示 高级检索支持选择检索词精确或模糊匹配 逻辑与, 所有词同时出现在文献中</div>
</div>
<div class="result-list">
  <div class="title-area"><span class="title">基于可调谐太赫兹超表面的双频段全息成像研究</span></div>
  <div class="title-area"><span class="title">太赫兹成像技术的无损检测应用</span></div>
</div>
<div class="abstract">
  93.的圆二色性值.此外,通过调节外部泵浦光能够实现圆二色性的动态调谐.可用于实现
  1 THz,可以通过光改变相位,使得原有结构相位出现反转
  5.动态调谐的示例片段 | [期刊论文]佚名-《杂质》2026 | 摘要…
</div>
<div>1.基于可调谐太赫兹超表面的双频段全息成像研究 | [期刊论文]张三-《期刊X》2026 | 摘要…</div>
</body></html>"""


@pytest.fixture(scope="module")
def pw_adv_dom_page(pw_browser):
    page = pw_browser.new_page()
    page.set_content(ADV_DOM_FIXTURE)
    yield page
    page.close()


# v0.14 发表时间行真实 DOM（探针实证）：
# DIV.time-select > SPAN.hrafwidth > SPAN.title("发表时间：")
# 旧 selector "div.hrafwidth:has(span.title:text-is('发表时间：'))" 因层级
# 错误永远无法命中；新 selector 以 div.time-select 为容器。
TIME_ROW_DOM_FIXTURE = """<html><body>
<div class="advanced-form">
  <div class="time-select">
    <span class="hrafwidth"><span class="title">发表时间：</span></span>
    <span class="ivu-select"><span class="ivu-select-selection">起始 不限</span></span>
    <span class="ivu-select"><span class="ivu-select-selection">结束 至今</span></span>
    <input type="hidden" value="">
    <input type="hidden" value="">
  </div>
</div>
</body></html>"""


@pytest.fixture(scope="module")
def pw_time_row_page(pw_browser):
    page = pw_browser.new_page()
    page.set_content(TIME_ROW_DOM_FIXTURE)
    yield page
    page.close()


class TestWanfangAdvTimeRowSelector:
    """v0.14：发表时间行 selector 回归（真实 DOM fixture）。

    锁定的真实结构：div.time-select > span.hrafwidth > span.title("发表时间：")。
    新选择器（生产常量 _ADV_TIME_ROW）必须命中；旧选择器（div.hrafwidth…）
    因层级错误永不命中，作为回归护栏。
    """

    def test_time_row_selector_hits_real_dom(self, pw_time_row_page):
        page = pw_time_row_page
        # ① 真实 DOM 各层存在
        assert page.locator("div.time-select").count() == 1
        assert page.locator("div.time-select span.hrafwidth").count() == 1
        assert (
            page.locator("div.time-select span.hrafwidth span.title").count() == 1
        )
        assert page.locator("span.title", has_text="发表时间：").count() == 1
        # ② 新 selector（生产常量）可命中
        assert page.locator(_ADV_TIME_ROW).count() == 1
        # ③ 旧 selector（层级错误）不命中
        old = "div.hrafwidth:has(span.title:text-is('发表时间：'))"
        assert page.locator(old).count() == 0

    def test_time_row_contains_two_year_selects(self, pw_time_row_page):
        """发表时间行内两个年份下拉（_set_publish_time_range 依赖 ≥2 个 .ivu-select）。"""
        row = pw_time_row_page.locator(_ADV_TIME_ROW)
        assert row.count() == 1
        assert row.locator(".ivu-select").count() == 2


class TestWanfangParsing:
    def test_regex_matches_first_result(self):
        m = _RESULT_RE.search(
            "1.基于被动态制备的星间太赫兹连续变量量子密钥分发方案 | [期刊论文]吴晓东黄端-《物理学报》2026"
        )
        assert m is not None
        assert m.group(1) == "1"
        assert "太赫兹" in m.group(2)
        assert m.group(3) == "期刊论文"
        assert m.group(5) == "物理学报"
        assert m.group(6) == "2026"

    def test_parse_results_from_fixture(self, pw_page):
        results = WanfangAdapter._parse_results(pw_page, 10)
        assert len(results) == 2
        r = results[0]
        assert r.rank == 1
        assert "太赫兹" in r.title
        assert r.document_type == "期刊论文"
        assert r.source == "物理学报"
        assert r.year == "2026"
        assert r.database == "万方"

    def test_max_count(self, pw_page):
        results = WanfangAdapter._parse_results(pw_page, 1)
        assert len(results) == 1


class TestWanfangDetailUrl:
    """v0.13 detail_url 回填：DOM 链接提取 + 归一化标题匹配。"""

    def test_detail_url_filled_when_href_available(self, pw_link_page):
        """有可用 href：精确匹配与前缀兜底均正确回填 detail_url。"""
        results = WanfangAdapter._parse_results(pw_link_page, 10)
        assert len(results) == 2
        # 第 1 条：链接文本与标题完全一致 → 精确匹配
        assert results[0].detail_url == "https://d.wanfangdata.com.cn/periodical/A"
        # 第 2 条：链接文本 = 标题 + "[期刊论文]" 徽标 → 前缀兜底
        assert results[1].detail_url == "https://d.wanfangdata.com.cn/periodical/B"

    def test_detail_url_none_when_no_href(self, pw_page):
        """无任何 <a> 链接：结果正常解析，detail_url 保持 None（正则兜底不变）。"""
        results = WanfangAdapter._parse_results(pw_page, 10)
        assert len(results) == 2
        assert all(r.detail_url is None for r in results)

    def test_non_http_href_ignored(self, pw_link_page):
        """javascript: 等非 http 链接不参与匹配（假链接不回填）。"""
        results = WanfangAdapter._parse_results(pw_link_page, 10)
        for r in results:
            if r.detail_url is not None:
                assert r.detail_url.startswith("http")


# v0.16 Phase A3：被引按结果卡片直读 fixture（镜像真实 DOM，探针取证
# runtime/wanfang_metadata_probe/result_rows.json：.title-area 内嵌隐藏
# .stat > .stat-content > .stat-item.quote，文本形如"被引 N"）。
# 卡1=被引18、卡2=被引0、卡3=无统计块（无数据形态）。
CARD_CITATION_FIXTURE = """<html><body>
<div class="normal-list periodical-list">
  <div class="title-area"><div class="ajust">
    <span class="index">1.</span>
    <span class="title">基于BIC的全介质太赫兹手性可调超表面</span>
    <div class="stat"><div class="stat-content" style="display: none;">
      <div class="stat-item read">文摘阅读 12</div>
      <div class="stat-item download">下载 1</div>
      <div class="stat-item quote">被引 18</div>
    </div></div>
  </div></div>
  <div class="author-area"><span>杨悦等</span></div>
</div>
<div class="normal-list periodical-list">
  <div class="title-area"><div class="ajust">
    <span class="index">2.</span>
    <span class="title">基于太赫兹时域光谱椭偏技术的缬氨酸手性结构研究</span>
    <div class="stat"><div class="stat-content" style="display: none;">
      <div class="stat-item quote">被引 0</div>
    </div></div>
  </div></div>
  <div class="author-area"><span>张元等</span></div>
</div>
<div class="normal-list periodical-list">
  <div class="title-area"><div class="ajust">
    <span class="index">3.</span>
    <span class="title">微带线在太赫兹低频段的色散特性分析与实验验证</span>
  </div></div>
  <div class="author-area"><span>徐振罗曼等</span></div>
</div>
</body></html>"""


@pytest.fixture(scope="module")
def pw_card_citation_page(pw_browser):
    page = pw_browser.new_page()
    page.set_content(CARD_CITATION_FIXTURE)
    yield page
    page.close()


class TestWanfangCardCitation:
    """被引按卡片直读：18 / 0 / 无块，逐卡对应不错配（Phase A3/A5）。"""

    def test_card_citation_normal_value(self, pw_card_citation_page):
        results = WanfangAdapter._parse_results(pw_card_citation_page, 10)
        assert len(results) == 3
        assert results[0].citation_count == 18

    def test_card_citation_zero_is_explicit_zero(self, pw_card_citation_page):
        """"被引 0" = 明确零引用，不是 None。"""
        results = WanfangAdapter._parse_results(pw_card_citation_page, 10)
        assert results[1].citation_count == 0

    def test_card_citation_missing_block_is_none(self, pw_card_citation_page):
        """无 .stat-item.quote 的卡片 → None（详情 resolver 兜底路径不变）。"""
        results = WanfangAdapter._parse_results(pw_card_citation_page, 10)
        assert results[2].citation_count is None

    def test_card_citation_per_card_mapping(self, pw_card_citation_page):
        """逐卡对应：不同卡片的被引值不得串行错配。"""
        results = WanfangAdapter._parse_results(pw_card_citation_page, 10)
        assert results[0].citation_count == 18
        assert results[1].citation_count == 0
        assert results[2].citation_count is None

    def test_none_and_zero_distinct(self, pw_card_citation_page):
        """语义保持：None（未知）与 0（明确零引用）不相等。"""
        results = WanfangAdapter._parse_results(pw_card_citation_page, 10)
        assert results[1].citation_count == 0
        assert results[2].citation_count is None
        assert results[1].citation_count != results[2].citation_count

    def test_extract_card_citations_direct(self, pw_card_citation_page):
        """卡片通道单独验证：归一化标题键 + 隐藏块可见性不影响提取。"""
        cites = WanfangAdapter._extract_card_citations(pw_card_citation_page)
        assert cites[WanfangAdapter._normalize_title("基于BIC的全介质太赫兹手性可调超表面")] == 18
        assert cites[WanfangAdapter._normalize_title("基于太赫兹时域光谱椭偏技术的缬氨酸手性结构研究")] == 0
        assert WanfangAdapter._normalize_title("微带线在太赫兹低频段的色散特性分析与实验验证") not in cites


class TestWanfangSearch:
    @mock.patch("tju_info_retrieval.sources.wanfang.PortalNavigator")
    def test_search_calls_portal_and_parse(self, mock_portal_cls):
        session = mock.Mock()
        adapter = WanfangAdapter(session)
        # mock PortalNavigator 实例的方法
        portal = mock_portal_cls.return_value
        page = mock.Mock()
        page.inner_text.return_value = (
            "1.标题A | [期刊论文]张三-《期刊X》2026 | 摘要…"
        )
        # open_resource 返回 mock page
        portal.open_resource.return_value = page

        results = adapter.search("太赫兹", 10)
        assert len(results) == 1
        assert results[0].title.strip() == "标题A"
        portal.open_portal.assert_called_once()
        portal.open_resource.assert_called_once_with("万方数据知识服务平台", "中文资源")


class _FakeLocator:
    """脚本化假 locator：记录事件并驱动假页状态（click/输入/计数）。"""

    def __init__(self, page, sel, idx=None):
        self.page = page
        self.sel = sel
        self.idx = idx

    @property
    def first(self):
        # 保留父级 nth(i) 的行索引：真实 Playwright 中
        # locator.nth(i).locator(sel).first 是"第 i 个作用域内首个匹配"，
        # 假页无多候选模型，透传父级 idx 才能让 on_click 知道行号
        return _FakeLocator(self.page, self.sel, idx=self.idx)

    def nth(self, i):
        return _FakeLocator(self.page, self.sel, idx=i)

    def locator(self, sel):
        return _FakeLocator(self.page, f"{self.sel} {sel}", idx=self.idx)

    def filter(self, **kw):
        return self

    def evaluate_all(self, expr):
        return list(getattr(self.page, "hidden_values", []))

    def count(self):
        return self.page.locator_count(self.sel)

    def click(self, **kw):
        self.page.events.append(("click", self.sel, self.idx, None))
        self.page.on_click(self.sel, self.idx)

    def fill(self, text, **kw):
        self.page.events.append(("fill", self.sel, self.idx, text))

    def clear(self, **kw):
        self.page.events.append(("clear", self.sel, self.idx, None))

    def press_sequentially(self, text, **kw):
        self.page.events.append(("press_sequentially", self.sel, self.idx, text))
        self.page.on_type(self.sel, self.idx, text)


class _FakePage:
    """脚本化假 page：模拟普通导航→高级检索→提交→结果的完整状态机。

    row_fields: 11 个输入槽（真实页面 .ivu-input 数量），None=隐藏输入
    （隐藏标签页/推荐检索词 textarea 等）；可见行默认字段为
    主题/题名或关键词/题名（2026-09 真机取证
    runtime/v0.17_author_search_probe/wanfang_adv_dom_diag.json）。
    字段下拉点击（.search-option.field 触发 + :text-is 选项）会真实变更
    对应可见行字段（模拟 iview 状态），与生产 _ADVANCED_ROWS_JS 回读对齐。
    """

    # 初始即"普通搜索结果页"（高级检索首步导航已完成，含智搜域名）
    PLAIN_URL = "https://lzxepffr-x.p.lib.tju.edu.cn/paper?q=%E5%A4%AA%E8%B5%AB%E5%85%B9&p=1"
    # 默认：前 2 个隐藏输入在后，可见行字段为主题/题名或关键词/题名（真实默认）
    DEFAULT_FIELDS = [None, None, "主题", "题名或关键词", "题名",
                      None, None, None, None, None, None]

    def __init__(self, mode="success", values=None, sync_inputs=True, row_fields=None,
                 hidden_values=None):
        self.url = self.PLAIN_URL
        self.events: list = []
        self.row_fields = (
            row_fields if row_fields is not None else list(self.DEFAULT_FIELDS)
        )
        self.input_values = (
            list(values) if values is not None else [""] * len(self.row_fields)
        )
        self.hidden_values = (
            list(hidden_values) if hidden_values is not None else []
        )
        self.mode = mode          # success | submit_fail
        self.sync_inputs = sync_inputs  # False=iview 不接收模型同步
        self.title_area_count = 1
        self.advanced_visited = False
        # 可见输入在 input_values 中的位置（与 _ADVANCED_ROWS_JS 的可见行一致）
        self.visible_positions = [
            i for i, f in enumerate(self.row_fields) if f is not None
        ]

    def locator(self, sel):
        return _FakeLocator(self, sel)

    def fill(self, sel, text, **kw):
        """页面级 fill（Playwright API：普通输入框，非 iview）。"""
        self.events.append(("fill", sel, None, text))

    def goto(self, url, **kw):
        self.events.append(("goto", url, None, None))
        self.advanced_visited = True
        self.url = url

    def evaluate(self, js, *args):
        if "rows.push" in js:
            # 与生产 _ADVANCED_ROWS_JS 对齐：仅可见行（field != None）
            rows = []
            for i, fld in enumerate(self.row_fields):
                if fld is None:
                    continue
                val = self.input_values[i] if i < len(self.input_values) else ""
                rows.append({"field": fld, "value": val})
            return rows
        return None

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, *a, **k):
        pass

    def inner_text(self, sel):
        return "1.标题A | [期刊论文]张三-《期刊X》2026 | 摘要…"

    def locator_count(self, sel):
        if "title-area" in sel:
            return self.title_area_count
        if ".ivu-select" in sel:
            return 2  # 发表时间行：起始/结束两个下拉
        return 1

    def on_click(self, sel, idx):
        # 字段下拉：.search-option.field 触发 + .ivu-select-item:text-is('字段')
        # → 对应可见行字段真实变更（精确/模糊下拉为 .search-option，不变更字段）
        if ".search-option.field" in sel and ":text-is('" in sel:
            field = sel.split(":text-is('", 1)[1].split("')", 1)[0]
            if idx is not None and 0 <= idx < len(self.visible_positions):
                self.row_fields[self.visible_positions[idx]] = field
        if "submit-btn" in sel:
            if self.mode == "success":
                self.url = "https://lzxepffr-x.p.lib.tju.edu.cn/paper?q=adv"
                self.title_area_count = 1
            else:
                self.title_area_count = 0  # 渲染失败 → 等待超时

    def on_type(self, sel, idx, text):
        if ".ivu-input" in sel and self.sync_inputs:
            if idx is not None and idx < len(self.visible_positions):
                pos = self.visible_positions[idx]
                if pos < len(self.input_values):
                    self.input_values[pos] = text


class TestWanfangAdvancedSearch(unittest.TestCase):
    """v0.13 高级检索（作者单位）路径：分支／键盘输入／提交前校验／失败不降级。"""

    def _adapter(self, fake):
        adapter = WanfangAdapter(mock.Mock())
        portal = mock.Mock()
        portal.open_portal.return_value = fake
        portal.open_resource.return_value = fake
        adapter._portal = portal
        return adapter

    def _req(self, affiliation="天津大学"):
        req = mock.Mock()
        req.author_name = ""
        req.author_affiliation = affiliation
        req.research_direction = "太赫兹"
        req.start_date = ""
        req.end_date = ""
        return req

    def test_advanced_search_preserves_original_keyword(self):
        """高级检索关键词必须保持原始字符串，禁止使用扩展 OR 表达式。"""
        fake = _FakePage()
        adapter = self._adapter(fake)
        req = mock.Mock()
        req.author_name = ""
        req.author_affiliation = "天津大学"
        req.research_direction = "太赫兹"  # 用户原始输入
        req.start_date = ""
        req.end_date = ""
        expanded = "(太赫兹 OR THz OR 太赫兹波 OR Terahertz)"  # 上游扩展产物
        with mock.patch("tju_info_retrieval.sources.wanfang.time.sleep"):
            results = adapter.search(expanded, 10, query_context=req)
        types = [e for e in fake.events if e[0] == "press_sequentially"]
        kw_typed = [e[3] for e in types if e[2] == 1]  # 关键词行（idx=1）
        self.assertTrue(kw_typed, "应有关键词行逐键输入")
        self.assertEqual(kw_typed[-1], "太赫兹", "关键词必须为原始字符串")
        self.assertNotIn("OR", kw_typed[-1])
        self.assertEqual(len(results), 1)

    def _search(self, adapter, fake, affiliation="天津大学"):
        with mock.patch("tju_info_retrieval.sources.wanfang.time.sleep"):
            return adapter.search("太赫兹", 10, query_context=self._req(affiliation))

    def test_no_affiliation_uses_plain_path(self):
        fake = _FakePage()
        adapter = self._adapter(fake)
        results = self._search(adapter, fake, affiliation="")
        self.assertFalse(fake.advanced_visited, "无机构条件不得进入高级检索")
        self.assertIn("fill", [e[0] for e in fake.events])
        self.assertTrue(any(e[1] == "#search-input" for e in fake.events))
        self.assertEqual(len(results), 1)

    def test_affiliation_uses_advanced_path(self):
        fake = _FakePage()
        adapter = self._adapter(fake)
        results = self._search(adapter, fake)
        self.assertTrue(fake.advanced_visited)
        clicks = [e for e in fake.events if e[0] == "click"]
        self.assertTrue(any(".ivu-select" in e[1] for e in clicks), "应打开字段下拉")
        self.assertTrue(any("作者单位" in e[1] for e in clicks), "应选择作者单位")
        self.assertTrue(any("submit-btn" in e[1] for e in clicks), "应点击提交")
        self.assertEqual(len(results), 1)

    def test_input_uses_keyboard_not_fill_on_iview(self):
        fake = _FakePage()
        adapter = self._adapter(fake)
        self._search(adapter, fake)
        for e in fake.events:
            if e[0] == "fill":
                self.assertNotIn(".ivu-input", e[1], "iview 输入框禁止 fill")
        types = [e for e in fake.events if e[0] == "press_sequentially"]
        self.assertTrue(any(e[3] == "天津大学" and e[2] == 0 for e in types))
        self.assertTrue(any(e[3] == "太赫兹" and e[2] == 1 for e in types))

    def test_hidden_inputs_are_skipped_and_not_written(self):
        """真实 DOM：存在隐藏 .ivu-input，精确选择可见行，错误输入不写入隐藏槽。"""
        preset = ["HIDDEN0", "HIDDEN1", "", "", "",
                  "HIDDEN5", "HIDDEN6", "HIDDEN7", "HIDDEN8", "HIDDEN9", "HIDDEN10"]
        fake = _FakePage(values=preset)  # 默认 row_fields：可见行位于 2/3/4
        adapter = self._adapter(fake)
        results = self._search(adapter, fake)
        # 可见行（按字段定位）被写入
        self.assertEqual(fake.input_values[2], "天津大学")   # 作者单位行
        self.assertEqual(fake.input_values[3], "太赫兹")     # 题名或关键词行
        self.assertEqual(fake.input_values[4], "")           # 题目行（未填）
        # 隐藏槽保持原值：错误输入未写入
        self.assertEqual(fake.input_values[0], "HIDDEN0")
        self.assertEqual(fake.input_values[1], "HIDDEN1")
        self.assertEqual(fake.input_values[5:], preset[5:])
        self.assertEqual(len(results), 1)

    def test_pre_submit_validation_aborts_when_inputs_empty(self):
        fake = _FakePage(values=[""] * 11, sync_inputs=False)
        adapter = self._adapter(fake)
        with self.assertRaises(SearchError) as ctx:
            self._search(adapter, fake)
        self.assertIn("未就绪", str(ctx.exception))
        self.assertFalse(
            any("submit-btn" in e[1] for e in fake.events), "输入未就绪不得提交"
        )

    def test_advanced_failure_does_not_fall_back_to_plain(self):
        fake = _FakePage(mode="submit_fail")
        adapter = self._adapter(fake)
        with mock.patch(
            "tju_info_retrieval.sources.wanfang._ADVANCED_RESULT_TIMEOUT_S", 0.3
        ):
            with self.assertRaises(SearchError) as ctx:
                self._search(adapter, fake)
        self.assertIn("未返回结果", str(ctx.exception))
        # 失败后不得回退普通搜索：普通导航只发生一次（高级路径前置步骤）
        plain_fills = [e for e in fake.events if e[0] == "fill" and e[1] == "#search-input"]
        self.assertEqual(len(plain_fills), 1, "高级失败不得再次走普通搜索")

    def test_start_end_dates_enter_advanced(self):
        """v0.13：start_date/end_date 映射到高级检索发表时间下拉（年粒度）。"""
        fake = _FakePage()
        adapter = self._adapter(fake)
        req = mock.Mock()
        req.author_name = ""
        req.author_affiliation = "天津大学"
        req.research_direction = "太赫兹"
        req.start_date = "2020.05"
        req.end_date = "2024.11"
        with mock.patch("tju_info_retrieval.sources.wanfang.time.sleep"):
            results = adapter.search("太赫兹", 10, query_context=req)
        clicks = [(e[1], e[2]) for e in fake.events if e[0] == "click"]
        self.assertTrue(any(".ivu-select" in s and i == 0 for s, i in clicks),
                        "应点击起始下拉")
        self.assertTrue(any(".ivu-select" in s and i == 1 for s, i in clicks),
                        "应点击结束下拉")
        self.assertTrue(any("2020年" in s for s, _ in clicks), "应点击 2020年 选项")
        self.assertTrue(any("2024年" in s for s, _ in clicks), "应点击 2024年 选项")
        self.assertEqual(len(results), 1)

    def test_no_dates_skips_time_row(self):
        """无时间条件：不触碰发表时间行（行为不变）。"""
        fake = _FakePage()
        adapter = self._adapter(fake)
        req = mock.Mock()
        req.author_name = ""
        req.author_affiliation = "天津大学"
        req.research_direction = "太赫兹"
        req.start_date = ""
        req.end_date = ""
        with mock.patch("tju_info_retrieval.sources.wanfang.time.sleep"):
            results = adapter.search("太赫兹", 10, query_context=req)
        clicks = [e[1] for e in fake.events if e[0] == "click"]
        self.assertFalse(any("发表时间" in s for s in clicks), "不应触碰发表时间行")
        self.assertEqual(len(results), 1)

    def test_publish_time_accepts_plain_year_readback(self):
        """回归：回读“2020”（无“年”后缀）不再被误判为设置失败。"""
        fake = _FakePage(hidden_values=["2020", "2024"])
        adapter = self._adapter(fake)
        req = self._req()
        req.start_date = "2020.05"
        req.end_date = "2024.11"
        with mock.patch("tju_info_retrieval.sources.wanfang.time.sleep"):
            results = adapter.search("太赫兹", 10, query_context=req)
        clicks = [e[1] for e in fake.events if e[0] == "click"]
        self.assertTrue(any("2020年" in s for s in clicks), "应点击起始年 2020年")
        self.assertTrue(any("2024年" in s for s in clicks), "应点击结束年 2024年")
        self.assertEqual(len(results), 1, "归一化校验后应正常返回结果")

    def test_publish_time_true_mismatch_still_raises(self):
        """回读 2021 与期望 2024：真正不一致仍抛 SearchError。"""
        fake = _FakePage(hidden_values=["2020", "2021"])
        adapter = self._adapter(fake)
        req = self._req()
        req.start_date = "2020.05"
        req.end_date = "2024.11"
        with mock.patch("tju_info_retrieval.sources.wanfang.time.sleep"):
            with self.assertRaises(SearchError) as ctx:
                adapter.search("太赫兹", 10, query_context=req)
        self.assertIn("发表时间设置未生效", str(ctx.exception))


class TestWanfangAuthorSearch(unittest.TestCase):
    """v0.17 Phase 1：作者字段进入高级检索（真机探针驱动的行规划语义）。

    真机取证（runtime/v0.17_author_search_probe/）：
    - 可见行 0-2 默认字段 主题/题名或关键词/题名，字段下拉 = .search-option.field；
    - 作者模糊查询服务端恒 0（作者:(张伟)），必须切「精确」（作者:("姓名")）；
    - 人名可作普通搜索导航词进入 /paper（空主题场景域名发现）。
    """

    def _adapter(self, fake):
        adapter = WanfangAdapter(mock.Mock())
        portal = mock.Mock()
        portal.open_portal.return_value = fake
        portal.open_resource.return_value = fake
        adapter._portal = portal
        return adapter

    @staticmethod
    def _req(person="", affiliation="", direction="太赫兹",
             start_date="", end_date="", ranking_mode="COMPREHENSIVE"):
        req = mock.Mock()
        req.author_name = person
        req.author_affiliation = affiliation
        req.research_direction = direction
        req.start_date = start_date
        req.end_date = end_date
        req.ranking_mode = ranking_mode
        return req

    def _search(self, adapter, fake, req, keyword="太赫兹"):
        with mock.patch("tju_info_retrieval.sources.wanfang.time.sleep"):
            return adapter.search(keyword, 10, query_context=req)

    def _clicks(self, fake):
        return [(e[1], e[2]) for e in fake.events if e[0] == "click"]

    def test_person_only_enters_advanced(self):
        """仅人名 → 必须进入高级检索（不再退化为普通关键词搜索）。"""
        fake = _FakePage()
        adapter = self._adapter(fake)
        results = self._search(adapter, fake, self._req(person="张三", direction=""), keyword="")
        self.assertTrue(fake.advanced_visited, "纯人名必须走高级检索")
        self.assertEqual(len(results), 1)

    def test_person_only_author_field_and_exact_mode(self):
        """纯人名：row0 切「作者」+ 切「精确」+ 逐键输入人名；关键词行不输入。"""
        fake = _FakePage()
        adapter = self._adapter(fake)
        results = self._search(adapter, fake, self._req(person="张三", direction=""), keyword="")
        clicks = self._clicks(fake)
        self.assertTrue(any(s == ".search-option.field" and i == 0 for s, i in clicks),
                        "应点击 row0 字段下拉")
        self.assertTrue(any("作者" in s and ":text-is('作者')" in s for s, _ in clicks),
                        "应选择「作者」字段（精确文本匹配）")
        self.assertTrue(any(s == ".search-option" and i == 1 for s, i in clicks),
                        "应点击 row0 精确/模糊下拉")
        self.assertTrue(any(":text-is('精确')" in s for s, _ in clicks),
                        "作者行必须切「精确」（模糊查询服务端恒 0）")
        types = [(e[2], e[3]) for e in fake.events if e[0] == "press_sequentially"]
        self.assertIn((0, "张三"), types, "作者行（可见行 0）应输入人名")
        self.assertTrue(all(v != "太赫兹" for _, v in types), "纯人名不得输入主题词")
        self.assertEqual(fake.input_values[2], "张三")
        self.assertEqual(fake.input_values[3], "", "关键词行不得被填入人名")
        self.assertEqual(len(results), 1)

    def test_person_only_nav_keyword_is_person(self):
        """空主题：普通搜索导航词使用人名（仅域名发现，非检索语义）。"""
        fake = _FakePage()
        adapter = self._adapter(fake)
        self._search(adapter, fake, self._req(person="张三", direction=""), keyword="")
        fills = [e for e in fake.events if e[0] == "fill" and e[1] == "#search-input"]
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0][3], "张三")

    def test_topic_and_person(self):
        """topic + person：作者行(row0) + 题名或关键词行(row1)，双条件。"""
        fake = _FakePage()
        adapter = self._adapter(fake)
        results = self._search(adapter, fake, self._req(person="张三"))
        self.assertTrue(fake.advanced_visited)
        self.assertEqual(fake.input_values[2], "张三")   # row0 作者
        self.assertEqual(fake.input_values[3], "太赫兹")  # row1 题名或关键词（原始词）
        self.assertEqual(len(results), 1)

    def test_person_and_affiliation(self):
        """person + affiliation（空主题）：作者(row0) + 作者单位(row1)。"""
        fake = _FakePage()
        adapter = self._adapter(fake)
        results = self._search(
            adapter, fake,
            self._req(person="张三", affiliation="天津大学", direction=""),
            keyword="",
        )
        self.assertTrue(fake.advanced_visited)
        clicks = self._clicks(fake)
        self.assertTrue(any(s == ".search-option.field" and i == 1 for s, i in clicks),
                        "作者单位应占用 row1")
        self.assertEqual(fake.input_values[2], "张三")     # row0 作者
        self.assertEqual(fake.input_values[3], "天津大学")  # row1 作者单位
        self.assertEqual(fake.input_values[4], "")          # row2 未填
        self.assertEqual(len(results), 1)

    def test_topic_person_affiliation_three_conditions(self):
        """topic + person + affiliation：三行三条件（row2 切题名或关键词）。"""
        fake = _FakePage()
        adapter = self._adapter(fake)
        results = self._search(
            adapter, fake,
            self._req(person="张三", affiliation="天津大学", direction="太赫兹"),
        )
        self.assertTrue(fake.advanced_visited)
        clicks = self._clicks(fake)
        self.assertTrue(any(s == ".search-option.field" and i == 2 for s, i in clicks),
                        "关键词行应占用 row2（前两行被占满）")
        self.assertTrue(any(":text-is('题名或关键词')" in s for s, _ in clicks),
                        "row2 应切换为题名或关键词")
        self.assertEqual(fake.input_values[2], "张三")     # row0 作者
        self.assertEqual(fake.input_values[3], "天津大学")  # row1 作者单位
        self.assertEqual(fake.input_values[4], "太赫兹")   # row2 关键词
        self.assertEqual(len(results), 1)

    def test_citation_mode_keeps_same_conditions(self):
        """CITATION 模式只改排序/配额，不改作者/主题条件（与综合推荐一致）。"""
        fake_plain = _FakePage()
        adapter_plain = self._adapter(fake_plain)
        self._search(adapter_plain, fake_plain,
                     self._req(person="张三", direction="太赫兹"))
        fake_cite = _FakePage()
        adapter_cite = self._adapter(fake_cite)
        with mock.patch.object(WanfangAdapter, "_apply_native_citation_sort") as sort:
            self._search(adapter_cite, fake_cite,
                         self._req(person="张三", direction="太赫兹",
                                   ranking_mode="CITATION"))
        sort.assert_called_once()  # CITATION 触发原生被引排序
        # 除排序外的表单交互逐事件一致（字段切换/输入/提交完全相同）
        self.assertEqual(fake_plain.events, fake_cite.events)

    def test_affiliation_only_row0_unchanged(self):
        """回归：仅单位（无作者）仍 row0→作者单位 + row1 关键词（v0.13 行为）。"""
        fake = _FakePage()
        adapter = self._adapter(fake)
        results = self._search(
            adapter, fake, self._req(person="", affiliation="天津大学")
        )
        clicks = self._clicks(fake)
        self.assertTrue(any(s == ".search-option.field" and i == 0 for s, i in clicks))
        self.assertTrue(any(":text-is('作者单位')" in s for s, _ in clicks))
        self.assertFalse(any(":text-is('精确')" in s for s, _ in clicks),
                         "无作者条件不得触碰精确/模糊下拉")
        self.assertEqual(fake.input_values[2], "天津大学")
        self.assertEqual(fake.input_values[3], "太赫兹")
        self.assertEqual(len(results), 1)


class TestNormalizeYear(unittest.TestCase):
    """回读年份归一：“2020年”与“2020”视为同一值（真机两种形态均出现）。"""

    def test_suffix_vs_plain_equivalent(self):
        self.assertEqual(_normalize_year("2020年"), _normalize_year("2020"))

    def test_plain_vs_suffix_equivalent(self):
        self.assertEqual(_normalize_year("2020"), _normalize_year("2020年"))

    def test_true_mismatch_detected(self):
        self.assertNotEqual(_normalize_year("2020"), _normalize_year("2021"))

    def test_none_and_empty(self):
        self.assertEqual(_normalize_year(None), "")
        self.assertEqual(_normalize_year(""), "")

    def test_whitespace_stripped(self):
        self.assertEqual(_normalize_year(" 2020年 "), "2020")


# 真实 DOM 结构（探针 time_row.json 实证：time-select 行 + 两个年份下拉 +
# 隐藏 input 携带选中值；下拉项含年份列表）
PUBLISH_TIME_FIXTURE = """<html><body>
<div class="advanced-search-lay">
  <div class="time-select">
    <span class="hrafwidth"><span class="title">发表时间：</span></span>
    <div class="ivu-select ivu-select-single">
      <div class="ivu-select-selection">
        <input type="hidden" value="不限">
        <span class="ivu-select-selected-value"> 不限 </span>
      </div>
      <div class="ivu-select-dropdown" style="display:none">
        <ul class="ivu-select-dropdown-list">
          <li class="ivu-select-item ivu-select-item-selected">不限</li>
          <li class="ivu-select-item">2026年</li><li class="ivu-select-item">2025年</li>
          <li class="ivu-select-item">2024年</li><li class="ivu-select-item">2023年</li>
          <li class="ivu-select-item">2022年</li><li class="ivu-select-item">2021年</li>
          <li class="ivu-select-item">2020年</li><li class="ivu-select-item">2005年</li>
        </ul>
      </div>
    </div>
    <span>-</span>
    <div class="ivu-select ivu-select-single">
      <div class="ivu-select-selection">
        <input type="hidden" value="至今">
        <span class="ivu-select-selected-value"> 至今 </span>
      </div>
      <div class="ivu-select-dropdown" style="display:none">
        <ul class="ivu-select-dropdown-list">
          <li class="ivu-select-item ivu-select-item-selected">至今</li>
          <li class="ivu-select-item">2026年</li><li class="ivu-select-item">2025年</li>
          <li class="ivu-select-item">2024年</li><li class="ivu-select-item">2023年</li>
          <li class="ivu-select-item">2022年</li><li class="ivu-select-item">2021年</li>
          <li class="ivu-select-item">2020年</li><li class="ivu-select-item">2005年</li>
        </ul>
      </div>
    </div>
  </div>
</body></html>"""


class TestPublishTimeRangeReadback:
    """真实 DOM 结构下的回读校验（set_content 静态页 + 预置隐藏值）。"""

    @pytest.fixture(scope="class")
    def time_page(self, pw_browser):
        page = pw_browser.new_page()
        page.set_content(PUBLISH_TIME_FIXTURE)
        yield page
        page.close()

    def _preset_hidden(self, page, start, end):
        page.evaluate(
            """(vals) => {
            const hs = document.querySelectorAll('.ivu-select-selection input[type=hidden]');
            if (hs.length >= 2) { hs[0].value = vals[0]; hs[1].value = vals[1]; }
            }""",
            [start, end],
        )

    def _open_dropdowns(self, page):
        """静态页预展开下拉（真实页面由点击展开；set_content 无 JS）。"""
        page.evaluate(
            """() => {
            document.querySelectorAll('.ivu-select-dropdown')
                .forEach(d => { d.style.display = 'block'; });
            }"""
        )
        page.wait_for_timeout(300)

    def test_suffix_readback_passes(self, time_page):
        """回读“2020年/2024年”（带年后缀）：归一化后通过。"""
        self._preset_hidden(time_page, "2020年", "2024年")
        self._open_dropdowns(time_page)
        _set_publish_time_range(time_page, "2020.05", "2024.11")  # 不抛

    def test_plain_readback_passes(self, time_page):
        """回读“2020/2024”（无年后缀）：归一化后通过。"""
        self._preset_hidden(time_page, "2020", "2024")
        self._open_dropdowns(time_page)
        _set_publish_time_range(time_page, "2020.05", "2024.11")  # 不抛

    def test_true_mismatch_raises(self, time_page):
        """回读“2019年/2021年”：与期望不一致 → SearchError。"""
        self._preset_hidden(time_page, "2019年", "2021年")
        self._open_dropdowns(time_page)
        with pytest.raises(SearchError) as ctx:
            _set_publish_time_range(time_page, "2020.05", "2024.11")
        assert "发表时间设置未生效" in str(ctx.value)


class TestWanfangDomParse:
    """v0.13 DOM 优先解析：DOM 标题优先、无 DOM 回退正则、混杂正文不污染。"""

    def test_dom_titles_parsed_priority(self, pw_adv_dom_page):
        results = WanfangAdapter._parse_results(pw_adv_dom_page, 10)
        assert len(results) == 2
        assert results[0].title == "基于可调谐太赫兹超表面的双频段全息成像研究"
        assert results[1].title == "太赫兹成像技术的无损检测应用"
        assert results[0].database == "万方"
        # 与正则命中条目匹配的 DOM 标题获得补充字段（作者/来源/年份）
        assert results[0].source == "期刊X"
        assert results[0].year == "2026"
        # 无对应正则条目的 DOM 标题仅保留标题（不污染）
        assert results[1].source is None

    def test_messy_body_digits_not_polluting(self, pw_adv_dom_page):
        results = WanfangAdapter._parse_results(pw_adv_dom_page, 10)
        titles = [r.title for r in results]
        for bad in ("动态调谐的示例片段", "93.的圆二色性值", "1 THz", "温馨提示"):
            assert not any(bad in t for t in titles), f"混杂正文污染结果: {bad}"

    def test_regex_fallback_when_no_dom(self, pw_page):
        # FIXTURE 无 .title-area → 回退正文正则（原行为不变）
        results = WanfangAdapter._parse_results(pw_page, 10)
        assert len(results) == 2
        assert "太赫兹" in results[0].title
        assert results[0].source == "物理学报"


SPA_CITATION_SORT_FIXTURE = """<html><body>
<div class="sort-area">
  <div class="sort-item down">相关度</div>
  <div id="citation-sort" class="sort-item">被引频次</div>
</div>
<div id="loading" class="loading" style="display:none">loading</div>
<div id="results">
  <div class="title-area"><span class="title">综合结果A</span>
    <span class="stat-item quote">被引 2</span></div>
  <div class="title-area"><span class="title">综合结果B</span>
    <span class="stat-item quote">被引 8</span></div>
</div>
<script>
window.zeroObserved = false;
document.querySelector('#citation-sort').addEventListener('click', () => {
  document.querySelector('.sort-item.down').classList.remove('down');
  document.querySelector('#citation-sort').classList.add('down');
  document.querySelector('#loading').style.display = 'block';
  document.querySelector('#results').innerHTML = '';
  window.zeroObserved = document.querySelectorAll('.title-area span.title').length === 0;
  setTimeout(() => {
    document.querySelector('#results').innerHTML = `
      <div class="title-area"><span class="title">高引用结果B</span>
        <span class="stat-item quote">被引 88</span></div>
      <div class="title-area"><span class="title">高引用结果A</span>
        <span class="stat-item quote">被引 22</span></div>`;
    document.querySelector('#loading').style.display = 'none';
    history.replaceState({}, '', '?o=citation');
  }, 650);
});
</script></body></html>"""


class TestWanfangAdvancedCitationHydration:
    """v0.16：高级检索点击被引排序后，须等待慢 SPA 重挂载再解析。"""

    @pytest.fixture
    def citation_spa_page(self, pw_browser):
        page = pw_browser.new_page()
        page.set_content(SPA_CITATION_SORT_FIXTURE)
        yield page
        page.close()

    def test_advanced_citation_sort_waits_for_hydrated_results(self, citation_spa_page):
        adapter = WanfangAdapter(mock.Mock())
        adapter._apply_native_citation_sort(citation_spa_page, advanced=True)
        results = adapter._parse_results(citation_spa_page, 30)
        assert [r.title for r in results] == ["高引用结果B", "高引用结果A"]

    def test_advanced_citation_count_survives_remount(self, citation_spa_page):
        WanfangAdapter(mock.Mock())._apply_native_citation_sort(
            citation_spa_page, advanced=True
        )
        results = WanfangAdapter._parse_results(citation_spa_page, 30)
        assert [r.citation_count for r in results] == [88, 22]

    def test_parser_is_not_called_during_transient_zero(self, citation_spa_page):
        WanfangAdapter(mock.Mock())._apply_native_citation_sort(
            citation_spa_page, advanced=True
        )
        assert citation_spa_page.evaluate("() => window.zeroObserved") is True
        assert citation_spa_page.locator(".title-area span.title").count() == 2
        assert len(WanfangAdapter._parse_results(citation_spa_page, 30)) == 2

    def test_citation_sort_is_active_and_loading_is_gone(self, citation_spa_page):
        WanfangAdapter(mock.Mock())._apply_native_citation_sort(
            citation_spa_page, advanced=True
        )
        state = WanfangAdapter._citation_result_state(citation_spa_page)
        assert state["active"] is True
        assert state["title_count"] == 2
        assert state["loading"] is False

    def test_plain_wanfang_citation_navigation_is_unchanged(self):
        page = mock.Mock()
        page.url = "https://s.wanfangdata.com.cn/paper?q=x"
        page.goto.side_effect = lambda url, **kwargs: setattr(page, "url", url)
        WanfangAdapter(mock.Mock())._apply_native_citation_sort(page, advanced=False)
        page.goto.assert_called_once()
        assert "o=%7B" in page.goto.call_args.args[0]

    @mock.patch("tju_info_retrieval.sources.wanfang.PortalNavigator")
    def test_advanced_citation_keeps_exact_keyword_and_affiliation(self, portal_cls):
        page = mock.Mock()
        portal_cls.return_value.open_resource.return_value = page
        ctx = mock.Mock(
            research_direction="太赫兹",
            author_affiliation="浙江大学",
            start_date="",
            end_date="",
            ranking_mode="CITATION",
        )
        adapter = WanfangAdapter(mock.Mock())
        adapter._portal = portal_cls.return_value
        with mock.patch.object(adapter, "_advanced_search", return_value=[]) as advanced:
            adapter.search("(太赫兹 OR THz)", 30, query_context=ctx)
        assert advanced.call_args.args[1] == "浙江大学"
        assert advanced.call_args.kwargs["original_keyword"] == "太赫兹"
        assert advanced.call_args.kwargs["citation_sort"] is True


# ============================================================
# v0.16：万方关键词路由回归（source-specific query routing）
# 上游扩展 Boolean（如 "(太赫兹 OR THz OR 太赫兹波 OR Terahertz)"）
# 不被万方普通搜索框/高级表单接受 → 万方所有页面检索必须用
# query_context.research_direction 原始词；CITATION 模式不改关键词。
# ============================================================

_EXPANDED = "(太赫兹 OR THz OR 太赫兹波 OR Terahertz)"
_ROW_TEXT = "1.标题A | [期刊论文]张三-《期刊X》2026 | 摘要…"


def _search_page_mock():
    """普通检索路径的最小 mock page（正则通道可解析出 1 条结果）。"""
    page = mock.Mock()
    page.inner_text.return_value = _ROW_TEXT
    page.url = "https://s.wanfangdata.com.cn/paper?q=%E5%A4%AA%E8%B5%AB%E5%85%B9"
    return page


def _filled_keywords(page) -> list[str]:
    """收集普通路径实际填入搜索框的关键词。"""
    return [
        call.args[1]
        for call in page.fill.call_args_list
        if call.args and call.args[0] == "#search-input"
    ]


class TestWanfangKeywordRouting:
    @mock.patch("tju_info_retrieval.sources.wanfang.PortalNavigator")
    def test_a_normal_search_uses_original_keyword(self, mock_portal_cls):
        """A：普通检索收到扩展 Boolean，实际提交原始词「太赫兹」。"""
        portal = mock_portal_cls.return_value
        page = _search_page_mock()
        portal.open_resource.return_value = page
        ctx = mock.Mock(
            research_direction="太赫兹", author_affiliation="", author_name=""
        )

        adapter = WanfangAdapter(mock.Mock())
        adapter._portal = portal
        adapter.search(_EXPANDED, 10, query_context=ctx)

        filled = _filled_keywords(page)
        assert filled == ["太赫兹"]
        assert _EXPANDED not in filled

    @mock.patch("tju_info_retrieval.sources.wanfang.PortalNavigator")
    def test_b_citation_mode_uses_same_keyword(self, mock_portal_cls):
        """B：RankingMode.CITATION 不改关键词（与综合推荐完全一致）。"""
        portal = mock_portal_cls.return_value
        page = _search_page_mock()
        portal.open_resource.return_value = page
        ctx = mock.Mock(
            research_direction="太赫兹",
            author_affiliation="",
            author_name="",
            ranking_mode="CITATION",
        )

        adapter = WanfangAdapter(mock.Mock())
        adapter._portal = portal
        adapter.search(_EXPANDED, 30, query_context=ctx)

        assert _filled_keywords(page) == ["太赫兹"]

    @mock.patch("tju_info_retrieval.sources.wanfang.PortalNavigator")
    def test_c_advanced_search_keeps_original_keyword(self, mock_portal_cls):
        """C：有作者单位进高级检索 → 仍用原始词「太赫兹」（既有行为保持）。"""
        portal = mock_portal_cls.return_value
        page = mock.Mock()
        portal.open_resource.return_value = page
        ctx = mock.Mock(
            research_direction="太赫兹",
            author_affiliation="天津大学",
            author_name="",
            start_date="",
            end_date="",
            ranking_mode="COMPREHENSIVE",
        )

        adapter = WanfangAdapter(mock.Mock())
        adapter._portal = portal
        with mock.patch.object(
            WanfangAdapter, "_advanced_search", return_value=[]
        ) as adv:
            adapter.search(_EXPANDED, 10, query_context=ctx)
        assert adv.call_args.kwargs["original_keyword"] == "太赫兹"

    @mock.patch("tju_info_retrieval.sources.wanfang.PortalNavigator")
    def test_e_missing_context_falls_back_to_keyword(self, mock_portal_cls):
        """E：query_context 缺失（旧调用）→ 安全回退到传入 keyword，不崩溃。"""
        portal = mock_portal_cls.return_value
        page = _search_page_mock()
        portal.open_resource.return_value = page

        adapter = WanfangAdapter(mock.Mock())
        adapter._portal = portal
        results = adapter.search("太赫兹", 10, query_context=None)

        assert len(results) == 1  # 旧调用链路不受影响
        assert _filled_keywords(page) == ["太赫兹"]

    @mock.patch("tju_info_retrieval.sources.wanfang.PortalNavigator")
    def test_error_message_reports_submitted_keyword(self, mock_portal_cls):
        """报错信息展示实际提交的原始词（而非上游扩展表达式）。"""
        portal = mock_portal_cls.return_value
        page = _search_page_mock()
        page.inner_text.return_value = "无可解析条目"
        portal.open_resource.return_value = page
        ctx = mock.Mock(
            research_direction="太赫兹", author_affiliation="", author_name=""
        )

        adapter = WanfangAdapter(mock.Mock())
        adapter._portal = portal
        with pytest.raises(SearchError, match="太赫兹"):
            adapter.search(_EXPANDED, 10, query_context=ctx)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
