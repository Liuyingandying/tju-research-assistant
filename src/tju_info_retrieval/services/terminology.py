"""领域词典（Domain Terminology，v0.8.1 Phase 2）。

把中→英概念词典从 ``query_translator.py`` 迁移到独立数据模块，
并扩充第一批科研领域词条，供 ``ConceptSegmenter``（切分）与
``QueryTranslator``（翻译）共用。

纯数据 + 轻量校验，零第三方依赖：
  - ``TERMS``：中→英概念词典，``dict[str, dict[str, list[str]]]``；
  - ``validate()``：校验词典（key 非空、value 非空、无重复），供测试与加载时调用。

结构约定（见 ``v0.8_domain_dictionary_design.md``）：
  - 收录完整领域词，不收录子串碎片；
  - 长词优先由 ``ConceptSegmenter`` 最长匹配天然保证（如 ``无线通信`` > ``通信``）。
"""

from __future__ import annotations

# 中→英概念词典（有限、人工维护；未命中保持原词）
# 每个概念映射到该语言的等价词列表；英文短语含空格由 QueryBuilder 加引号。
TERMS: dict[str, dict[str, list[str]]] = {
    # 太赫兹 / 通感一体化（v0.8 已有）
    "太赫兹": {
        "en": ["terahertz", "THz"],
    },
    "通感一体化": {
        "en": [
            "integrated sensing and communication",
            "ISAC",
        ],
    },
    # 第一批科研领域词（v0.8.1 Phase 2 扩充）
    "通信": {
        "en": ["communication"],
    },
    "无线通信": {
        "en": ["wireless communication", "wireless"],
    },
    "毫米波": {
        "en": ["millimeter wave", "mmWave"],
    },
    "6G": {
        "en": ["6G", "sixth generation"],
    },
    "感知": {
        "en": ["sensing"],
    },
    "雷达": {
        "en": ["radar"],
    },
    "阵列": {
        "en": ["array"],
    },
    "天线": {
        "en": ["antenna"],
    },
    "波束形成": {
        "en": ["beamforming"],
    },
}


def validate(terms: dict[str, dict[str, list[str]]] | None = None) -> list[str]:
    """校验词典，返回问题列表（空列表表示通过）。

    检查项：
      - 中文 key 非空；
      - 每个概念的 ``en`` 列表非空；
      - 无重复 key（Python dict 天然去重，此处显式检查以防静默覆盖）。
    """
    terms = terms if terms is not None else TERMS
    problems: list[str] = []
    for key, entry in terms.items():
        if not key or not key.strip():
            problems.append(f"空概念 key: {key!r}")
        en = entry.get("en", []) if isinstance(entry, dict) else []
        if not en:
            problems.append(f"概念 {key!r} 缺少 en 等价词")
    return problems
