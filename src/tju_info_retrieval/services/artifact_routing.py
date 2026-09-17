"""成果类型 × 数据库 → 内部 adapter source 名解析（v0.17 Phase 2.2-D）。

纯函数、确定性、无 GUI/网络依赖。GUI 在提交前把用户选择的
「成果类型 × 数据库」解析成最终内部 source list，SearchService 只接内部名，
不重构 Request / SearchService。

语义（source ≠ artifact_type，任务 §二）：
- paper × CNKI/万方/IEEE Xplore → 同名论文 source；
- patent × 万方 → "万方专利"（独立 source，独立 quota，独立 adapter）；
- patent × CNKI/IEEE Xplore → 当前无 adapter，不产出。

默认兼容：成果类型选择为空时按 paper 处理（旧用户行为完全一致）。
"""
from __future__ import annotations

from tju_info_retrieval.models.artifact import (
    ARTIFACT_NEWS,
    ARTIFACT_PAPER,
    ARTIFACT_PATENT,
)

# 论文成果类型可映射的数据库（key 为既有内部 source 名，原样透传）
PAPER_SOURCE_MAP: dict[str, str] = {
    "CNKI": "CNKI",
    "万方": "万方",
    "IEEE Xplore": "IEEE Xplore",
}
# 专利成果类型可映射的数据库（key 为数据库名 → 专利内部 source 名）
PATENT_SOURCE_MAP: dict[str, str] = {
    "CNKI": "CNKI专利",
    "万方": "万方专利",
}
# 专利可用的数据库名（用于仅专利组合的提示判定）
SUPPORTED_PATENT_DATABASES: tuple[str, ...] = ("CNKI", "万方")
# 新闻内部 source（v0.17 Phase 2.3-C）：不依赖任何数据库复选框——
# 新闻检索入口为天津大学新闻网（中文站点，覆盖天大科研新闻及站内转载的
# 部分中央媒体报道），语言适用性由 GUI 层按语言模式过滤。
NEWS_INTERNAL_SOURCE = "天津大学新闻网"


def resolve_effective_sources(
    selected_databases: list[str],
    selected_artifact_types: list[str] | None,
) -> list[str]:
    """解析最终可执行内部 source 名（保序、去重）。

    selected_databases：用户勾选且语言模式允许的数据库内部名
      （CNKI / 万方 / IEEE Xplore）；
    selected_artifact_types：成果类型（paper / patent 子集）。
      **None 与 [] 语义不同**（Phase 2.2-D hotfix，人工 GUI 验收发现）：
      - None：legacy 调用方未提供成果类型 → 默认 ["paper"]（旧调用兼容）；
      - []：GUI / 新调用方明确选择零种成果类型 → 返回 []，不得回退 paper。
        （GUI 在提交前已单独阻止"全未勾选"，此处 [] 是防御语义。）

    示例：
      paper + [CNKI, 万方]           → [CNKI, 万方]
      patent + [万方]                 → [万方专利]
      paper+patent + [万方]           → [万方, 万方专利]
      paper+patent + [CNKI,万方,IEEE] → [CNKI, 万方, IEEE Xplore, 万方专利]
      patent + [CNKI, IEEE Xplore]    → []
      None  + [万方]                  → [万方]（legacy 默认论文）
      []    + [万方]                  → []（显式空选择不回退）
      news + []                       → [天津大学新闻网]（news 不依赖数据库选择）
      paper+news + [CNKI]             → [CNKI, 天津大学新闻网]

    注意：news 的语言适用性（英文模式不可用）由 GUI 层在调用前过滤
    selected_artifact_types 实现，本函数保持纯映射。
    """
    if selected_artifact_types is None:
        artifacts = [ARTIFACT_PAPER]  # legacy：未提供 → 默认论文
    else:
        artifacts = list(selected_artifact_types)  # 显式 [] → 零成果类型
    dbs = list(selected_databases)
    effective: list[str] = []
    if ARTIFACT_PAPER in artifacts:
        for db in dbs:
            mapped = PAPER_SOURCE_MAP.get(db)
            if mapped and mapped not in effective:
                effective.append(mapped)
    if ARTIFACT_PATENT in artifacts:
        for db in dbs:
            mapped = PATENT_SOURCE_MAP.get(db)
            if mapped and mapped not in effective:
                effective.append(mapped)
    if ARTIFACT_NEWS in artifacts:
        # news 不依赖数据库选择：勾选即可执行（语言过滤在 GUI 层）
        if NEWS_INTERNAL_SOURCE not in effective:
            effective.append(NEWS_INTERNAL_SOURCE)
    return effective


def patent_has_no_supported_database(selected_databases: list[str]) -> bool:
    """仅专利提示判定：所选数据库中是否存在专利可映射的数据库。"""
    return not any(db in SUPPORTED_PATENT_DATABASES for db in selected_databases)