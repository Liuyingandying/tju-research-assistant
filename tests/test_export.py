#!/usr/bin/env python3
"""正式测试：结果导出 JSON / CSV / Markdown。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import unittest

from tju_info_retrieval.services import export as export_service

SAMPLE = [
    {
        "rank": 1,
        "title": "标题A",
        "authors": ["张三", "李四"],
        "source": "期刊A",
        "year": "2026",
        "document_type": "期刊",
        "database": "CNKI",
        "detail_url": "https://kns.cnki.net/a1",
        "abstract": "摘要内容",
        "doi": "10.1234/test",
        "keywords": ["关键词1", "关键词2"],
        "venue": "期刊A",
        "authors_raw": "张三；李四",
    },
    {
        "rank": 2,
        "title": "标题B",
        "authors": [],
        "source": None,
        "year": None,
        "document_type": None,
        "database": "CNKI",
        "detail_url": None,
        "abstract": None,
        "doi": None,
        "keywords": [],
        "venue": None,
        "authors_raw": None,
    },
]


class TestExport(unittest.TestCase):
    def test_export_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.json"
            export_service.export_json(SAMPLE, p)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]["title"], "标题A")
            self.assertEqual(data[0]["authors"], "张三；李四")

    def test_export_csv(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.csv"
            export_service.export_csv(SAMPLE, p)
            text = p.read_text(encoding="utf-8-sig")
            self.assertIn("标题", text)
            self.assertIn("标题A", text)
            self.assertIn("https://kns.cnki.net/a1", text)

    def test_export_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.md"
            export_service.export_markdown(SAMPLE, p)
            text = p.read_text(encoding="utf-8")
            self.assertIn("标题A", text)
            self.assertIn("| 序号 |", text)

    def test_export_no_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("out.json", "out.csv", "out.md"):
                p = Path(d) / name
                if name.endswith(".json"):
                    export_service.export_json(SAMPLE, p)
                elif name.endswith(".csv"):
                    export_service.export_csv(SAMPLE, p)
                else:
                    export_service.export_markdown(SAMPLE, p)
                low = p.read_text(encoding="utf-8").lower()
                for pat in ("cookie", "token", "password", "authorization"):
                    self.assertNotIn(pat, low, f"{name} 不应包含 {pat}")

    def test_export_json_preserves_artifact_metadata(self):
        """v0.17 Phase 2.1：JSON 导出保留多类型成果的 typed 元数据。"""
        typed_row = {
            **SAMPLE[0],
            "artifact_type": "patent",
            "artifact_metadata": {
                "inventors": ["陈远"],
                "publication_number": "CN202610012345A",
                "applicant": "天津大学",
            },
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.json"
            export_service.export_json([typed_row], p)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data[0]["artifact_type"], "patent")
            self.assertEqual(data[0]["artifact_metadata"]["publication_number"],
                             "CN202610012345A")


if __name__ == "__main__":
    unittest.main(verbosity=2)
