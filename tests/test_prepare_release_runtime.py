#!/usr/bin/env python3
"""正式测试：发布用 runtime 生成器（v0.18 Phase 2.9-B.2-B）。

覆盖：
- 默认 dry-run 不写任何文件；
- 白名单复制：只带配置模板与 README，不带开发探针/审计产物；
- 排除 Profile / 登录态 / 缓存 / 日志 / 凭据；
- 生成 README_release.txt 与空目录占位说明；
- 生成的 runtime 通过 release scope 扫描（0 CRITICAL）；
- 源目录只读（不被修改）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import check_release_data as checker
from tools import prepare_release_runtime as prep


@pytest.fixture
def fake_runtime(tmp_path) -> Path:
    """构造一个"像真实 runtime 一样脏"的源目录。"""
    source = tmp_path / "runtime"
    source.mkdir(parents=True, exist_ok=True)
    # 配置模板（应保留）
    (source / "summary_provider.example.json").write_text(
        json.dumps({"enabled": False, "base_url": "", "model": ""},
                   ensure_ascii=False), encoding="utf-8")
    (source / "README.md").write_text("# runtime 说明\n", encoding="utf-8")
    # 登录态 / Profile（必须排除）
    profile = source / "edge_profile"
    (profile / "Default").mkdir(parents=True)
    (profile / "Local State").write_text("{}", encoding="utf-8")
    (profile / "Default" / "Login Data").write_text("binary", encoding="utf-8")
    (source / "tju_storage_state.json").write_text(
        json.dumps({"cookies": [{"name": "SESSION", "value": "x"}]}),
        encoding="utf-8")
    (source / "cache" / "enhanced_summary").mkdir(parents=True)
    (source / "cache" / "enhanced_summary" / "abc.json").write_text(
        json.dumps({"summary": {"provider": "openai-compatible"}}),
        encoding="utf-8")
    (source / "logs").mkdir()
    (source / "logs" / "run.log").write_text("log line\n", encoding="utf-8")
    (source / "credential.dat").write_text("secret-bytes", encoding="utf-8")
    # 调研报告（用户数据，不应保留）
    (source / "reports").mkdir()
    (source / "reports" / "abc.json").write_text(
        json.dumps({"title": "调研报告"}), encoding="utf-8")
    # 开发探针产物（不应保留）
    (source / "probe_tju_llm.py").write_text("print('probe')\n", encoding="utf-8")
    (source / "audit_resources.json").write_text("{}", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "x.pyc").write_bytes(b"\x00\x01")
    return source


def _run(source: Path, dest: Path, *extra: str) -> int:
    return prep.main(["--source", str(source), "--dest", str(dest), *extra])


class TestDryRun:
    def test_dry_run_writes_nothing(self, fake_runtime, tmp_path):
        dest = tmp_path / "out" / "runtime"
        before = sorted(p.name for p in fake_runtime.rglob("*"))
        assert _run(fake_runtime, dest, "--dry-run") == 0
        assert not dest.exists()
        assert sorted(p.name for p in fake_runtime.rglob("*")) == before

    def test_default_is_dry_run(self, fake_runtime, tmp_path, capsys):
        dest = tmp_path / "out" / "runtime"
        assert _run(fake_runtime, dest) == 0
        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        assert not dest.exists()

    def test_dry_run_lists_kept_and_excluded(self, fake_runtime, tmp_path, capsys):
        _run(fake_runtime, tmp_path / "out" / "runtime", "--dry-run")
        out = capsys.readouterr().out
        assert "summary_provider.example.json" in out
        assert "profile:" in out
        assert "storage_state:" in out
        assert "cache:" in out
        assert "logs:" in out
        assert "credentials:" in out
        assert "dev-artifact:" in out


class TestExecute:
    def test_execute_copies_only_templates(self, fake_runtime, tmp_path):
        dest = tmp_path / "out" / "runtime"
        assert _run(fake_runtime, dest, "--execute") == 0
        files = {p.relative_to(dest).as_posix()
                 for p in dest.rglob("*") if p.is_file()}
        assert "summary_provider.example.json" in files
        assert "README_release.txt" in files
        # 开发探针 / 审计产物不复制
        assert "probe_tju_llm.py" not in files
        assert "audit_resources.json" not in files
        assert not any(f.endswith(".pyc") for f in files)

    def test_execute_excludes_secrets(self, fake_runtime, tmp_path):
        dest = tmp_path / "out" / "runtime"
        _run(fake_runtime, dest, "--execute")
        names = {p.name for p in dest.rglob("*")}
        assert "tju_storage_state.json" not in names
        assert "credential.dat" not in names
        assert "run.log" not in names
        assert "abc.json" not in names
        assert not (dest / "reports").exists() or all(
            p.name == "PUT_USER_DATA_HERE.txt"
            for p in (dest / "reports").rglob("*") if p.is_file())
        assert not any(p.name == "Local State" for p in dest.rglob("*"))
        assert not any(p.name == "Login Data" for p in dest.rglob("*"))

    def test_execute_creates_empty_dir_placeholders(self, fake_runtime, tmp_path):
        dest = tmp_path / "out" / "runtime"
        _run(fake_runtime, dest, "--execute")
        for name in ("cache", "logs", "edge_profile", "profile"):
            marker = dest / name / "PUT_USER_DATA_HERE.txt"
            assert marker.is_file(), name
            real = [p for p in (dest / name).rglob("*")
                    if p.is_file() and p.name != "PUT_USER_DATA_HERE.txt"]
            assert real == [], f"{name} 不应含实际文件"

    def test_readme_generated_with_guidance(self, fake_runtime, tmp_path):
        dest = tmp_path / "out" / "runtime"
        _run(fake_runtime, dest, "--execute")
        readme = (dest / "README_release.txt").read_text(encoding="utf-8")
        assert "自动创建" in readme
        assert "LOCALAPPDATA" in readme
        assert "不包含任何用户数据" in readme

    def test_source_not_modified(self, fake_runtime, tmp_path):
        snapshot = {p: p.read_bytes() for p in fake_runtime.rglob("*")
                    if p.is_file()}
        dest = tmp_path / "out" / "runtime"
        _run(fake_runtime, dest, "--execute")
        after = {p: p.read_bytes() for p in fake_runtime.rglob("*")
                 if p.is_file()}
        assert snapshot == after

    def test_existing_dest_requires_force(self, fake_runtime, tmp_path):
        dest = tmp_path / "out" / "runtime"
        dest.mkdir(parents=True)
        assert _run(fake_runtime, dest, "--execute") == 1     # 拒绝覆盖
        assert _run(fake_runtime, dest, "--execute", "--force") == 0

    def test_missing_source_returns_error(self, tmp_path):
        assert prep.main(["--source", str(tmp_path / "none"),
                          "--dest", str(tmp_path / "out"),
                          "--execute"]) == 1


class TestGeneratedRuntimeIsReleaseClean:
    def test_generated_runtime_passes_release_scan(self, fake_runtime, tmp_path):
        dest = tmp_path / "out" / "runtime"
        _run(fake_runtime, dest, "--execute")
        passed, findings = checker.run([], verbose=False,
                                       scope=checker.SCOPE_RELEASE,
                                       release_runtimes=[dest])
        blocking = [f for f in findings
                    if f.severity in checker.BLOCKING_SEVERITIES]
        assert passed is True, blocking

    def test_generated_runtime_excluded_from_zip_cleanliness(
            self, fake_runtime, tmp_path):
        """把生成结果打成 zip 后仍应通过（模拟发布打包）。"""
        import zipfile

        dest = tmp_path / "out" / "runtime"
        _run(fake_runtime, dest, "--execute")
        archive = tmp_path / "dist" / "rc.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w") as zf:
            for path in dest.rglob("*"):
                if path.is_file():
                    zf.write(path, f"runtime/{path.relative_to(dest)}")
        passed, findings = checker.run([], verbose=False,
                                       scope=checker.SCOPE_RELEASE,
                                       zips=[archive])
        blocking = [f for f in findings
                    if f.severity in checker.BLOCKING_SEVERITIES]
        assert passed is True, blocking

    def test_dirty_source_would_fail_release_scan(self, fake_runtime, tmp_path):
        """反证：不做排除直接把源 runtime 当发布产物 → 必须 FAIL。"""
        passed, findings = checker.run([], verbose=False,
                                       scope=checker.SCOPE_RELEASE,
                                       release_runtimes=[fake_runtime])
        assert passed is False
        assert any(f.severity == checker.CRITICAL for f in findings)
