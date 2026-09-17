#!/usr/bin/env python3
"""正式测试：论文库清理脚本（v0.18 Phase 2.9-B.1-C）。

覆盖：
- 默认 dry-run 绝不修改文件；
- `--execute` 只删白名单命中的测试残留，真实论文一条不动；
- `--include-marked` 才删除带测试标记的记录；
- `--clear-marked-note` 只清标记、保留记录；
- 执行前自动备份；
- 模糊/相近标题不会被误删。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import cleanup_test_library_records as cleanup

REAL_PAPERS = [
    {
        "id": "real-1",
        "title": "太赫兹技术在癌症诊断医学中的应用",
        "authors": ["何禹霖"],
        "year": "2026",
        "source": "中国激光医学杂志",
        "detail_url": "https://kns.cnki.net/kcms2/article/abstract?v=abc",
        "abstract": "真实摘要",
        "tags": ["医学应用"],
        "note": "",
        "reading_status": "skimmed",
    },
    {
        "id": "real-2",
        "title": "游戏“玩工”劳动过程的同意制造与抵抗实践——以RPG类手游为例",
        "authors": ["张三"],
        "year": "2024",
        "source": "社会学研究",
        "tags": [],
        "note": "",
        "reading_status": "unread",
    },
]
TEST_RECORDS = [
    {
        "id": "test-1", "title": "Test paper", "authors": ["Author A"],
        "year": "2025", "source": "Test Journal", "abstract": None,
        "detail_url": None, "tags": [], "note": "",
        "reading_status": "unread",
    },
    {
        "id": "test-2", "title": "Deep THz study", "authors": ["Author A"],
        "year": "2025", "source": "Test Journal", "abstract": None,
        "detail_url": "https://ieeexplore.ieee.org/document/12345/",
        "tags": [], "note": "", "reading_status": "unread",
    },
    {
        "id": "test-3", "title": "Test Paper", "authors": ["Author A"],
        "year": "2025", "source": "Test Journal", "abstract": None,
        "detail_url": None, "tags": [], "note": "",
        "reading_status": "unread",
    },
]
MARKED_PAPER = {
    "id": "marked-1",
    "title": "某真实论文（带验收备注）",
    "authors": ["李四"],
    "year": "2026",
    "source": "某期刊",
    "abstract": "真实摘要",
    "tags": ["信道"],
    "note": "验收测试备注：组会重点看图3",
    "reading_status": "reading",
}


@pytest.fixture
def library_file(tmp_path) -> Path:
    path = tmp_path / "library.json"
    path.write_text(json.dumps(
        {"schema_version": 2,
         "records": [*REAL_PAPERS, *TEST_RECORDS, MARKED_PAPER]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _titles(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [r.get("title") for r in data["records"]]


class TestDryRun:
    def test_dry_run_does_not_modify_file(self, library_file):
        before = library_file.read_bytes()
        code = cleanup.main(["--dry-run", "--library", str(library_file)])
        assert code == 0
        assert library_file.read_bytes() == before

    def test_default_is_dry_run(self, library_file, capsys):
        """不带 --execute 时等同 dry-run。"""
        before = library_file.read_bytes()
        code = cleanup.main(["--library", str(library_file)])
        out = capsys.readouterr().out
        assert code == 0
        assert "DRY-RUN" in out
        assert library_file.read_bytes() == before

    def test_dry_run_lists_only_rule1_candidates(self, library_file, capsys):
        cleanup.main(["--dry-run", "--library", str(library_file)])
        out = capsys.readouterr().out
        assert "命中候选 3 条" in out
        assert "test_title" in out
        # 真实论文与带标记论文都不在默认候选中
        assert "太赫兹技术在癌症诊断医学中的应用" not in out
        assert "marked-1" not in out

    def test_dry_run_no_backup_created(self, library_file, tmp_path):
        cleanup.main(["--dry-run", "--library", str(library_file)])
        assert list(tmp_path.glob("*.bak-*")) == []


class TestExecute:
    def test_execute_removes_only_test_titles(self, library_file):
        code = cleanup.main(["--execute", "--library", str(library_file)])
        assert code == 0
        titles = _titles(library_file)
        assert "Test paper" not in titles
        assert "Test Paper" not in titles   # 忽略大小写
        assert "Deep THz study" not in titles
        # 真实论文 + 带标记论文仍在
        assert "太赫兹技术在癌症诊断医学中的应用" in titles
        assert "游戏“玩工”劳动过程的同意制造与抵抗实践——以RPG类手游为例" in titles
        assert "某真实论文（带验收备注）" in titles
        assert len(titles) == 3

    def test_execute_writes_backup(self, library_file, tmp_path):
        cleanup.main(["--execute", "--library", str(library_file)])
        backups = list(tmp_path.glob("library.json.bak-*"))
        assert len(backups) == 1
        original = json.loads(backups[0].read_text(encoding="utf-8"))
        assert len(original["records"]) == 6      # 备份是删除前的完整内容

    def test_execute_result_is_valid_schema(self, library_file):
        cleanup.main(["--execute", "--library", str(library_file)])
        data = json.loads(library_file.read_text(encoding="utf-8"))
        assert data["schema_version"] == 2
        assert isinstance(data["records"], list)

    def test_include_marked_removes_marked_records(self, library_file):
        cleanup.main(["--execute", "--include-marked",
                      "--library", str(library_file)])
        titles = _titles(library_file)
        assert "某真实论文（带验收备注）" not in titles
        assert "太赫兹技术在癌症诊断医学中的应用" in titles
        assert len(titles) == 2

    def test_clear_marked_note_keeps_records(self, library_file):
        cleanup.main(["--execute", "--clear-marked-note",
                      "--library", str(library_file)])
        data = json.loads(library_file.read_text(encoding="utf-8"))
        by_id = {r["id"]: r for r in data["records"]}
        # 记录保留
        assert "marked-1" in by_id
        assert by_id["marked-1"]["title"] == "某真实论文（带验收备注）"
        # 标记被清掉，其余备注内容保留
        note = by_id["marked-1"]["note"]
        assert "验收测试" not in note
        assert "图3" in note
        # 测试残留仍在（未删）
        assert "test-1" in by_id

    def test_no_candidates_execute_is_noop(self, tmp_path):
        path = tmp_path / "clean.json"
        path.write_text(json.dumps(
            {"schema_version": 2, "records": REAL_PAPERS},
            ensure_ascii=False), encoding="utf-8")
        before = path.read_bytes()
        code = cleanup.main(["--execute", "--library", str(path)])
        assert code == 0
        assert path.read_bytes() == before


class TestRules:
    @pytest.mark.parametrize("title,matched", [
        ("Test paper", True),
        ("test paper", True),
        ("  Test Paper  ", True),
        ("Deep THz study", True),
        ("deep thz study", True),
        ("Test paper 关于太赫兹", False),      # 只是包含词 → 不删
        ("My Test paper review", False),
        ("太赫兹技术在癌症诊断医学中的应用", False),
        ("Deep THz study on channel", False),
        ("", False),
        (None, False),
    ])
    def test_title_rule_is_exact_match(self, title, matched):
        record = {"title": title}
        assert cleanup._record_matches(record, cleanup.RULE_TEST_TITLE) is matched

    def test_marker_rule(self):
        assert cleanup._record_matches(
            {"note": "验收测试备注：x"}, cleanup.RULE_TEST_MARKER) is True
        assert cleanup._record_matches(
            {"note": "真实笔记"}, cleanup.RULE_TEST_MARKER) is False

    def test_fixture_source_rule_requires_both(self):
        assert cleanup._record_matches(
            {"source": "Test Journal", "authors": ["Author A"]},
            cleanup.RULE_FIXTURE_SOURCE) is True
        assert cleanup._record_matches(
            {"source": "Test Journal", "authors": ["真实作者"]},
            cleanup.RULE_FIXTURE_SOURCE) is False
        assert cleanup._record_matches(
            {"source": "中国知网", "authors": ["Author A"]},
            cleanup.RULE_FIXTURE_SOURCE) is False

    def test_find_candidates_assigns_first_matching_rule(self):
        records = [{"id": "a", "title": "Test paper",
                    "note": "验收测试备注"},
                   {"id": "b", "title": "真实论文"}]
        found = cleanup.find_candidates(
            records, [cleanup.RULE_TEST_TITLE, cleanup.RULE_TEST_MARKER])
        assert len(found) == 1
        assert found[0][1] == cleanup.RULE_TEST_TITLE

    def test_missing_file_returns_error(self, tmp_path):
        code = cleanup.main(["--dry-run",
                             "--library", str(tmp_path / "nope.json")])
        assert code == 1


class TestRealLibraryUntouched:
    def test_script_does_not_touch_real_library_in_dry_run(self):
        """对**真实**论文库跑 dry-run：文件必须字节不变。"""
        from tju_info_retrieval import app_paths

        real = app_paths.project_root() / "data" / "library.json"
        if not real.is_file():
            pytest.skip("真实论文库不存在")
        before = real.read_bytes()
        cleanup.main(["--dry-run", "--library", str(real)])
        assert real.read_bytes() == before
