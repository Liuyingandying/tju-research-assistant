"""中文科研关键词切分（v0.8.1 Phase 1）。

把无空格的中文连续科研词按专业词典（``TERMS``）最长匹配切分为独立概念词，
供 ``QueryTranslator`` 逐词元翻译为英文等价词。

纯逻辑、确定性、零第三方依赖：
  - 命中**最长**词典词 → 切为一个概念；
  - 未命中 → 累积连续未命中字符为一段（避免逐字碎片）；
  - 未知词 → 整段保留（安全回退，不抛异常）；
  - 空白视为概念边界（不构成概念，切分后被丢弃）。

典型效果：
    "太赫兹通感一体化" -> ["太赫兹", "通感一体化"]
    "太赫兹通信"       -> ["太赫兹", "通信"]
    "量子计算"         -> ["量子计算"]        # 未知词整段保留
"""

from __future__ import annotations


class ConceptSegmenter:
    """基于专业词典的最长匹配中文概念切分器（纯逻辑，可单测）。

    词典由调用方注入（默认空集），避免与 ``QueryTranslator`` 循环依赖；
    ``QueryTranslator`` 以 ``set(TERMS.keys())`` 作为词典。
    """

    def __init__(self, dictionary: set[str] | None = None) -> None:
        self._dict = set(dictionary) if dictionary else set()

    def segment(self, text: str) -> list[str]:
        """把中文科研关键词切分为概念词列表（最长匹配）。"""
        if not text:
            return []
        raw: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            matched = self._longest_match(text, i)
            if matched is not None:
                raw.append(matched)
                i += len(matched)
            else:
                start = i
                while i < n and self._longest_match(text, i) is None:
                    i += 1
                raw.append(text[start:i])
        # 去空白并丢弃空段：空白是概念边界，不构成概念
        concepts: list[str] = []
        for seg in raw:
            seg = seg.strip()
            if seg:
                concepts.append(seg)
        return concepts

    def _longest_match(self, text: str, start: int) -> str | None:
        """从 ``start`` 起匹配最长的词典词；未命中返回 ``None``。"""
        for end in range(len(text), start, -1):
            if text[start:end] in self._dict:
                return text[start:end]
        return None
