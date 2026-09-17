#!/usr/bin/env python3
"""正式测试：统一版本与系统信息管理。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval import version as version_module
from tju_info_retrieval.version import APP_NAME, FEATURES, SUPPORTED_SOURCES, VERSION


class TestVersion(unittest.TestCase):
    def test_version_is_090(self):
        self.assertEqual(VERSION, "0.9.0")

    def test_app_name(self):
        self.assertEqual(APP_NAME, "信息自动检索整理系统")

    def test_supported_sources(self):
        self.assertEqual(SUPPORTED_SOURCES, ["CNKI", "万方", "IEEE Xplore"])

    def test_features_complete(self):
        for f in (
            "查询扩展", "多源检索", "结果去重", "排序", "全文页面", "报告生成",
            "跨语言查询翻译", "概念切分", "领域术语词典", "数据库专用查询构建",
            "IEEE 元数据解析",
        ):
            self.assertIn(f, FEATURES)

    def test_static_test_baseline_removed(self):
        # v0.7.4：测试状态改为动态读取（services/test_report.py），静态基线已移除
        self.assertFalse(hasattr(version_module, "TEST_BASELINE"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
