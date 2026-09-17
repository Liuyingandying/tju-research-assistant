#!/usr/bin/env python3
"""正式测试：发布数据安全检查工具（v0.18 Phase 2.9-B.1-D）。

覆盖 6 类检查各自的检出能力 + 干净目录 PASS：
1. 测试标题残留；2. 测试标记（标签/备注）；3. mock provider 数据；
4. 测试 API key；5. cookie / token / 登录态；6. Edge profile。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import check_release_data as checker


def _write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")
    return path


def _scan(path: Path) -> tuple[bool, list]:
    return checker.run([path], verbose=False)


def _rules(findings) -> set[str]:
    return {f.rule for f in findings}


class TestCleanTree:
    def test_clean_tree_passes(self, tmp_path):
        _write(tmp_path / "data" / "library.json", {
            "schema_version": 2,
            "records": [{
                "id": "r1", "title": "太赫兹技术在癌症诊断医学中的应用",
                "authors": ["何禹霖"], "year": "2026", "source": "某期刊",
                "tags": ["医学应用"], "note": "组会重点看图3",
                "enhanced_summary": {"provider": "openai-compatible",
                                     "ai_generated": True},
            }],
        })
        _write(tmp_path / "runtime" / "research_profile.json",
               {"research_area": "太赫兹"})
        passed, findings = _scan(tmp_path)
        assert passed is True, [f"{f.rule}:{f.path}" for f in findings]
        assert findings == []

    def test_missing_dirs_are_skipped(self, tmp_path):
        passed, findings = _scan(tmp_path / "nonexistent")
        assert passed is True
        assert findings == []


class TestDetections:
    def test_detects_test_title(self, tmp_path):
        _write(tmp_path / "data" / "library.json", {
            "schema_version": 2,
            "records": [{"id": "t1", "title": "Test paper"},
                        {"id": "t2", "title": "Deep THz study"}],
        })
        passed, findings = _scan(tmp_path)
        assert passed is False
        assert "test-title" in _rules(findings)

    def test_detects_test_marker(self, tmp_path):
        _write(tmp_path / "data" / "library.json", {
            "schema_version": 2,
            "records": [{"id": "t1", "title": "真实论文",
                         "note": "验收测试备注：组会重点看图3"}],
        })
        passed, findings = _scan(tmp_path)
        assert passed is False
        assert "test-marker" in _rules(findings)

    def test_detects_mock_provider(self, tmp_path):
        _write(tmp_path / "runtime" / "cache" / "enhanced_summary" / "a.json",
               {"summary": {"provider": "mock", "ai_generated": False}})
        passed, findings = _scan(tmp_path)
        assert passed is False
        assert "mock-provider" in _rules(findings)

    def test_detects_test_api_key(self, tmp_path):
        _write(tmp_path / "runtime" / "summary_provider.json",
               {"enabled": True, "api_key": "test-key-abc123"})
        passed, findings = _scan(tmp_path)
        assert passed is False
        assert "test-api-key" in _rules(findings)

    def test_detects_plaintext_secret_field(self, tmp_path):
        _write(tmp_path / "runtime" / "cfg.json",
               {"api_key": "abcdef123456"})
        passed, findings = _scan(tmp_path)
        assert passed is False
        assert "test-api-key" in _rules(findings)

    def test_detects_cookie_and_storage_state(self, tmp_path):
        _write(tmp_path / "runtime" / "cache" / "dump.json",
               {"cookies": [{"name": "SESSION", "value": "x"}],
                "origins": []})
        passed, findings = _scan(tmp_path)
        assert passed is False
        assert "secret-content" in _rules(findings)

    def test_detects_bearer_token(self, tmp_path):
        _write(tmp_path / "data" / "trace.log",
               "Authorization: Bearer abcdefghijklmnop1234567890")
        passed, findings = _scan(tmp_path)
        assert passed is False
        assert "secret-content" in _rules(findings)

    def test_detects_storage_state_file_name(self, tmp_path):
        """dev scope：登录态属开发机本地数据（LOCAL，不阻断），release scope 下为 CRITICAL。"""
        _write(tmp_path / "data" / "tju_storage_state.json", {"cookies": []})
        dev_passed, dev_findings = checker.run([tmp_path], verbose=False,
                                               scope=checker.SCOPE_DEV)
        assert dev_passed is True
        assert any(f.severity == checker.LOCAL for f in dev_findings)

        rel_passed, rel_findings = checker.run([tmp_path], verbose=False,
                                               scope=checker.SCOPE_RELEASE)
        assert rel_passed is False
        assert any(f.severity == checker.CRITICAL for f in rel_findings)
        assert "sensitive-file" in _rules(rel_findings)

    def test_detects_edge_profile(self, tmp_path):
        """dev scope：本机 Profile 归 LOCAL（不阻断，但必须可见）；release scope 下 CRITICAL。"""
        profile = tmp_path / "runtime" / "edge_profile"
        _write(profile / "Local State", "{}")
        _write(profile / "Default" / "Preferences", "{}")

        dev_passed, dev_findings = checker.run([tmp_path], verbose=False,
                                               scope=checker.SCOPE_DEV)
        assert dev_passed is True
        assert any(f.severity == checker.LOCAL for f in dev_findings)

        rel_passed, rel_findings = checker.run([tmp_path], verbose=False,
                                               scope=checker.SCOPE_RELEASE)
        assert rel_passed is False
        assert "edge-profile" in _rules(rel_findings)

    def test_empty_edge_profile_placeholder_is_not_blocking(self, tmp_path):
        """空占位目录（运行时重建）不算违规。"""
        placeholder = tmp_path / "runtime" / "edge_profile"
        _write(placeholder / checker.PLACEHOLDER_NAME, "运行时自动创建")
        passed, findings = checker.run([tmp_path], verbose=False,
                                       scope=checker.SCOPE_RELEASE)
        assert passed is True
        assert any(f.severity == checker.LOW for f in findings)

    def test_sensitive_file_in_dist_is_fatal(self, tmp_path):
        """dist/ 是发布产物目录：即使 dev scope 也按 release 判定（不降级）。"""
        _write(tmp_path / "dist" / "data" / "tju_storage_state.json",
               {"cookies": []})
        passed, findings = _scan(tmp_path)
        assert passed is False
        assert "sensitive-file" in _rules(findings)
        hit = [f for f in findings if f.rule == "sensitive-file"]
        assert hit and hit[0].severity == checker.CRITICAL


class TestReporting:
    def test_finding_reports_relative_path_with_root_name(self, tmp_path):
        """路径标签带扫描根名（真实仓库扫描时即 data/library.json）。"""
        _write(tmp_path / "data" / "library.json",
               {"records": [{"title": "Test paper"}]})
        _, findings = _scan(tmp_path)
        labels = {str(f.path).replace("\\", "/") for f in findings}
        assert any(label.endswith("data/library.json") for label in labels), labels
        assert all(tmp_path.name in label for label in labels), labels

    def test_real_tree_labels_are_root_prefixed(self):
        """真实仓库扫描：data/library.json 形式（不含仓库绝对路径前缀）。"""
        from tju_info_retrieval import app_paths

        root = app_paths.project_root()
        _, findings = checker.run([root / "data"], verbose=False)
        if not findings:
            pytest.skip("data/ 当前无问题项")
        labels = {str(f.path).replace("\\", "/") for f in findings}
        assert all(label.startswith("data/") for label in labels), labels

    def test_api_key_value_is_redacted(self, tmp_path, capsys):
        _write(tmp_path / "runtime" / "cfg.json",
               {"api_key": "sk-abcdefghijklmnopqrstuvwxyz012345"})
        code = checker.main(["--root", str(tmp_path)])
        out = capsys.readouterr().out
        assert code == 1
        assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in out
        assert "***" in out

    def test_json_output(self, tmp_path, capsys):
        _write(tmp_path / "data" / "library.json",
               {"records": [{"title": "Test paper"}]})
        code = checker.main(["--root", str(tmp_path), "--json"])
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert code == 1
        assert payload["passed"] is False
        assert payload["findings"]

    def test_clean_tree_returns_zero(self, tmp_path, capsys):
        _write(tmp_path / "data" / "library.json",
               {"records": [{"title": "真实论文"}]})
        code = checker.main(["--root", str(tmp_path)])
        out = capsys.readouterr().out
        assert code == 0
        assert "PASS" in out

    def test_exclude_pattern(self, tmp_path):
        """排除模式可用（根相对路径写法，不依赖临时目录名）。"""
        _write(tmp_path / "runtime" / "notes_latest.json",
               {"note": "验收测试备注"})
        passed_dirty, _ = checker.run([tmp_path], verbose=False)
        assert passed_dirty is False
        passed_clean, findings = checker.run(
            [tmp_path], verbose=False, exclude=("runtime/notes_latest.json",))
        assert passed_clean is True, findings

    def test_dev_artifact_glob_is_local_not_blocking(self, tmp_path):
        """开发态调试/验收产物（runtime/v0.* 等）在 dev scope 归 LOCAL。"""
        _write(tmp_path / "runtime" / "v0.18_dev_probe.json",
               {"note": "验收测试备注"})
        passed, findings = checker.run([tmp_path], verbose=False,
                                       scope=checker.SCOPE_DEV)
        assert passed is True
        assert any(f.severity == checker.LOCAL for f in findings)
        rel_passed, _ = checker.run([tmp_path], verbose=False,
                                    scope=checker.SCOPE_RELEASE)
        assert rel_passed is False


class TestRealTreeScan:
    def test_scanner_runs_on_real_tree(self):
        """工具可在本仓库真实目录上运行（不做 PASS/FAIL 断言，只保证可执行）。"""
        from tju_info_retrieval import app_paths

        root = app_paths.project_root()
        bases = [root / name for name in checker.DEFAULT_SCAN_DIRS]
        passed, findings = checker.run(bases, verbose=False)
        assert isinstance(passed, bool)
        assert isinstance(findings, list)
        for finding in findings:
            assert finding.rule
            assert finding.severity in ("CRITICAL", "HIGH", "LOW", "LOCAL")


# ============================================================
# v0.18 Phase 2.9-B.2-E：ZIP / 发布 runtime / 源码快照
# ============================================================


def _make_zip(path: Path, members: dict) -> Path:
    import zipfile

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


class TestZipScan:
    def test_clean_zip_passes(self, tmp_path):
        archive = _make_zip(tmp_path / "dist" / "rc.zip", {
            "runtime/README_release.txt": "发布说明，无用户数据",
            "runtime/summary_provider.example.json": '{"enabled": false}',
        })
        passed, findings = checker.run([], verbose=False, scope=checker.SCOPE_RELEASE,
                                       zips=[archive])
        assert passed is True, findings

    def test_zip_with_storage_state_is_critical(self, tmp_path):
        archive = _make_zip(tmp_path / "dist" / "rc.zip", {
            "runtime/tju_storage_state.json": '{"cookies": [], "origins": []}',
        })
        passed, findings = checker.run([], verbose=False, scope=checker.SCOPE_RELEASE,
                                       zips=[archive])
        assert passed is False
        assert "sensitive-file" in _rules(findings)
        assert any(f.severity == checker.CRITICAL for f in findings)

    def test_zip_with_edge_profile_is_critical(self, tmp_path):
        archive = _make_zip(tmp_path / "dist" / "rc.zip", {
            "runtime/edge_profile/Local State": "{}",
            "runtime/edge_profile/Default/Login Data": "binary-ish",
        })
        passed, findings = checker.run([], verbose=False, scope=checker.SCOPE_RELEASE,
                                       zips=[archive])
        assert passed is False
        assert "edge-profile" in _rules(findings)

    def test_zip_with_test_titles_is_high(self, tmp_path):
        archive = _make_zip(tmp_path / "dist" / "rc.zip", {
            "data/library.json": '{"records": [{"title": "Test paper"}]}',
        })
        passed, findings = checker.run([], verbose=False, scope=checker.SCOPE_RELEASE,
                                       zips=[archive])
        assert passed is False
        assert any(f.rule == "test-title" and f.severity == checker.HIGH
                   for f in findings)
        assert all(str(f.path).startswith("rc.zip!") for f in findings)

    def test_zip_with_api_key_is_critical_redacted(self, tmp_path, capsys):
        archive = _make_zip(tmp_path / "dist" / "rc.zip", {
            "runtime/cfg.json": '{"api_key": "sk-abcdefghijklmnopqrstuvwx"}',
        })
        code = checker.main(["--scope", "release", "--zip", str(archive),
                             "--root", str(tmp_path / "none")])
        out = capsys.readouterr().out
        assert code == 1
        assert "sk-abcdefghijklmnopqrstuvwx" not in out
        assert "***" in out

    def test_zip_placeholder_dir_is_not_blocking(self, tmp_path):
        archive = _make_zip(tmp_path / "dist" / "rc.zip", {
            f"runtime/edge_profile/{checker.PLACEHOLDER_NAME}": "运行时创建",
        })
        passed, findings = checker.run([], verbose=False, scope=checker.SCOPE_RELEASE,
                                       zips=[archive])
        assert passed is True, findings

    def test_exclude_applies_to_zip_members(self, tmp_path):
        archive = _make_zip(tmp_path / "dist" / "rc.zip", {
            "runtime/v0.18_dev.json": '{"note": "验收测试"}'})
        dirty, _ = checker.run([], verbose=False, scope=checker.SCOPE_RELEASE,
                               zips=[archive])
        assert dirty is False
        clean, findings = checker.run(
            [], verbose=False, scope=checker.SCOPE_RELEASE, zips=[archive],
            exclude=("runtime/v0.18_*",))
        assert clean is True, findings

    def test_missing_zip_is_skipped_not_fatal(self, tmp_path, capsys):
        """不存在的压缩包计入"跳过"，不产生误报。"""
        missing = tmp_path / "nope.zip"
        passed, findings = checker.run(
            [], verbose=False, scope=checker.SCOPE_RELEASE, zips=[missing])
        assert passed is True
        assert not any(str(missing) in str(f.path) for f in findings)

        code = checker.main(["--scope", "release", "--zip", str(missing),
                             "--root", str(tmp_path / "none")])
        out = capsys.readouterr().out
        assert code == 0
        assert "跳过" in out

    def test_bad_zip_is_reported(self, tmp_path):
        bad = tmp_path / "dist" / "bad.zip"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("not a zip", encoding="utf-8")
        passed, findings = checker.run([], verbose=False,
                                       scope=checker.SCOPE_RELEASE, zips=[bad])
        assert any(f.rule == "bad-zip" for f in findings)


class TestReleaseRuntimeScan:
    def test_clean_release_runtime_passes(self, tmp_path):
        runtime = tmp_path / "dist" / "runtime"
        _write(runtime / "README_release.txt", "发布说明")
        _write(runtime / "summary_provider.example.json", {"enabled": False})
        for name in ("cache", "logs", "edge_profile", "profile"):
            _write(runtime / name / checker.PLACEHOLDER_NAME, "运行时创建")
        passed, findings = checker.run([], verbose=False,
                                       scope=checker.SCOPE_RELEASE,
                                       release_runtimes=[runtime])
        assert passed is True, findings

    def test_release_runtime_with_profile_is_critical(self, tmp_path):
        runtime = tmp_path / "dist" / "runtime"
        _write(runtime / "edge_profile" / "Local State", "{}")
        passed, findings = checker.run([], verbose=False,
                                       scope=checker.SCOPE_RELEASE,
                                       release_runtimes=[runtime])
        assert passed is False
        assert any(f.rule == "release-runtime-dir" for f in findings)

    def test_release_runtime_with_cache_content_is_critical(self, tmp_path):
        runtime = tmp_path / "dist" / "runtime"
        _write(runtime / "cache" / "enhanced_summary" / "a.json",
               {"summary": {"provider": "openai-compatible"}})
        passed, findings = checker.run([], verbose=False,
                                       scope=checker.SCOPE_RELEASE,
                                       release_runtimes=[runtime])
        assert passed is False
        assert any(f.rule == "release-runtime-dir" for f in findings)


class TestSourceSnapshotScan:
    def test_clean_snapshot_passes(self, tmp_path):
        snapshot = tmp_path / "out" / "source_snapshot"
        _write(snapshot / "src" / "main.py", "print('hello')")
        _write(snapshot / "README.md", "# 项目说明")
        passed, findings = checker.run([], verbose=False,
                                       scope=checker.SCOPE_RELEASE,
                                       source_snapshots=[snapshot])
        assert passed is True, findings

    def test_snapshot_with_user_data_is_critical(self, tmp_path):
        snapshot = tmp_path / "out" / "source_snapshot"
        _write(snapshot / "runtime" / "edge_profile" / "Local State", "{}")
        passed, findings = checker.run([], verbose=False,
                                       scope=checker.SCOPE_RELEASE,
                                       source_snapshots=[snapshot])
        assert passed is False
        assert "snapshot-user-data" in _rules(findings)

    def test_snapshot_with_reports_is_critical(self, tmp_path):
        """调研报告属用户数据，源码快照不得包含。"""
        snapshot = tmp_path / "out" / "source_snapshot"
        _write(snapshot / "runtime" / "reports" / "abc.json",
               {"title": "调研报告"})
        passed, findings = checker.run([], verbose=False,
                                       scope=checker.SCOPE_RELEASE,
                                       source_snapshots=[snapshot])
        assert passed is False
        assert "snapshot-user-data" in _rules(findings)

    def test_snapshot_with_library_is_critical(self, tmp_path):
        snapshot = tmp_path / "out" / "source_snapshot"
        _write(snapshot / "data" / "library.json", {"records": []})
        passed, findings = checker.run([], verbose=False,
                                       scope=checker.SCOPE_RELEASE,
                                       source_snapshots=[snapshot])
        assert passed is False
        assert "snapshot-user-data" in _rules(findings)
