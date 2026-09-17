"""增强模式分析结果模型（v0.18 Phase 2.7-A）。

与 StructuredSummary（离线四字段，models/summary.py）完全分离：
- StructuredSummary 的字段保持 Evidence 约束语义，禁止修改；
- EnhancedSummary 承载 AI 增强分析的额外解释字段；
- provider / ai_generated 标注输出来源，禁止与基础 Evidence 混合。

本阶段仅 Mock provider 使用该模型；真实 LLM 接入后同样复用它。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 增强分析独有的解释字段（AI 生成内容）
ENHANCED_FIELDS = (
    "research_background",
    "technical_route",
    "innovation_points",
    "relation_to_user_direction",
    "reading_recommendation",
    "limitations",
)

ENHANCED_FIELD_LABELS = {
    "research_background": "研究背景",
    "technical_route": "技术路线",
    "innovation_points": "创新点",
    "relation_to_user_direction": "与研究方向的关系",
    "reading_recommendation": "阅读建议",
    "limitations": "局限性",
}

# 文献类型（v0.18 Phase 2.8-C）：枚举 + 中文显示
DOCUMENT_TYPES = (
    "experimental_method",
    "review",
    "theory_model",
    "application_case",
    "perspective",
    "other",
    "uncertain",
)
DOCUMENT_TYPE_LABELS = {
    "experimental_method": "实验/方法研究",
    "review": "综述",
    "theory_model": "理论/建模",
    "application_case": "应用/案例研究",
    "perspective": "观点/展望",
    "other": "其他",
    "uncertain": "类型不确定",
}

# 按文献类型自适应的字段标题（UI label；数据字段复用）
FIELD_LABELS_BY_TYPE = {
    "review": {
        "technical_route": "技术脉络",
        "innovation_points": "综述贡献",
    },
    "perspective": {
        "technical_route": "论证结构",
        "innovation_points": "核心观点",
    },
    # experimental_method / theory_model / application_case 等保持默认
}

# 阅读优先级（v0.18 Phase 2.9-B）：规范化枚举 + 中文显示
# 这是 AI 的"推荐优先级"，与用户实际阅读状态（reading_status）严格区分。
READING_DEEP = "deep_read"
READING_PRIORITY = "priority_read"
READING_SKIM = "skim"
READING_DEFER = "defer"
READING_PRIORITIES = (READING_DEEP, READING_PRIORITY, READING_SKIM, READING_DEFER)
READING_PRIORITY_LABELS = {
    READING_DEEP: "精读",
    READING_PRIORITY: "重点阅读",
    READING_SKIM: "泛读",
    READING_DEFER: "可暂缓",
    "": "未标注",
}
# 论文库本地排序权重（deep_read 最高；未标注最低）
READING_PRIORITY_ORDER = {
    READING_DEEP: 4,
    READING_PRIORITY: 3,
    READING_SKIM: 2,
    READING_DEFER: 1,
    "": 0,
}

# 未配置 Provider 时的固定提示（UI 层展示，不进模型值）
ENHANCED_NOT_CONFIGURED_TEXT = "增强分析服务未配置"


@dataclass
class EnhancedSummary:
    """AI 增强分析输出（仅新增解释字段，不含基础四字段）。

    基础四字段继续由 StructuredSummary/OfflineSummaryEngine 产出，
    二者在 UI 层分区展示，不合并、不互相覆盖。
    v0.18 Phase 2.8-C：新增 document_type / document_type_reason
    （带默认值，旧调用兼容）。
    """

    research_background: str = ""
    technical_route: str = ""
    innovation_points: str = ""
    relation_to_user_direction: str = ""
    reading_recommendation: str = ""
    limitations: str = ""
    # 文献类型自适应（2.8-C）：判定结果与依据
    document_type: str = ""
    document_type_reason: str = ""
    # 方向匹配等级（2.9-A）：strong/medium/weak/none/unknown
    direction_match_level: str = ""
    # 规范化阅读优先级（2.9-B）：deep_read/priority_read/skim/defer（""=未标注）
    reading_priority: str = ""
    # 输出来源标识（mock / openai-compatible 供应商名等）
    provider: str = ""
    # 是否为 AI 生成内容（True 时 UI 展示 AI 标识与免责声明）
    ai_generated: bool = False

    def to_dict(self) -> dict:
        return {
            "research_background": self.research_background,
            "technical_route": self.technical_route,
            "innovation_points": self.innovation_points,
            "relation_to_user_direction": self.relation_to_user_direction,
            "reading_recommendation": self.reading_recommendation,
            "limitations": self.limitations,
            "document_type": self.document_type,
            "document_type_reason": self.document_type_reason,
            "direction_match_level": self.direction_match_level,
            "reading_priority": self.reading_priority,
            "provider": self.provider,
            "ai_generated": self.ai_generated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EnhancedSummary":
        data = data or {}
        return cls(
            research_background=str(data.get("research_background") or ""),
            technical_route=str(data.get("technical_route") or ""),
            innovation_points=str(data.get("innovation_points") or ""),
            relation_to_user_direction=str(
                data.get("relation_to_user_direction") or ""),
            reading_recommendation=str(data.get("reading_recommendation") or ""),
            limitations=str(data.get("limitations") or ""),
            document_type=str(data.get("document_type") or ""),
            document_type_reason=str(data.get("document_type_reason") or ""),
            direction_match_level=str(data.get("direction_match_level") or ""),
            reading_priority=str(data.get("reading_priority") or ""),
            provider=str(data.get("provider") or ""),
            ai_generated=bool(data.get("ai_generated")),
        )

    def items(self) -> list[tuple[str, str, str]]:
        """[(key, 中文标签, 值)]，按固定字段顺序（仅解释字段）。"""
        return [
            (key, ENHANCED_FIELD_LABELS[key], getattr(self, key))
            for key in ENHANCED_FIELDS
        ]