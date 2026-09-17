#!/usr/bin/env python3
"""v0.13 Phase 2.4：IEEE affiliation 元数据增强服务测试（全部 mock）。

覆盖：
1. document_id 解析成功调用
2. 原始 IEEE URL 生成正确（导航到 ieeexplore.ieee.org/document/{id}/）
3. interstitial 等待逻辑（202 Loading → 轮询稳定 / 超时降级）
4. All Authors 点击（<a> 链接）
5. author-card 解析（剔除作者名）
6. 多作者多个单位
7. 无单位返回 []
8. 页面失败返回 []（静默降级）
9. 不影响旧 SearchResult / MetadataRecord 序列化
"""
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.services.ieee_metadata import IeeeMetadataFetcher

PROXY_URL = "https://bwihxdcuvvklgwkgplesvnxssjtshjk-s.p.lib.tju.edu.cn/document/11275411/"
ORIGINAL_URL = "https://ieeexplore.ieee.org/document/11275411/"


def _mock_page(stable=True, cards=None):
    page = mock.Mock()
    if stable:
        page.title.return_value = "Beam Misalignment Fading | IEEE Conference"
        page.inner_text.return_value = "x" * 300
    else:
        page.title.side_effect = Exception("page lost")
        page.inner_text.side_effect = Exception("page lost")
    page.locator.return_value.first.count.return_value = 0
    page.query_selector_all.return_value = cards if cards is not None else []
    return page


def _mock_session(page=None):
    session = mock.Mock()
    session.is_alive.return_value = True
    if page is None:
        page = _mock_page()
    ctx = mock.Mock()
    ctx.new_page.return_value = page
    session.context = ctx
    return session, page


def _card(name, affiliation):
    """构造 author-card mock：inner_text = 作者名+单位；含作者链接。"""
    card = mock.Mock()
    card.inner_text.return_value = (
        f"{name} {affiliation}" if affiliation else name
    )
    if name:
        link = mock.Mock()
        link.inner_text.return_value = name
        card.query_selector.return_value = link
    else:
        card.query_selector.return_value = None
    return card


# ============================================================
# 1/2. document_id 解析与原始 URL
# ============================================================

class TestDocumentIdAndUrl(unittest.TestCase):
    def test_parse_document_id_called(self):
        session, _page = _mock_session()
        fetcher = IeeeMetadataFetcher(session)
        with mock.patch(
            "tju_info_retrieval.services.ieee_metadata.parse_document_id",
            return_value="11275411",
        ) as m_pdi, mock.patch(
            "tju_info_retrieval.services.ieee_metadata.ieee_original_url",
            return_value=ORIGINAL_URL,
        ) as m_ourl:
            result = fetcher.fetch_affiliations(PROXY_URL)
        self.assertEqual(result, [])
        m_pdi.assert_called_once_with(PROXY_URL)
        m_ourl.assert_called_once_with(PROXY_URL)

    def test_invalid_document_id_returns_empty_without_browser(self):
        session = mock.Mock()
        fetcher = IeeeMetadataFetcher(session)
        result = fetcher.fetch_affiliations("https://example.com/other/abc")
        self.assertEqual(result, [])
        session.is_alive.assert_not_called()

    def test_original_url_navigated(self):
        """真实 parse_document_id/ieee_original_url：导航到原始站 URL。"""
        session, page = _mock_session()
        fetcher = IeeeMetadataFetcher(session)
        result = fetcher.fetch_affiliations(PROXY_URL)
        self.assertEqual(result, [])
        page.goto.assert_called_once_with(
            ORIGINAL_URL, wait_until="domcontentloaded", timeout=45000
        )


# ============================================================
# 3. interstitial 等待逻辑
# ============================================================

