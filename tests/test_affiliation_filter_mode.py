#!/usr/bin/env python3
"""v0.13 Phase 6：单位筛选模式（soft/strict）测试。

覆盖：
1. 默认 soft 保持旧行为
2. strict 匹配成功保留
3. strict 不匹配删除
4. strict 缺 Metadata 删除
5. soft 缺 Metadata 保留
6. GUI 模式切换
7. QueryRequest 字段传递
8. 旧 Query 兼容（无 affiliation_filter_mode 属性 → soft）
"""
import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.models.metadata import MetadataRecord
from tju_info_retrieval.models.query import (
    AFFILIATION_FILTER_MODES,
    QueryRequest,
)
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.services.filtering import FilterService


def _result(**kw):
    data = dict(rank=1, title="T", authors=["A"], year="2022",
                database="IEEE Xplore", detail_url="u1")
    data.update(kw)
    return SearchResult(**data)


def _request(**kw):
    data = dict(research_direction="太赫兹", author_affiliation="Tianjin University")
    data.update(kw)
    return QueryRequest(**data)


def _record(result_id, *affiliations):
    return MetadataRecord(result_id=result_id, affiliations=list(affiliations))


def _meta(*records):
    return {r.result_id: r for r in records}


# ============================================================
# 1/5. 默认 soft 保持旧行为
# ============================================================

class TestSoftDefault(unittest.TestCase):
    def setUp(self):
        self.svc = FilterService()

    def test_default_mode_is_soft(self):
        q = _request()
        self.assertEqual(q.affiliation_filter_mode, "soft")

    def test_soft_keeps_unknown_metadata(self):
        q = _request()  # soft 默认
        r = _result(detail_url="no-record")
        out = self.svc.filter([r], q, metadata_map={})
        self.assertEqual(len(out), 1)  # 保留

    def test_soft_matches_when_data_exists(self):
        q = _request()  # soft
        meta = _meta(_record("u1", "Tianjin University"))
        keep = _result(title="天大", detail_url="u1")
        drop = _result(title="北大", detail_url="u2")
        out = self.svc.filter([keep, drop], q, metadata_map=meta)
        # u2 无元数据 → soft 保留
        self.assertEqual({r.title for r in out}, {"天大", "北大"})

    def test_soft_removes_confirmed_mismatch(self):
        """soft 也删除已确认不匹配（有元数据但单位不符）。"""
        q = _request()
        meta = _meta(_record("u1", "Peking University"))
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(out, [])


# ============================================================
# 2/3/4. strict 模式
# ============================================================

class TestStrict(unittest.TestCase):
    def setUp(self):
        self.svc = FilterService()

    def test_strict_match_kept(self):
        q = _request(affiliation_filter_mode="strict")
        meta = _meta(_record("u1", "Tianjin University"))
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(len(out), 1)

    def test_strict_mismatch_removed(self):
        q = _request(affiliation_filter_mode="strict")
        meta = _meta(_record("u1", "Peking University"))
        r = _result(detail_url="u1")
        out = self.svc.filter([r], q, metadata_map=meta)
        self.assertEqual(out, [])

    def test_strict_unknown_metadata_removed(self):
        q = _request(affiliation_filter_mode="strict")
        r = _result(detail_url="no-record")
        out = self.svc.filter([r], q, metadata_map={})
        self.assertEqual(out, [])

    def test_strict_mixed(self):
        q = _request(affiliation_filter_mode="strict")
        meta = _meta(
            _record("u1", "Tianjin University"),
            _record("u2", "Peking University"),
        )
        match = _result(title="天大", detail_url="u1")
        mismatch = _result(title="北大", detail_url="u2")
        unknown = _result(title="未知", detail_url="u3")
        out = self.svc.filter([match, mismatch, unknown], q, metadata_map=meta)
        self.assertEqual([r.title for r in out], ["天大"])


# ============================================================
# 6/7. GUI 模式切换与字段传递
# ============================================================

class TestGuiMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        self.window = MainWindow()
        self.captured = []

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def _search(self):
        self.window.search_requested.connect(self.captured.append)
        self.window.input_direction.setText("太赫兹")
        self.window.input_author_affiliation.setText("天津大学")
        self.window._on_search()

    def test_combo_exists_with_options(self):
        labels = [
            self.window.combo_affiliation_mode.itemText(i)
            for i in range(self.window.combo_affiliation_mode.count())
        ]
        self.assertEqual(labels, ["智能匹配", "严格匹配"])
        # 默认智能匹配
        self.assertEqual(self.window.combo_affiliation_mode.currentData(), "soft")

    def test_default_soft_passed(self):
        self._search()
        self.assertEqual(self.captured[0].affiliation_filter_mode, "soft")

    def test_strict_passed(self):
        self.window.combo_affiliation_mode.setCurrentIndex(1)
        self._search()
        self.assertEqual(self.captured[0].affiliation_filter_mode, "strict")

    def test_query_request_validate_accepts_modes(self):
        for mode in AFFILIATION_FILTER_MODES:
            q = _request(affiliation_filter_mode=mode)
            q.validate()  # 不抛异常


# ============================================================
# 8. 旧 Query 兼容
# ============================================================

class TestOldQueryCompat(unittest.TestCase):
    def setUp(self):
        self.svc = FilterService()

    def test_legacy_query_object_without_mode(self):
        """旧 Query 对象缺 affiliation_filter_mode 属性 → 回退 soft。"""
        from types import SimpleNamespace

        legacy = SimpleNamespace(
            research_direction="太赫兹",
            author_name="",
            author_affiliation="Tianjin University",
            start_date="",
            end_date="",
        )
        r = _result(detail_url="no-record")
        out = self.svc.filter([r], legacy, metadata_map={})
        self.assertEqual(len(out), 1)  # soft 行为：保留

    def test_invalid_mode_rejected(self):
        q = _request(affiliation_filter_mode="banana")
        with self.assertRaises(ValueError):
            q.validate()

    def test_affiliation_stats(self):
        """统计三分类供状态提示。"""
        q = _request(affiliation_filter_mode="strict")
        meta = _meta(
            _record("u1", "Tianjin University"),
            _record("u2", "Peking University"),
        )
        results = [
            _result(detail_url="u1"),
            _result(detail_url="u2"),
            _result(detail_url="u3"),
        ]
        stats = FilterService.affiliation_stats(results, q, meta)
        self.assertEqual(stats, {"matched": 1, "mismatch": 1, "unknown": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
