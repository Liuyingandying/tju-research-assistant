"""统一版本与系统信息管理。"""
from __future__ import annotations

VERSION = "0.9.0"
APP_NAME = "信息自动检索整理系统"

# 对外展示的产品名 / 发布标签（v0.18 Phase 2.10-B）。
# 注意：不改 VERSION（tests/test_version.py 断言其为 "0.9.0"）；
# RELEASE_LABEL 仅用于设置页 / Dashboard 的"发布版本"展示。
PRODUCT_NAME = "TJU Research Assistant"
RELEASE_LABEL = "v0.18 RC"

SUPPORTED_SOURCES: list[str] = ["CNKI", "万方", "IEEE Xplore"]

FEATURES: list[str] = [
    "查询扩展",
    "多源检索",
    "结果去重",
    "排序",
    "全文页面",
    "报告生成",
    "跨语言查询翻译",
    "概念切分",
    "领域术语词典",
    "数据库专用查询构建",
    "IEEE 元数据解析",
    "现代化学术风格桌面界面",
]

# 面向首次使用者的能力列表（v0.18 Phase 2.10-B）：用于设置页/Dashboard 展示，
# 描述"这个系统能做什么"，与 FEATURES（实现特性）区分。
CAPABILITIES: list[str] = [
    "多源论文检索：CNKI / 万方 / IEEE Xplore 一站式查询",
    "AI 增强分析：研究背景、技术路线、创新点、与你的方向关系",
    "科研画像：按研究方向与研究阶段个性化排序与推荐",
    "论文库管理：收藏、阅读状态、标签与笔记",
    "AI 调研报告：6 章节领域综述，来源可追溯",
    "报告导出：Markdown / Word / PDF",
    "推荐理由：评分拆解与可解释排序",
]

# 测试状态不再静态维护：由 services/test_report.py 从 JUnit XML 动态读取
# （scripts/run_acceptance_check.py 会生成 runtime/test_summary.json）。