class TestSettleInterstitial(unittest.TestCase):
    def test_interstitial_polled_until_stable(self):
        page = mock.Mock()
        page.title.side_effect = [
            "Loading https://ieeexplore.ieee.org/document/11275411/",
            "Loading ...",
            "Beam Misalignment Fading | IEEE Conference",
        ]
        page.inner_text.return_value = "x" * 300
        page.locator.return_value.first.count.return_value = 0
        page.query_selector_all.return_value = []
        session, _ = _mock_session(page=page)
        fetcher = IeeeMetadataFetcher(session)
        fetcher.SETTLE_POLL_SECONDS = 0.01
        result = fetcher.fetch_affiliations(PROXY_URL)
        self.assertEqual(result, [])
        # 轮询了 3 次 title（前两次是 Loading，第三次稳定）才进入提取
        self.assertEqual(page.title.call_count, 3)
        self.assertGreaterEqual(page.query_selector_all.call_count, 1)

    def test_interstitial_timeout_returns_empty(self):
        """页面一直不可用（title 抛异常）→ 超时 → []，不进入提取。"""
        page = mock.Mock()
        page.title.side_effect = Exception("stuck")
        page.inner_text.side_effect = Exception("stuck")
        page.query_selector_all.return_value = []
        session, _ = _mock_session(page=page)
        fetcher = IeeeMetadataFetcher(session)
        fetcher.SETTLE_TIMEOUT_SECONDS = 0.3
        fetcher.SETTLE_POLL_SECONDS = 0.05
        result = fetcher.fetch_affiliations(PROXY_URL)
        self.assertEqual(result, [])
        page.query_selector_all.assert_not_called()


# ============================================================
# 4. All Authors 点击
# ============================================================

class TestAllAuthorsExpand(unittest.TestCase):
    def test_all_authors_clicked_when_present(self):
        page = mock.Mock()
        page.title.return_value = "Beam | IEEE"
        page.inner_text.return_value = "x" * 300
        locator = mock.Mock()
        locator.first.count.return_value = 1
        page.locator.return_value = locator
        page.query_selector_all.return_value = []
        session, _ = _mock_session(page=page)
        fetcher = IeeeMetadataFetcher(session)
        result = fetcher.fetch_affiliations(PROXY_URL)
        self.assertEqual(result, [])
        page.locator.assert_called_once_with("a:has-text('All Authors')")
        locator.first.click.assert_called_once()

    def test_all_authors_skipped_when_absent(self):
        page = mock.Mock()
        page.title.return_value = "Beam | IEEE"
        page.inner_text.return_value = "x" * 300
        locator = mock.Mock()
        locator.first.count.return_value = 0
        page.locator.return_value = locator
        page.query_selector_all.return_value = []
        session, _ = _mock_session(page=page)
        fetcher = IeeeMetadataFetcher(session)
        result = fetcher.fetch_affiliations(PROXY_URL)
        self.assertEqual(result, [])
        locator.first.click.assert_not_called()


# ============================================================
# 5/6/7. author-card 解析
# ============================================================

class TestParseAffiliation(unittest.TestCase):
    def test_single_author_affiliation(self):
        card = _card("Samuel Sani", "Sabanci University, Istanbul, Turkey")
        self.assertEqual(
            IeeeMetadataFetcher._parse_affiliation(card),
            "Sabanci University, Istanbul, Turkey",
        )

    def test_multiple_authors_multiple_affiliations(self):
        cards = [
            _card("Samuel Sani", "Sabanci University, Istanbul, Turkey"),
            _card("Wolfgang Gerstacker", "Friedrich-Alexander-Universität, Erlangen"),
        ]
        page = _mock_page(stable=True, cards=cards)
        session, _ = _mock_session(page=page)
        fetcher = IeeeMetadataFetcher(session)
        result = fetcher.fetch_affiliations(PROXY_URL)
        self.assertEqual(result, [
            "Sabanci University, Istanbul, Turkey",
            "Friedrich-Alexander-Universität, Erlangen",
        ])

    def test_parse_author_link_with_semicolon(self):
        """banner 样式文本（作者名以 ; 结尾）也能正确剔除。"""
        card = mock.Mock()
        card.inner_text.return_value = "Samuel Sani; Sabanci University, Istanbul, Turkey"
        link = mock.Mock()
        link.inner_text.return_value = "Samuel Sani"
        card.query_selector.return_value = link
        self.assertEqual(
            IeeeMetadataFetcher._parse_affiliation(card),
            "Sabanci University, Istanbul, Turkey",
        )

    def test_no_affiliation_returns_empty(self):
        cards = [_card("Samuel Sani", None)]
        page = _mock_page(stable=True, cards=cards)
        session, _ = _mock_session(page=page)
        fetcher = IeeeMetadataFetcher(session)
        self.assertEqual(fetcher.fetch_affiliations(PROXY_URL), [])

    def test_no_cards_returns_empty(self):
        page = _mock_page(stable=True, cards=[])
        session, _ = _mock_session(page=page)
        fetcher = IeeeMetadataFetcher(session)
        self.assertEqual(fetcher.fetch_affiliations(PROXY_URL), [])

    def test_card_without_author_link_keyword_guard(self):
        """无作者链接且文本不含机构关键词 → None（避免把作者名当单位）。"""
        card = mock.Mock()
        card.inner_text.return_value = "Samuel Sani"
        card.query_selector.return_value = None
        self.assertIsNone(IeeeMetadataFetcher._parse_affiliation(card))

    def test_card_without_author_link_but_institution(self):
        """无作者链接但文本含机构关键词 → 视为单位。"""
        card = mock.Mock()
        card.inner_text.return_value = "Sabanci University, Istanbul, Turkey"
        card.query_selector.return_value = None
        self.assertEqual(
            IeeeMetadataFetcher._parse_affiliation(card),
            "Sabanci University, Istanbul, Turkey",
        )

    def test_empty_card_returns_none(self):
        card = mock.Mock()
        card.inner_text.return_value = ""
        self.assertIsNone(IeeeMetadataFetcher._parse_affiliation(card))


