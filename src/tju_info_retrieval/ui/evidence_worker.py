"""后台摘要获取 Worker（v0.17 Phase 2.4-C-B）。

每次 fetch 创建独立 BrowserSession 并在同一 slot 内创建/使用/销毁
（Playwright sync 生命周期约束）；不触碰 SearchWorker 生命周期。

single-flight 由主窗口在提交侧保证（本 worker 一次只处理一个请求）。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal, Slot

from tju_info_retrieval.browser.session import BrowserSession
from tju_info_retrieval.sources.cnki_paper_evidence import (
    AUTH,
    CAPTCHA,
    EvidenceError,
    NETWORK,
    NO_ABSTRACT,
    NO_URL,
    TIMEOUT,
    CnkiPaperEvidenceFetcher,
)
from tju_info_retrieval.sources.ieee_paper_evidence import IeeePaperEvidenceFetcher

logger = logging.getLogger(__name__)

_ERROR_MESSAGES = {
    NO_URL: "当前论文缺少可用详情链接，无法获取摘要。",
    TIMEOUT: "论文摘要获取超时，请稍后重试。",
    AUTH: "数据库授权状态已失效，请重新登录后重试。",
    CAPTCHA: "当前页面需要人工验证，暂时无法获取摘要。",
    NO_ABSTRACT: "当前页面未能获取可靠摘要，无法生成关键信息整理。",
    NETWORK: "论文摘要获取失败（网络异常），请稍后重试。",
}

# source 内部名 → fetcher（CNKI 10s 默认；IEEE 30s 默认，均 fetcher 自带）
_FETCHERS = {
    "CNKI": CnkiPaperEvidenceFetcher,
    "IEEE Xplore": IeeePaperEvidenceFetcher,
}


class PaperEvidenceWorker(QObject):
    """后台论文摘要获取（CNKI/IEEE 共用；每次独立 BrowserSession）。"""

    abstract_ready = Signal(str)      # 摘要
    evidence_failed = Signal(str)     # 用户可读错误消息
    finished = Signal()

    @Slot(str, str)
    def fetch_abstract(self, source: str, detail_url: str) -> None:
        session: BrowserSession | None = None
        try:
            fetcher_cls = _FETCHERS.get(source)
            if fetcher_cls is None:
                self.evidence_failed.emit(
                    "当前来源暂不支持按需获取摘要，无法生成关键信息整理。")
                return
            session = BrowserSession()
            session.start()
            abstract = fetcher_cls(session).fetch(detail_url)
            self.abstract_ready.emit(abstract)
        except EvidenceError as exc:
            self.evidence_failed.emit(
                _ERROR_MESSAGES.get(exc.code, "论文摘要获取失败，请稍后重试。"))
        except Exception as exc:  # noqa: BLE001 - 兜底不崩溃
            logger.warning("摘要获取异常: %s", exc)
            self.evidence_failed.emit("论文摘要获取失败，请稍后重试。")
        finally:
            if session is not None:
                try:
                    session.shutdown()
                except Exception:  # noqa: BLE001
                    pass
            self.finished.emit()