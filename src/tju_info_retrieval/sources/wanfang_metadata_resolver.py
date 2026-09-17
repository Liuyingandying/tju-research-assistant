"""万方详情页 metadata 解析器与补全服务（v0.14）。

目标：让 WanfangAdapter 返回的 Result 成为完整论文对象——
补齐 year / authors / doi / citation_count / detail_url。

组成：
- `parse_detail_page(page)`：从万方详情页 DOM 解析元数据
  （selector 均经真实页面探针实证，见
  docs/v0.14_wanfang_metadata_audit.md 与 runtime/wanfang_metadata_probe/）。
- `WanfangMetadataResolver`：在结果页逐条点击标题 → 捕获详情新标签 →
  解析并原位补齐 Result 字段；title/url → metadata 缓存避免重复访问。

约束与语义：
- 单条 ≤5s、整体预算 ≤30s（超时跳过，不阻塞搜索）；
- 任何失败降级：字段保持 None，不抛出、不影响搜索结果；
- 缓存按归一化标题，进程内全局共享（跨检索复用）。
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 归一化标题 → 解析结果缓存（进程级，跨检索复用；上限防膨胀）
_CACHE_MAX = 500
_GLOBAL_CACHE: dict[str, dict] = {}

_PER_ITEM_TIMEOUT_S = 5.0
_TOTAL_BUDGET_S = 30.0


def _norm(text: str | None) -> str:
    """标题归一（缓存键）：去空白，转小写。"""
    return re.sub(r"[\s\u3000]+", "", (text or "")).lower()


def _apply(result: Any, meta: dict) -> None:
    """将解析结果原位补齐到 Result（只填空字段，不覆盖已有值）。"""
    if not getattr(result, "year", None) and meta.get("year"):
        result.year = meta["year"]
    if not getattr(result, "authors", None) and meta.get("authors"):
        result.authors = meta["authors"]
    if not getattr(result, "doi", None) and meta.get("doi"):
        result.doi = meta["doi"]
    cc = meta.get("citation_count")
    if getattr(result, "citation_count", None) is None and cc is not None:
        result.citation_count = cc
    if not getattr(result, "detail_url", None) and meta.get("detail_url"):
        result.detail_url = meta["detail_url"]


def parse_detail_page(page) -> dict:
    """从万方详情页解析元数据；任何字段失败均为 None/空，不抛出。

    实测 selector（runtime/wanfang_metadata_probe/detail_fields.json）：
    - 作者：a.test-detail-author（文本尾部数字为单位角标，剔除）
    - 出版/发表日期：div.itemUrl（YYYY-MM-DD）
    - 被引：div.minerLine（“被引 N”）
    - DOI：div.doiStyle（“DOI:”标签）所在容器的 10.xxxx/... 值
    """
    out: dict = {"title": None, "year": None, "authors": [],
                 "doi": None, "citation_count": None, "detail_url": None}
    try:
        raw_title = page.title() or ""
        out["title"] = raw_title.split("-")[0].strip() or None
    except Exception:  # noqa: BLE001
        pass
    # 作者（剔除尾部单位角标数字）
    try:
        names = page.locator("a.test-detail-author").all_inner_texts()
        out["authors"] = [
            re.sub(r"\s*\d+\s*$", "", (a or "").strip()) for a in names if (a or "").strip()
        ]
    except Exception:  # noqa: BLE001
        out["authors"] = []
    # 年份（出版/发表日期 itemUrl，取首个含 4 位年者）
    try:
        for d in page.locator("div.itemUrl").all_inner_texts():
            m = re.search(r"(?:19|20)\d{2}", d or "")
            if m:
                out["year"] = m.group(0)
                break
    except Exception:  # noqa: BLE001
        pass
    # 被引（div.minerLine “被引 N”）
    try:
        for ln in page.locator("div.minerLine").all_inner_texts():
            m = re.search(r"被引\s*(\d+)", ln or "")
            if m:
                out["citation_count"] = int(m.group(1))
                break
    except Exception:  # noqa: BLE001
        pass
    # DOI（doiStyle 标签容器内的 10.xxxx 值）
    try:
        box = page.locator("div.doiStyle").first
        if box.count() > 0:
            parent_text = box.evaluate("el => (el.parentElement.innerText || '')")
            m = re.search(r"10\.\d{4,5}/[^\s，。]+", parent_text or "")
            if m:
                out["doi"] = m.group(0)
    except Exception:  # noqa: BLE001
        pass
    return out


class WanfangMetadataResolver:
    """在结果页逐条点击标题，捕获详情新标签并补齐 Result 元数据。

    - page：万方结果页（已渲染 .title-area 行）；
    - cache：归一化标题 → 元数据（缺省用进程级全局缓存，跨检索复用）；
    - 预算：单条 ≤per_item_timeout_s，整体 ≤total_budget_s，超时跳过剩余；
    - 所有异常内部消化：metadata 补全失败不影响搜索结果返回。
    """

    def __init__(
        self,
        page,
        cache: dict | None = None,
        per_item_timeout_s: float = _PER_ITEM_TIMEOUT_S,
        total_budget_s: float = _TOTAL_BUDGET_S,
    ) -> None:
        self._page = page
        self._cache = cache if cache is not None else _GLOBAL_CACHE
        self._per_item_timeout_s = per_item_timeout_s
        self._total_budget_s = total_budget_s

    def enrich(self, results, count: int | None = None) -> int:
        """补齐 results 元数据；返回实际解析（含缓存命中）的条数。

        流程：先应用缓存命中，再对未命中条目逐条点击标题 →
        expect_page 捕获详情新标签 → 解析 → 应用 → 关闭标签。
        任一条失败跳过（该条字段保持 None），不中断后续条目；
        任何异常降级返回（不向调用方抛出，不影响搜索）。
        """
        targets = list(results[:count]) if count else list(results)
        if not targets:
            return 0
        resolved = 0
        pending: list[tuple[int, Any]] = []
        for i, r in enumerate(targets):
            meta = self._cache.get(_norm(r.title))
            if meta:
                _apply(r, meta)
                resolved += 1
            else:
                pending.append((i, r))
        if not pending:
            return resolved
        deadline = time.monotonic() + self._total_budget_s
        try:
            rows = self._page.locator(".title-area")
            for i, r in pending:
                if time.monotonic() >= deadline:
                    break
                try:
                    with self._page.context.expect_page(
                        timeout=int(self._per_item_timeout_s * 1000)
                    ) as page_info:
                        rows.nth(i).click(
                            position={"x": 160, "y": 12}, timeout=4000
                        )
                    detail = page_info.value
                except Exception:  # noqa: BLE001 - 单条失败跳过
                    continue
                try:
                    detail.wait_for_load_state("domcontentloaded", timeout=3000)
                    detail.wait_for_timeout(1000)
                    meta = parse_detail_page(detail)
                    meta["detail_url"] = detail.url
                    self._cache[_norm(r.title)] = meta
                    _apply(r, meta)
                    resolved += 1
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    try:
                        detail.close()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as exc:  # noqa: BLE001 - 整体失败降级
            logger.warning("万方 metadata resolver 整体降级: %s", exc)
        return resolved
