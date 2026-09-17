"""只读演示数据提供者（v0.18 Phase 2.10-B Phase C）。

用途：让首次使用者在不联网、不配置 AI、不污染真实数据的前提下，
30 秒内看到系统完整链路的"样子"：

    3 篇示例论文  →  2 份 AI 增强分析  →  1 份 AI 调研报告

只读保证（硬约束）：

1. **无写路径**：本模块不导入任何 Store，不做任何文件写入；
2. **返回副本**：每次访问都从模块级模板**新建对象**，调用方修改不会
   影响下一次调用（`papers()` / `analyses()` / `report()` 相互独立）；
3. **不触碰真实目录**：不读写 `data/`、`runtime/`、用户真实目录。

与既有 ``demo_data.py`` 的区别：``demo_data`` 提供的是**搜索结果 dict**
（用于演示去重/排序/报告生成主流程）；本模块提供的是**论文库记录 /
增强分析 / 调研报告**对象（用于展示"收藏 → 分析 → 报告"完整链路）。
二者互不依赖。
"""

from __future__ import annotations

from tju_info_retrieval.models.enhanced_summary import (
    EnhancedSummary,
)
from tju_info_retrieval.models.library import LibraryRecord
from tju_info_retrieval.models.research_report import (
    REPORT_SECTION_LABELS,
    STATUS_GENERATED,
    ReportSection,
    ReportSource,
    ResearchReport,
    build_generation_scope,
)

# ---------------------------------------------------------------------------
# 模块级模板（只读；访问器一律返回新建副本）
# ---------------------------------------------------------------------------

# 演示用科研画像快照
_DEMO_PROFILE: dict = {
    "research_area": "太赫兹技术",
    "sub_direction": "太赫兹成像与超表面",
    "keywords": ["太赫兹", "成像", "超表面"],
    "excluded_topics": [],
    "research_stage": "master",
    "profile_version": 1,
}

# 3 篇示例论文（用于 LibraryRecord.from_search_result）
_DEMO_PAPER_DICTS: list[dict] = [
    {
        "title": "太赫兹超表面宽带成像系统研究",
        "authors": ["陈远", "刘明"],
        "year": "2026",
        "source": "光学学报",
        "database": "CNKI",
        "document_type": "期刊论文",
        "detail_url": "https://example.invalid/demo/1",
        "abstract": "本文提出一种基于超表面的太赫兹宽带成像系统，"
                    "在 0.1–0.5 THz 范围内实现高分辨率成像。",
        "doi": "10.1000/demo.1",
        "keywords": ["太赫兹", "超表面", "成像"],
        "saved_at": "2026-09-08T10:00:00+00:00",
    },
    {
        "title": "太赫兹量子级联激光器研究进展",
        "authors": ["王圣麟", "李华"],
        "year": "2025",
        "source": "物理学报",
        "database": "万方",
        "document_type": "综述",
        "detail_url": "https://example.invalid/demo/2",
        "abstract": "综述了太赫兹量子级联激光器的发展脉络与关键技术瓶颈。",
        "doi": "10.1000/demo.2",
        "keywords": ["太赫兹", "量子级联激光器"],
        "saved_at": "2026-09-09T14:30:00+00:00",
    },
    {
        "title": "Terahertz Imaging with Metasurfaces",
        "authors": ["Kun Meng", "Liguo Zhu"],
        "year": "2024",
        "source": "IRMMW-THz",
        "database": "IEEE Xplore",
        "document_type": "Conference Paper",
        "detail_url": "https://example.invalid/demo/3",
        "abstract": "We demonstrate metasurface-based terahertz imaging with "
                    "enhanced spatial resolution.",
        "doi": "10.1000/demo.3",
        "keywords": ["terahertz", "metasurface"],
        "saved_at": "2026-09-10T09:15:00+00:00",
    },
]

# 2 份 AI 增强分析（对应前 2 篇论文，按索引引用）
_DEMO_ANALYSIS_DICTS: list[tuple[int, dict]] = [
    (0, {
        "research_background": "太赫兹成像受衍射极限限制，传统方案的"
                               "分辨率与带宽难以兼顾。",
        "technical_route": "设计亚波长超表面单元，通过相位调控实现宽带"
                           "波前整形，配合时域光谱系统完成成像。",
        "innovation_points": "在 0.1–0.5 THz 宽带内同时提升分辨率与视场，"
                             "并给出可加工的单元结构。",
        "relation_to_user_direction": "与你的研究方向「太赫兹成像与超表面」"
                                      "高度相关。",
        "reading_recommendation": "建议精读，重点关注第 3 节的单元设计方法。",
        "limitations": "实验系统体积较大，尚未验证工程化封装。",
        "document_type": "experimental_method",
        "document_type_reason": "论文以实验系统与单元设计为主。",
        "direction_match_level": "strong",
        "reading_priority": "deep_read",
        "provider": "demo",
        "ai_generated": True,
    }),
    (1, {
        "research_background": "太赫兹源功率与工作温度是制约应用的关键。",
        "technical_route": "梳理量子级联结构、波导与散热方案的演进路线。",
        "innovation_points": "总结了高温工作与频率覆盖的最新进展。",
        "relation_to_user_direction": "为你的成像系统提供器件层面的支撑认知。",
        "reading_recommendation": "建议重点阅读，了解器件现状与趋势。",
        "limitations": "综述未涉及与成像系统的联合优化。",
        "document_type": "review",
        "document_type_reason": "系统性梳理领域进展，属综述。",
        "direction_match_level": "medium",
        "reading_priority": "priority_read",
        "provider": "demo",
        "ai_generated": True,
    }),
]

# 1 份调研报告（演示）
_DEMO_REPORT_TITLE = "太赫兹技术调研报告（演示）"

_DEMO_SECTION_CONTENT: dict[str, tuple[str, list[int]]] = {
    "background": (
        "太赫兹波段位于微波与红外之间，兼具穿透性与光谱分辨能力，"
        "在成像、通信与无损检测等方向受到持续关注。[P1][P2]",
        [1, 2],
    ),
    "trend": (
        "近年研究由单一器件向「器件 + 系统 + 算法」协同演进，"
        "超表面与量子级联器件是两条主线。[P1][P3]",
        [1, 3],
    ),
    "tech_route": (
        "典型技术路线包括：超表面波前整形、时域光谱成像、"
        "以及量子级联光源的波导与散热设计。[P1][P2][P3]",
        [1, 2, 3],
    ),
    "challenge": (
        "主要挑战在于宽带高效率器件、系统小型化与工程可靠性。",
        [],
    ),
    "future": (
        "未来方向指向可重构超表面、片上集成与智能重建算法的结合。[P1]",
        [1],
    ),
    "profile_link": (
        "与你的研究方向「太赫兹成像与超表面」及硕士阶段学习目标一致，"
        "建议优先精读实验方法类文献。[P1]",
        [1],
    ),
}

# 章节顺序（与 REPORT_SECTION_KEYS 一致）
_DEMO_SECTION_ORDER: list[str] = [
    "background", "trend", "tech_route", "challenge", "future",
    "profile_link",
]


# ---------------------------------------------------------------------------
# 访问器（全部返回新建副本）
# ---------------------------------------------------------------------------

def _build_paper(data: dict) -> LibraryRecord:
    """从模板 dict 新建一条 LibraryRecord（不落盘）。"""
    return LibraryRecord.from_search_result(dict(data))


def _build_analysis(data: dict) -> EnhancedSummary:
    return EnhancedSummary.from_dict(dict(data))


def _build_report_source(index: int, data: dict) -> ReportSource:
    return ReportSource(
        paper_id=str(data.get("id") or f"demo-{index}"),
        title=str(data.get("title") or ""),
        authors=list(data.get("authors") or []),
        year=str(data.get("year") or ""),
        doi=str(data.get("doi") or ""),
        abstract=str(data.get("abstract") or ""),
        artifact_type=str(data.get("artifact_type") or "paper"),
        source=str(data.get("source") or ""),
        document_type=str(data.get("document_type") or ""),
        index=index,
    )


class DemoDataProvider:
    """只读演示数据提供者（3 论文 / 2 分析 / 1 报告）。

    所有方法均为类方法且**无副作用**：不写文件、不依赖网络、不缓存
    可变状态。返回对象为每次新建副本，调用方修改不影响后续调用。
    """

    #: 演示论文数量
    PAPER_COUNT = len(_DEMO_PAPER_DICTS)
    #: 演示增强分析数量
    ANALYSIS_COUNT = len(_DEMO_ANALYSIS_DICTS)
    #: 演示报告数量
    REPORT_COUNT = 1

    # ---------- 论文 ----------

    @classmethod
    def papers(cls) -> list[LibraryRecord]:
        """3 篇示例论文（LibraryRecord 副本，未持久化）。"""
        return [_build_paper(data) for data in _DEMO_PAPER_DICTS]

    # ---------- AI 分析 ----------

    @classmethod
    def analyses(cls) -> list[EnhancedSummary]:
        """2 份 AI 增强分析（副本）。"""
        return [_build_analysis(data) for _idx, data in _DEMO_ANALYSIS_DICTS]

    @classmethod
    def analysis_pairs(cls) -> list[tuple[int, EnhancedSummary]]:
        """(论文索引, 分析) 列表；索引对应 papers() 的顺序。"""
        return [(idx, _build_analysis(data))
                for idx, data in _DEMO_ANALYSIS_DICTS]

    # ---------- 报告 ----------

    @classmethod
    def report(cls) -> ResearchReport:
        """1 份 AI 调研报告（副本；来源可追溯）。"""
        papers = cls.papers()
        sources = [
            _build_report_source(i, data)
            for i, data in enumerate(_DEMO_PAPER_DICTS, start=1)
        ]
        sections: list[ReportSection] = []
        for order, key in enumerate(_DEMO_SECTION_ORDER):
            content, refs = _DEMO_SECTION_CONTENT.get(key, ("", []))
            source_ids = [
                sources[i - 1].paper_id for i in refs
                if 1 <= i <= len(sources)
            ]
            sections.append(ReportSection(
                title=REPORT_SECTION_LABELS.get(key, key),
                content=content,
                order=order,
                key=key,
                source_papers=source_ids,
                insufficient=not source_ids,
            ))
        return ResearchReport(
            id="demo-report-0001",
            title=_DEMO_REPORT_TITLE,
            created_time="2026-09-10T15:00:00+00:00",
            profile_snapshot=dict(_DEMO_PROFILE),
            papers=sources,
            sections=sections,
            provider="demo",
            model="demo",
            status=STATUS_GENERATED,
            generation_scope=build_generation_scope(
                len(sources), _DEMO_PROFILE.get("profile_version")),
        )

    # ---------- 聚合快照（Dashboard 用） ----------

    @classmethod
    def profile(cls) -> dict:
        """演示科研画像快照（副本）。"""
        return dict(_DEMO_PROFILE)

    @classmethod
    def snapshot(cls) -> dict:
        """Dashboard 演示模式所需的聚合数据（全部为副本）。

        结构::

            {
              "profile": {"research_area", "research_stage", ...},
              "stats": {"paper_count", "analyzed_count", "report_count"},
              "recent_papers": [{title, authors, year, source, saved_at}, ...],
              "recent_reports": [{title, created_time, paper_count}, ...],
            }
        """
        papers = cls.papers()
        recent_papers = [
            {
                "title": p.title,
                "authors": list(p.authors),
                "year": p.year,
                "source": p.source,
                "saved_at": p.saved_at,
            }
            for p in sorted(papers, key=lambda r: r.saved_at or "", reverse=True)
        ]
        report = cls.report()
        return {
            "profile": dict(_DEMO_PROFILE),
            "stats": {
                "paper_count": cls.PAPER_COUNT,
                "analyzed_count": cls.ANALYSIS_COUNT,
                "report_count": cls.REPORT_COUNT,
            },
            "recent_papers": recent_papers,
            "recent_reports": [
                {
                    "title": report.title,
                    "created_time": report.created_time,
                    "paper_count": report.paper_count,
                }
            ],
        }


__all__ = ["DemoDataProvider"]
