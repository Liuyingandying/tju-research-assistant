"""Research Profile 科研画像模型（v0.18 Phase 2.9-A）。

用户科研方向信息（本机单用户，非 secret）：
- research_area：研究领域（如"太赫兹"）；
- sub_direction：主要方向列表（如 THz-ISAC / THz sensing）；
- keywords：关注关键词；
- excluded_topics：暂不关注主题；
- research_stage：科研阶段枚举；
- preferred_output_style：输出详略偏好；
- profile_version：每次保存自增（进入增强分析缓存 key——用户改方向后
  旧 AI 分析自动失效）。

Profile 是用户上下文，与 Evidence（论文事实）严格分离，禁止混合。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# 科研阶段枚举
STAGE_UNDERGRADUATE = "undergraduate"
STAGE_MASTER = "master"
STAGE_PHD = "phd"
STAGE_RESEARCHER = "researcher"
RESEARCH_STAGES = (
    STAGE_UNDERGRADUATE,
    STAGE_MASTER,
    STAGE_PHD,
    STAGE_RESEARCHER,
)
RESEARCH_STAGE_LABELS = {
    STAGE_UNDERGRADUATE: "本科生",
    STAGE_MASTER: "硕士生",
    STAGE_PHD: "博士生",
    STAGE_RESEARCHER: "研究员",
}

# 输出风格枚举
STYLE_BRIEF = "brief"
STYLE_NORMAL = "normal"
STYLE_DETAILED = "detailed"
OUTPUT_STYLES = (STYLE_BRIEF, STYLE_NORMAL, STYLE_DETAILED)
OUTPUT_STYLE_LABELS = {
    STYLE_BRIEF: "简洁",
    STYLE_NORMAL: "标准",
    STYLE_DETAILED: "详细",
}

# 方向匹配等级（LLM 输出枚举）
MATCH_STRONG = "strong"
MATCH_MEDIUM = "medium"
MATCH_WEAK = "weak"
MATCH_NONE = "none"
MATCH_UNKNOWN = "unknown"
DIRECTION_MATCH_LEVELS = (
    MATCH_STRONG, MATCH_MEDIUM, MATCH_WEAK, MATCH_NONE, MATCH_UNKNOWN,
)
DIRECTION_MATCH_LABELS = {
    MATCH_STRONG: "强相关",
    MATCH_MEDIUM: "中等相关",
    MATCH_WEAK: "弱相关",
    MATCH_NONE: "不相关",
    MATCH_UNKNOWN: "无法判断",
}


def default_profile() -> "ResearchProfile":
    """发布版默认画像：不含任何具体研究方向（v0.18 Phase 2.9-B）。

    新用户首次使用得到空画像，须自行填写（禁止预置"太赫兹/THz-ISAC"
    等开发用户专属方向）。科研阶段默认本科生，输出风格默认标准。
    """
    return ResearchProfile(
        research_area="",
        sub_direction=[],
        keywords=[],
        excluded_topics=[],
        research_stage=STAGE_UNDERGRADUATE,
        preferred_output_style=STYLE_NORMAL,
    )


@dataclass
class ResearchProfile:
    """用户科研画像（本机单用户）。"""

    research_area: str = ""
    sub_direction: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    excluded_topics: list[str] = field(default_factory=list)
    research_stage: str = STAGE_UNDERGRADUATE
    preferred_output_style: str = STYLE_NORMAL
    # 每次保存 +1；进入增强分析缓存 key
    profile_version: int = 1
    updated_time: str = ""

    def to_dict(self) -> dict:
        return {
            "research_area": self.research_area,
            "sub_direction": list(self.sub_direction),
            "keywords": list(self.keywords),
            "excluded_topics": list(self.excluded_topics),
            "research_stage": self.research_stage,
            "preferred_output_style": self.preferred_output_style,
            "profile_version": int(self.profile_version),
            "updated_time": self.updated_time,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ResearchProfile":
        data = data or {}
        stage = str(data.get("research_stage") or STAGE_UNDERGRADUATE)
        if stage not in RESEARCH_STAGES:
            stage = STAGE_UNDERGRADUATE
        style = str(data.get("preferred_output_style") or STYLE_NORMAL)
        if style not in OUTPUT_STYLES:
            style = STYLE_NORMAL

        def _str_list(value) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(v).strip() for v in value if str(v).strip()]

        return cls(
            research_area=str(data.get("research_area") or "").strip(),
            sub_direction=_str_list(data.get("sub_direction")),
            keywords=_str_list(data.get("keywords")),
            excluded_topics=_str_list(data.get("excluded_topics")),
            research_stage=stage,
            preferred_output_style=style,
            profile_version=int(data.get("profile_version") or 1),
            updated_time=str(data.get("updated_time") or ""),
        )

    def is_configured(self) -> bool:
        """画像是否已配置（领域/方向/关键词任一非空）。

        未配置 → 增强分析按"无画像"处理：direction_match_level=unknown，
        relation 使用固定措辞，禁止个性化相关性表述。
        """
        return bool(
            (self.research_area or "").strip()
            or [s for s in self.sub_direction if str(s).strip()]
            or [k for k in self.keywords if str(k).strip()]
        )

    def bump_version(self) -> int:
        """保存时自增版本（触发增强缓存失效）。"""
        self.profile_version = int(self.profile_version or 0) + 1
        self.updated_time = time.strftime("%Y-%m-%dT%H:%M:%S")
        return self.profile_version

    def context_summary(self) -> str:
        """生成给 LLM 的用户研究上下文文本（不进 Evidence）。"""
        parts = [f"研究领域：{self.research_area or '未提供'}"]
        if self.sub_direction:
            parts.append(f"主要方向：{'、'.join(self.sub_direction)}")
        if self.keywords:
            parts.append(f"关注关键词：{'、'.join(self.keywords)}")
        if self.excluded_topics:
            parts.append(f"暂不关注：{'、'.join(self.excluded_topics)}")
        stage = RESEARCH_STAGE_LABELS.get(self.research_stage,
                                          self.research_stage)
        parts.append(f"科研阶段：{stage}")
        return "；".join(parts)