"""CNKI 论文 on-demand 摘要获取（v0.17 Phase 2.4-C-B）。

仅当用户主动选中单条 CNKI paper 并触发「关键信息整理」时调用——
不参与 SEARCH 阶段、不做批量详情抓取。

职责：按 detail_url 打开论文详情页，读取真实摘要（只取摘要，不取 PDF/全文/
引用/附件），normalize 后返回字符串；失败按分类抛 EvidenceError。

休眠安全验证语义（探针 runtime/v0.17_paper_evidence_probe/05-06 json）：
DOM 含 captcha 元素不表示被阻断——仅当摘要不可得 且 标记存在才判 BLOCKED。
禁止拖滑块/绕验证码/换 UA/无限刷新。
"""
from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger(__name__)

# 摘要提取：详情页文本“摘要：\t<首行长文本>”（探针 06 json 取证）
_ABSTRACT_RE = re.compile(r"摘要[:：]\s*([^\n]{10,})")
# 休眠/激活验证码标记（与 CNKI Patent 同语义）
_CAPTCHA_MARKERS = ("安全验证", "拖动", "验证码")

_DEFAULT_TIMEOUT_S = 10.0
_CACHE_MAX = 300
_GLOBAL_CACHE: dict[str, str] = {}

# 失败分类 → UI 文案（worker 层映射）
NO_URL = "no_url"
TIMEOUT = "timeout"
AUTH = "auth"
CAPTCHA = "captcha"
NO_ABSTRACT = "no_abstract"
NETWORK = "network"


class EvidenceError(Exception):
    """摘要获取失败（code ∈ {no_url, timeout, auth, captcha, no_abstract, network}）。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def parse_abstract(page_text: str) -> str:
    """从详情页文本提取摘要（首行，normalize 空白）。无摘要返回空串。"""
    m = _ABSTRACT_RE.search(page_text or "")
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()


class CnkiPaperEvidenceFetcher:
    """CNKI 论文详情摘要获取（进程级 cache；单条 ≤10s）。"""

    def __init__(self, session, cache: dict | None = None,
                 timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._session = session
        self._cache = cache if cache is not None else _GLOBAL_CACHE
        self._timeout = timeout_s

    def fetch(self, detail_url: str) -> str:
        """返回摘要；失败抛 EvidenceError（不返回伪造/空值）。"""
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
                      timeout=max(self._timeout, 5.0) * 1000)
            # 总预算内轮询摘要节点就绪
            while time.monotonic() < deadline:
                page.wait_for_timeout(500)
                text = page.inner_text("body") or ""
                if text.strip():
                    break
            text = page.inner_text("body") or ""
            # 授权失效判定：CNKI 跳转登录页（URL 含 /login）；页面正文的
            # “登录/注册”字样出现在详情页顶部导航，属正常 chrome，不误判。
            if re.search(r"/login", (page.url or "").lower()):
                raise EvidenceError(AUTH)
            abstract = parse_abstract(text)
            if abstract:
                return abstract
            # 摘要不可得 + 验证码标记 → 真阻断；否则 → 页面无可读摘要
            if any(m in text for m in _CAPTCHA_MARKERS):
                raise EvidenceError(CAPTCHA)
            raise EvidenceError(NO_ABSTRACT)
        except EvidenceError:
            raise
        except Exception as exc:  # noqa: BLE001 - goto 超时/网络错误分类
            if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
                raise EvidenceError(TIMEOUT) from exc
            raise EvidenceError(NETWORK) from exc
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass