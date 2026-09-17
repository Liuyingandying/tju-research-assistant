"""演示数据（仅用于展示，不发起真实检索）。

包含 CNKI / 万方 / IEEE 三个来源的示例论文，其中含一对跨来源重复标题，
用于演示 ResultMerger 去重、RankingService 排序、报告生成与导出。

加载优先级：runtime/demo_results.json（可自定义）→ 内置 DEMO_RESULTS。
"""
from __future__ import annotations

import json
from pathlib import Path

from tju_info_retrieval.app_paths import demo_results_path

DEMO_QUERY_INFO: dict = {
    "research_direction": "太赫兹（演示）",
    "expansion_direction": "综合",
    "sources": ["CNKI", "万方", "IEEE Xplore"],
}

DEMO_RESULTS: list[dict] = [
    {
        "rank": 1,
        "title": "太赫兹成像方法研究",
        "authors": ["张三", "李四"],
        "source": "期刊A",
        "year": "2026",
        "document_type": "期刊论文",
        "database": "CNKI",
        "detail_url": None,
        "abstract": None,
        "doi": None,
        "keywords": ["太赫兹", "成像"],
        "venue": "期刊A",
        "authors_raw": "张三；李四",
    },
    {
        "rank": 2,
        "title": "太赫兹通信系统设计",
        "authors": ["王五"],
        "source": "期刊B",
        "year": "2025",
        "document_type": "期刊论文",
        "database": "CNKI",
        "detail_url": None,
        "abstract": None,
        "doi": None,
        "keywords": ["太赫兹", "通信"],
        "venue": "期刊B",
        "authors_raw": "王五",
    },
    {
        "rank": 3,
        "title": "太赫兹波导器件仿真",
        "authors": ["赵六", "钱七"],
        "source": "物理学报",
        "year": "2024",
        "document_type": "期刊论文",
        "database": "万方",
        "detail_url": None,
        "abstract": None,
        "doi": None,
        "keywords": ["太赫兹", "波导"],
        "venue": "物理学报",
        "authors_raw": "赵六；钱七",
    },
    # 与第 1 条同名（跨来源重复），用于演示去重
    {
        "rank": 4,
        "title": "太赫兹成像方法研究",
        "authors": ["张三", "李四"],
        "source": "期刊A",
        "year": "2026",
        "document_type": "期刊论文",
        "database": "万方",
        "detail_url": None,
        "abstract": None,
        "doi": None,
        "keywords": [],
        "venue": "期刊A",
        "authors_raw": "张三；李四",
    },
    {
        "rank": 5,
        "title": "Terahertz Imaging Method",
        "authors": ["Kun Meng", "Liguo Zhu"],
        "source": "IRMMW-THz",
        "year": "2024",
        "document_type": "Conference Paper",
        "database": "IEEE Xplore",
        "detail_url": None,
        "abstract": None,
        "doi": None,
        "keywords": ["terahertz"],
        "venue": "2024 49th International Conference on Infrared, Millimeter, and Terahertz Waves (IRMMW-THz)",
        "authors_raw": "Kun Meng; Liguo Zhu",
    },
    {
        "rank": 6,
        "title": "Terahertz Quantum Cascade Lasers",
        "authors": ["H. Li", "C. Wang"],
        "source": "CLEO-PR",
        "year": "2018",
        "document_type": "Conference Paper",
        "database": "IEEE Xplore",
        "detail_url": None,
        "abstract": None,
        "doi": None,
        "keywords": ["quantum cascade"],
        "venue": "Conference on Lasers and Electro-Optics Pacific Rim (CLEO-PR)",
        "authors_raw": "H. Li; C. Wang",
    },
    # v0.17 Phase 2.1：多类型演示（专利 / 新闻），typed 元数据由
    # artifact.py 的 PatentMetadata / NewsMetadata 辅助构造
    {
        "rank": 7,
        "title": "一种太赫兹超表面宽带成像系统及方法",
        "authors": ["陈远，刘明"],
        "source": None,
        "year": "2026",
        "document_type": "发明专利",
        "database": "万方专利",
        "detail_url": None,
        "abstract": None,
        "doi": None,
        "keywords": ["太赫兹", "超表面", "成像"],
        "venue": None,
        "authors_raw": None,
        "artifact_type": "patent",
        "artifact_metadata": {
            "inventors": ["陈远", "刘明"],
            "applicant": "天津大学",
            "publication_number": "CN202610012345A",
            "application_number": "CN202610056789.0",
            "date": "2026-05-12",
        },
    },
    {
        "rank": 8,
        "title": "天大太赫兹团队在宽频成像方向取得新突破",
        "authors": [],
        "source": None,
        "year": "2026",
        "document_type": "新闻报道",
        "database": "演示新闻",
        "detail_url": None,
        "abstract": None,
        "doi": None,
        "keywords": ["太赫兹", "成像"],
        "venue": None,
        "authors_raw": None,
        "artifact_type": "news",
        "artifact_metadata": {
            "media": "科技日报（演示）",
            "publish_time": "2026-08-20",
            "authors": ["实习记者 周晓"],
            "related_person": ["王圣麟"],
        },
    },
]


def _default_json_path() -> Path:
    return demo_results_path()


def load_demo_results() -> dict:
    """加载演示数据：优先 runtime/demo_results.json，缺失时用内置数据。"""
    path = _default_json_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("results"):
                return data
        except Exception:
            pass
    return {
        "query_info": dict(DEMO_QUERY_INFO),
        "results": [dict(r) for r in DEMO_RESULTS],
    }
