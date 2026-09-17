"""检索请求模型 QueryRequest。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

MIN_RESULT_COUNT = 1
MAX_RESULT_COUNT = 200
EXPANSION_OPTIONS = ("综合", "理论", "应用", "综述")

ALLOWED_YEAR_RANGES = (0, 5, 10)  # 0=不限，5=近5年，10=近10年

# 单位筛选模式：soft=智能匹配（缺数据保留），strict=严格匹配（缺数据删除）
AFFILIATION_FILTER_MODES = ("soft", "strict")

# 高级检索日期格式：YYYY.MM（月份 01-12）
DATE_PATTERN = re.compile(r"^\d{4}\.(?:0[1-9]|1[0-2])$")


class RankingMode(str, Enum):
    """排序模式（v0.11 Phase 3）。

    COMPREHENSIVE: 综合推荐（标题匹配为主 + 年份 + 引用 + 数据库权重）
    RECENT:        最新发表（优先年份）
    CITATION:      高引用（优先引用影响力）
    """

    COMPREHENSIVE = "COMPREHENSIVE"
    RECENT = "RECENT"
    CITATION = "CITATION"


@dataclass
class QueryRequest:
    """一次检索请求。

    一期语义：
    - research_direction 作为实际检索词。
    - sources 指定数据源列表（默认 ["CNKI"]）。
    - person_name / person_org 为一期预留字段（v0.13 起作者条件由
      author_name / author_affiliation 承接，旧字段保留兼容）。
    - expansion_direction 进入模型但本轮不改变检索
      （query expansion 后续阶段开放）。

    v0.11 候选池解耦：
    - candidate_count 控制 Adapter 解析的候选行数（默认与 result_count 一致）。
    - result_count 控制最终展示/截断的行数。
    - 旧接口兼容：仅传 result_count 时，candidate_count 自动等于 result_count。

    v0.11 高级筛选：
    - ranking_mode 排序模式（默认 COMPREHENSIVE，旧调用兼容）。
    - year_range 年份筛选（0=不限 / 5=近5年 / 10=近10年，默认 0）。

    v0.13 高级检索条件：
    - author_name / author_affiliation：作者与作者单位
      （本阶段仅随请求传递，Adapter 查询语法暂不使用）。
    - start_date / end_date：起止年月，格式 YYYY.MM，空值表示不限。
    - affiliation_filter_mode：单位筛选模式（soft=智能匹配，strict=严格匹配）。

    v0.17 Phase 1（研究方向 OR 人员姓名）：
    - research_direction 与 author_name 至少一项非空；两者均空 → 校验失败。
    - author_affiliation 不能独立作为主检索条件（仅人员单位仍被阻止）。
    - author_name 非空而 research_direction 为空 → 作者检索（合法）。
    """

    research_direction: str = ""
    expansion_direction: str = "综合"
    person_name: str = ""
    person_org: str = ""
    databases: list[str] = field(default_factory=lambda: ["CNKI"])
    result_count: int = 10
    candidate_count: int = 0  # 0 表示与 result_count 一致（向后兼容）
    sources: list[str] = field(default_factory=lambda: ["CNKI"])
    ranking_mode: str = RankingMode.COMPREHENSIVE.value
    year_range: int = 0  # 0=不限，5=近5年，10=近10年
    # v0.13 高级检索条件（本阶段仅传递，Adapter 查询语法暂不使用）
    author_name: str = ""
    author_affiliation: str = ""
    start_date: str = ""
    end_date: str = ""
    # v0.13 Phase 6：单位筛选模式（soft=智能匹配 / strict=严格匹配）
    affiliation_filter_mode: str = "soft"
    # v0.13 Phase 8：来源检索已验证单位条件（如 CNKI 高级检索按 author_affiliation
    # 过滤）；为 True 时 strict 模式信任来源验证，不再因结果无 affiliation 元数据而误删
    affiliation_source_verified: bool = False

    def validate(self) -> None:
        if not self.research_direction.strip() and not self.author_name.strip():
            raise ValueError("请输入研究方向或人员姓名，至少填写一项")
        if not (MIN_RESULT_COUNT <= self.result_count <= MAX_RESULT_COUNT):
            raise ValueError(
                f"结果数量需在 {MIN_RESULT_COUNT}-{MAX_RESULT_COUNT} 之间"
            )
        if self.candidate_count != 0 and not (
            MIN_RESULT_COUNT <= self.candidate_count <= MAX_RESULT_COUNT
        ):
            raise ValueError(
                f"候选数量需在 {MIN_RESULT_COUNT}-{MAX_RESULT_COUNT} 之间"
            )
        if self.candidate_count != 0 and self.candidate_count < self.result_count:
            raise ValueError("候选数量不能小于展示数量")
        if self.ranking_mode not in {mode.value for mode in RankingMode}:
            raise ValueError(f"排序模式仅支持 {tuple(m.value for m in RankingMode)}")
        if self.year_range not in ALLOWED_YEAR_RANGES:
            raise ValueError(f"年份范围仅支持 {ALLOWED_YEAR_RANGES}")
        if self.affiliation_filter_mode not in AFFILIATION_FILTER_MODES:
            raise ValueError(
                f"单位筛选模式仅支持 {AFFILIATION_FILTER_MODES}"
            )
        self.validate_date_range()
        if not self.sources:
            raise ValueError("请至少选择一个数据来源")

    def validate_date_range(self) -> None:
        """校验 start_date / end_date（YYYY.MM）。

        - 空值合法（表示不限）。
        - 非空必须符合 YYYY.MM（月份 01-12）。
        - 结束时间不能早于开始时间（相同允许）。
        """
        for label, value in (("开始时间", self.start_date), ("结束时间", self.end_date)):
            if value and not DATE_PATTERN.match(value):
                raise ValueError(f"{label}格式应为 YYYY.MM（如 2023.09）")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("结束时间不能早于开始时间")

    @property
    def effective_candidate_count(self) -> int:
        """解析后的实际候选数量：显式 candidate_count 或回退到 result_count。"""
        return self.candidate_count if self.candidate_count != 0 else self.result_count
