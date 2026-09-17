#!/usr/bin/env python3
"""正式测试：CNKI DOM 解析（极小 synthetic HTML fixture，仅模拟已观察到的
table.result-table-list 结构；不保存真实授权页面 HTML）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.sources.cnki import CNKIAdapter

FIXTURE = """<html><body>
<table class="result-table-list">
  <tr><th>题名</th><th>作者</th><th>来源</th><th>发表时间</th><th>数据库</th></tr>
  <tr>
    <td class="seq">1</td>
    <td class="name"><a class="fz14 inline" href="https://kns.cnki.net/a1">标题A</a></td>
    <td class="author"><a>张三</a>;<a>李四</a></td>
    <td class="source"><p><a>期刊A</a></p></td>
    <td class="date">2026-01-15 10:00</td>
    <td class="data"><span>期刊</span></td>
  </tr>
  <tr>
    <td class="seq">2</td>
    <td class="name"><a class="fz14 inline" href="https://kns.cnki.net/a2">标题B</a></td>
    <td class="author"><a>王五</a></td>
    <td class="source"><p><a>会议B</a></p></td>
    <td class="date">2025-06-01 00:00</td>
    <td class="data"><span>会议</span></td>
  </tr>
  <tr>
    <td class="seq">3</td>
    <td class="name"><a class="fz14 inline" href="https://kns.cnki.net/a3">标题C</a></td>
    <td class="author"><a>赵六</a></td>
    <td class="source"><p><a>学位C</a></p></td>
    <td class="date"></td>
    <td class="data"><span>学位论文</span></td>
  </tr>
</table>
</body></html>"""


class TestCnkiParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(channel="msedge", headless=True)
        cls._page = cls._browser.new_page()
        cls._page.set_content(FIXTURE)
        # 仅测试解析，不需要真实 session
        cls._adapter = CNKIAdapter(None)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def test_parse_full_fields(self):
        results = self._adapter.parse_results(self._page, 10)
        self.assertEqual(len(results), 3)
        r = results[0]
        self.assertEqual(r.rank, 1)
        self.assertEqual(r.title, "标题A")
        self.assertEqual(r.authors, ["张三", "李四"])
        self.assertEqual(r.source, "期刊A")
        self.assertEqual(r.year, "2026")
        self.assertEqual(r.detail_url, "https://kns.cnki.net/a1")
        self.assertEqual(r.document_type, "期刊")
        self.assertEqual(r.database, "CNKI")

    def test_document_type(self):
        results = self._adapter.parse_results(self._page, 10)
        self.assertEqual(results[1].document_type, "会议")
        self.assertEqual(results[2].document_type, "学位论文")

    def test_missing_year_is_none(self):
        results = self._adapter.parse_results(self._page, 10)
        self.assertIsNone(results[2].year)

    def test_max_count(self):
        results = self._adapter.parse_results(self._page, 2)
        self.assertEqual(len(results), 2)

    def test_citation_missing_is_none(self):
        """基础 fixture 无 td.quote（v0.16 前旧结构/匿名态空列）→ citation None。"""
        results = self._adapter.parse_results(self._page, 10)
        for r in results:
            self.assertIsNone(r.citation_count)


# v0.16 Phase A2：被引列按真机校准结构（docs/v0.16_cnki_citation_probe.md）
# —— table.result-table-list 内 th「被引」↔ td.quote，纯文本数字、无链接。
CITATION_FIXTURE = """<html><body>
<table class="result-table-list">
  <tr><th>题名</th><th>作者</th><th>来源</th><th>发表时间</th><th>数据库</th><th>被引</th><th>下载</th></tr>
  <tr>
    <td class="seq">1</td>
    <td class="name"><a class="fz14 inline" href="https://kns.cnki.net/c1">皮肤屏障损伤分析</a></td>
    <td class="author"><a>齐济</a></td>
    <td class="source"><p><a>光谱学与光谱分析</a></p></td>
    <td class="date">2025-12-04</td>
    <td class="data"><span>期刊</span></td>
    <td class="quote">237</td>
    <td class="download"></td>
  </tr>
  <tr>
    <td class="seq">2</td>
    <td class="name"><a class="fz14 inline" href="https://kns.cnki.net/c2">磁光材料进展</a></td>
    <td class="author"><a>王圣麟</a></td>
    <td class="source"><p><a>人工晶体学报</a></p></td>
    <td class="date">2026-09-05</td>
    <td class="data"><span>期刊</span></td>
    <td class="quote">0</td>
    <td class="download"></td>
  </tr>
  <tr>
    <td class="seq">3</td>
    <td class="name"><a class="fz14 inline" href="https://kns.cnki.net/c3">无被引数据论文</a></td>
    <td class="author"><a>某某</a></td>
    <td class="source"><p><a>某期刊</a></p></td>
    <td class="date">2026-01-01</td>
    <td class="data"><span>期刊</span></td>
    <td class="quote"></td>
    <td class="download"></td>
  </tr>
  <tr>
    <td class="seq">4</td>
    <td class="name"><a class="fz14 inline" href="https://kns.cnki.net/c4">占位符论文</a></td>
    <td class="author"><a>某某</a></td>
    <td class="source"><p><a>某期刊</a></p></td>
    <td class="date">2026-01-02</td>
    <td class="data"><span>期刊</span></td>
    <td class="quote">--</td>
    <td class="download"></td>
  </tr>
  <tr>
    <td class="seq">5</td>
    <td class="name"><a class="fz14 inline" href="https://kns.cnki.net/c5">链接包裹论文</a></td>
    <td class="author"><a>某某</a></td>
    <td class="source"><p><a>某期刊</a></p></td>
    <td class="date">2026-01-03</td>
    <td class="data"><span>期刊</span></td>
    <td class="quote"><a>55</a></td>
    <td class="download"></td>
  </tr>
</table>
</body></html>"""


class TestCnkiCitationParsing(unittest.TestCase):
    """被引列解析：237 / 0 / 空文本 / 占位符 / 链接包裹（None != 0 语义）。"""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(channel="msedge", headless=True)
        cls._page = cls._browser.new_page()
        cls._page.set_content(CITATION_FIXTURE)
        cls._adapter = CNKIAdapter(None)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def _results(self):
        return self._adapter.parse_results(self._page, 10)

    def test_citation_normal_value(self):
        self.assertEqual(self._results()[0].citation_count, 237)

    def test_citation_zero_is_explicit_zero(self):
        """「0」= 明确零引用，不是 None。"""
        self.assertEqual(self._results()[1].citation_count, 0)

    def test_citation_empty_cell_is_none(self):
        self.assertIsNone(self._results()[2].citation_count)

    def test_citation_placeholder_text_is_none(self):
        """"--" 等无数字占位文本 → None。"""
        self.assertIsNone(self._results()[3].citation_count)

    def test_citation_link_wrapped_still_parsed(self):
        """容错：数字被链接包裹时仍解析成功。"""
        self.assertEqual(self._results()[4].citation_count, 55)

    def test_none_and_zero_distinct(self):
        """语义保持：None（未知）与 0（明确零引用）不相等。"""
        results = self._results()
        self.assertIsNotNone(results[1].citation_count)
        self.assertIsNone(results[2].citation_count)
        self.assertNotEqual(results[1].citation_count, results[2].citation_count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