# ============================================================
# 8. 页面失败静默降级
# ============================================================

class TestFailureDegradation(unittest.TestCase):
    def test_page_failure_returns_empty(self):
        """goto 抛异常 + interstitial 超时 → []，不抛异常。"""
        page = mock.Mock()
        page.goto.side_effect = Exception("network error")
        page.title.side_effect = Exception("stuck")
        page.inner_text.side_effect = Exception("stuck")
        session, _ = _mock_session(page=page)
        fetcher = IeeeMetadataFetcher(session)
        fetcher.SETTLE_TIMEOUT_SECONDS = 0.3
        fetcher.SETTLE_POLL_SECONDS = 0.05
        self.assertEqual(fetcher.fetch_affiliations(PROXY_URL), [])

    def test_goto_error_still_polls(self):
        """goto 报错但页面随后稳定 → 继续流程并返回 []。"""
        page = mock.Mock()
        page.goto.side_effect = Exception("goto failed")
        page.title.return_value = "Beam | IEEE"
        page.inner_text.return_value = "x" * 300
        page.locator.return_value.first.count.return_value = 0
        page.query_selector_all.return_value = []
        session, _ = _mock_session(page=page)
        fetcher = IeeeMetadataFetcher(session)
        result = fetcher.fetch_affiliations(PROXY_URL)
        self.assertEqual(result, [])
        self.assertGreaterEqual(page.query_selector_all.call_count, 1)


# ============================================================
# 9. 不影响旧 SearchResult / MetadataRecord
# ============================================================

class TestSearchResultAndRecord(unittest.TestCase):
    def test_search_result_not_modified(self):
        from tju_info_retrieval.models.result import SearchResult

        r = SearchResult(
            rank=1, title="Beam Misalignment Fading",
            authors=["Samuel Sani"], year="2025", database="IEEE Xplore",
        )
        before = r.to_dict()
        session, _page = _mock_session()
        fetcher = IeeeMetadataFetcher(session)
        result = fetcher.fetch_affiliations(PROXY_URL)
        self.assertEqual(result, [])
        self.assertEqual(r.to_dict(), before)
        self.assertFalse(hasattr(r, "affiliations"))

    def test_metadata_record_round_trip(self):
        from tju_info_retrieval.models.metadata import MetadataRecord

        rec = MetadataRecord(
            result_id=PROXY_URL,
            affiliations=["Sabanci University, Istanbul, Turkey"],
            fetched_at="2026-09-03T12:00:00",
        )
        restored = MetadataRecord.from_dict(rec.to_dict())
        self.assertEqual(restored, rec)

    def test_metadata_record_missing_keys(self):
        from tju_info_retrieval.models.metadata import MetadataRecord

        rec = MetadataRecord.from_dict({})
        self.assertEqual(rec.result_id, "")
        self.assertEqual(rec.affiliations, [])
        self.assertIsNone(rec.fetched_at)


if __name__ == "__main__":
    unittest.main(verbosity=2)
