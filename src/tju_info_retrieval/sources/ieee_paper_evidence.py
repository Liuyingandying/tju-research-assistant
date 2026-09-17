"""IEEE 论文 on-demand 摘要获取（v0.17 Phase 2.4-C-C）。

仅当用户主动选中单条 IEEE paper 并触发「关键信息整理」时调用（与 CNKI
fetcher 同架构；SEARCH 阶段零详情访问）。

职责：按 detail_url 打开 IEEE 文档页，读取 Abstract 文本（只取摘要，
不取 PDF/全文/References/Cited By/Metrics），normalize 后返回字符串；
失败按分类抛 EvidenceError（复用 cnki_paper_evidence 的错误体系）。

超时：IEEE 经校园代理真实 latency ~15-25s（PoC），默认 30s（bounded，
不无限等待）；CNKI fetcher 的 10s 默认不变（各 fetcher 自带默认值）。
"""
from __future__ import annotations

import logging
import re
import time

from tju_info_retrieval.sources.cnki_paper_evidence import (
    AUTH,
    CAPTCHA,
    NETWORK,
    NO_ABSTRACT,
    NO_URL,
    TIMEOUT,
    EvidenceError,
)

logger = logging.getLogger(__name__)

# Abstract 正文段起点；终点取其后首个常见章节头（含 IEEE 版权行/Index Terms/
# Keywords/Published in/Date of Publication/Manuscript 等）。
_ABSTRACT_BODY_RE = re.compile(
    r"Abstract\s*:\s*(.{15,}?)(?=\n\s*(?:Index Terms|Keywords|"
    r"Published in|Date of Publication|Manuscript received|"
    r"©|\d{4} IEEE|Access\s+Peer|View Document))",
    re.DOTALL,
)
_ABSTRACT_FALLBACK_RE = re.compile(r"Abstract\s*:\s*([^\n]{10,})")
_WHITESPACE_RE = re.compile(r"\s+")

_DEFAULT_TIMEOUT_S = 30.0
_CACHE_MAX = 300
_GLOBAL_CACHE: dict[str, str] = {}


def parse_ieee_abstract(page_text: str) -> str:
    """从 IEEE 文档页文本提取 Abstract 正文（normalize 空白）。

    优先以章节头截断；无标记时取 Abstract: 后首段前 1200 字符。
    """
    text = page_text or ""
    m = _ABSTRACT_BODY_RE.search(text)
    if not m:
        m = _ABSTRACT_FALLBACK_RE.search(text)
    if not m:
        return ""
    return _WHITESPACE_RE.sub(" ", m.group(1)).strip()


class IeeePaperEvidenceFetcher:
    """IEEE 论文详情摘要获取（进程级 cache；单条 ≤30s）。"""

    def __init__(self, session, cache: dict | None = None,
                 timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._session = session
        self._cache = cache if cache is not None else _GLOBAL_CACHE
        self._timeout = timeout_s

    def fetch(self, detail_url: str) -> str:
        url = (detail_url or "").strip()
        if not url.startswith("http"):
            raise EvidenceError(NO_URL)
        if url in self._cache:
            return self._cache[url]
        abstract = self._fetch_once(url)
        if len(self._cache) > _CACHE_MAX:
            self._cache.pop(next(iter(self._cache)))
        self._cache[url] = abstract
        return abstract

    def _fetch_once(self, url: str) -> str:
        page = None
        deadline = time.monotonic() + self._timeout
        try:
            page = self._session.context.new_page()
            page.goto(url, wait_until="domcontentloaded",
                      timeout=max(self._timeout, 10.0) * 1000)
            # 总预算内等正文就绪
            while time.monotonic() < deadline:
                page.wait_for_timeout(500)
                text = page.inner_text("body") or ""
                if text.strip():
                    break
            text = page.inner_text("body") or ""
            low = (text or "").lower()
            if "robot check" in low or "unusual traffic" in low \
                    or "captcha" in low:
                raise EvidenceError(CAPTCHA)
            if "/login" in (page.url or "").lower():
                raise EvidenceError(AUTH)
            abstract = parse_ieee_abstract(text)
            if abstract:
                return abstract
            raise EvidenceError(NO_ABSTRACT)
        except EvidenceError:
            raise
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
                raise EvidenceError(TIMEOUT) from exc
            raise EvidenceError(NETWORK) from exc
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass