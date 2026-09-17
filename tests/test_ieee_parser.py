#!/usr/bin/env python3
"""v0.8.2 正式测试：IeeeAdapter._parse_results 结构化 DOM 元数据解析。

覆盖三类场景（通过真实 headless Edge 加载 HTML fixture 驱动 JS 解析）：
1. IEEE 期刊项：year / venue / document_type 从结构化节点提取；
2. IEEE 会议项：同上；
3. 缺失结构化节点时安全回退（正则或 None）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest
from playwright.sync_api import sync_playwright

from tju_info_retrieval.sources.ieee import IeeeAdapter

# 期刊项 fixture：镜像真实 DOM 的 .description a（期刊名）与 .publisher-info-container
JOURNAL_FIXTURE = """<html><body>
<div class="List-results-items">
  <div class="result-item">
    <h3><a class="fw-bold" href="/document/8663550/">A survey on terahertz communications</a></h3>
    <div class="author"><a>Zhi Chen</a></div>
    <div class="description">
      <a href="/xpl/RecentIssue.jsp?punumber=6245522">China Communications</a>
      <div class="publisher-info-container">
        <span>Year: 2019</span>
        <span> | Magazine Article</span>
      </div>
    </div>
  </div>
</div>
</body></html>"""

# 会议项 fixture：.description a 为完整会议名（含缩写 IRMMW-THz）
CONFERENCE_FIXTURE = """<html><body>
<div class="List-results-items">
  <div class="result-item">
    <h3><a class="fw-bold" href="/document/10697698/">Single-input Single-output Terahertz Communication System</a></h3>
    <div class="author"><a>Xuan-Wei Miao</a></div>
    <div class="description">
      <a href="/xpl/conhome/10697483/proceeding">2024 49th International Conference on Infrared, Millimeter, and Terahertz Waves (IRMMW-THz)</a>
      <div class="publisher-info-container">
        <span>Year: 2024</span>
        <span> | Conference Paper</span>
      </div>
    </div>
 </div>
</div>
</body></html>"""

# 缺失结构化节点 fixture：无 .description a、无 .publisher-info-container
MISSING_FIXTURE = """<html><body>
<div class="List-results-items">
  <div class="result-item">
    <h3><a class="fw-bold" href="/document/3/">Bare result without metadata</a></h3>
    <div class="author"><a>No Meta</a></div>
 </div>
</div>
</body></html>"""


@pytest.fixture(scope="module")
def pw_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        yield page
        browser.close()


class TestIeeeParserJournal:
    def test_journal_structured_metadata(self, pw_page):
        pw_page.set_content(JOURNAL_FIXTURE)
        results = IeeeAdapter._parse_results(pw_page, 10)
        assert len(results) == 1
        r = results[0]
        assert r.rank == 1
        assert r.title == "A survey on terahertz communications"
        assert r.authors == ["Zhi Chen"]
        assert r.year == "2019"
        assert r.venue == "China Communications"
        assert r.document_type == "Magazine Article"
        assert r.detail_url == "/document/8663550/"
        assert r.database == "IEEE Xplore"


class TestIeeeParserConference:
    def test_conference_structured_metadata(self, pw_page):
        pw_page.set_content(CONFERENCE_FIXTURE)
        results = IeeeAdapter._parse_results(pw_page, 10)
        assert len(results) == 1
        r = results[0]
        assert r.title == "Single-input Single-output Terahertz Communication System"
        assert r.year == "2024"
        # .description a 为完整会议名（含缩写 IRMMW-THz）
        assert r.venue == "2024 49th International Conference on Infrared, Millimeter, and Terahertz Waves (IRMMW-THz)"
        assert "IRMMW-THz" in r.venue
        assert r.document_type == "Conference Paper"


class TestIeeeParserMissing:
    def test_missing_nodes_return_none(self, pw_page):
        pw_page.set_content(MISSING_FIXTURE)
        results = IeeeAdapter._parse_results(pw_page, 10)
        assert len(results) == 1
        r = results[0]
        assert r.title == "Bare result without metadata"
        assert r.authors == ["No Meta"]
        assert r.year is None
        assert r.venue is None
        assert r.document_type is None
        assert r.citation_count is None

    def test_empty_page_returns_empty_list(self, pw_page):
        pw_page.set_content("<html><body></body></html>")
        assert IeeeAdapter._parse_results(pw_page, 10) == []


# v0.16 Phase A4：citation 语义匹配 fixture（镜像真实 DOM，探针取证：
# 结果项内 <div>Cited by: <a ...>Papers (329)</a></div>）
CITATION_FIXTURE = """<html><body>
<div class="List-results-items">
  <div class="result-item">
    <h3><a class="fw-bold" href="/document/8663550/">A survey on terahertz communications</a></h3>
    <div class="author"><a>Zhi Chen</a></div>
    <div class="description">
      <a href="/xpl/RecentIssue.jsp?punumber=6245522">China Communications</a>
      <div class="publisher-info-container">
        <span>Year: 2019</span>
        <span> | Magazine Article</span>
      </div>
      <div>Cited by: <span><a href="/document/8663550/citations?tabFilter=papers#citations">Papers (329)</a></span></div>
    </div>
  </div>
  <div class="result-item">
    <h3><a class="fw-bold" href="/document/2/">Newly published paper</a></h3>
    <div class="author"><a>Some Author</a></div>
    <div class="description">
      <a href="/xpl/RecentIssue.jsp?punumber=1">Some Journal</a>
      <div class="publisher-info-container"><span>Year: 2026</span></div>
      <div>Cited by: <span><a href="/document/2/citations#citations">Papers (0)</a></span></div>
    </div>
  </div>
  <div class="result-item">
    <h3><a class="fw-bold" href="/document/3/">No citation block</a></h3>
    <div class="author"><a>Another Author</a></div>
    <div class="description">
      <a href="/xpl/RecentIssue.jsp?punumber=1">Some Journal</a>
      <div class="publisher-info-container"><span>Year: 2026</span></div>
      <div>Related conference papers (12) available</div>
    </div>
  </div>
</div>
</body></html>"""


class TestIeeeCitationParsing:
    """citation 提取：329 / 0 / 无区块，None != 0 语义（Phase A4/A5）。"""

    def _results(self, pw_page):
        pw_page.set_content(CITATION_FIXTURE)
        return IeeeAdapter._parse_results(pw_page, 10)

    def test_citation_normal_value(self, pw_page):
        results = self._results(pw_page)
        assert results[0].citation_count == 329

    def test_citation_zero_is_explicit_zero(self, pw_page):
        """"Papers (0)" = 明确零引用，不是 None。"""
        results = self._results(pw_page)
        assert results[1].citation_count == 0

    def test_citation_missing_block_is_none(self, pw_page):
        """无 Cited by 区块 → None；含其它括号数字不得误判为 citation。"""
        results = self._results(pw_page)
        assert results[2].citation_count is None

    def test_none_and_zero_distinct(self, pw_page):
        results = self._results(pw_page)
        assert results[1].citation_count == 0
        assert results[2].citation_count is None
        assert results[1].citation_count != results[2].citation_count


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
