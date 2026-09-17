#!/usr/bin/env python3
"""发布前数据安全检查（v0.18 Phase 2.9-B.2-E，Release Guard v2）。

检查 6 类问题：

1. 测试标题残留（Test paper / Deep THz study 等 fixture 标题）；
2. 测试标记（标签 / 备注 / 说明里的"验收测试、测试备注…"）；
3. mock provider 数据（provider=mock、未配置占位）；
4. 测试 API key（sk-*、test-key、api_key 明文值等）；
5. cookie / token / 登录态（Cookie、Authorization、Bearer、storage_state）；
6. 浏览器 Profile（edge_profile / Local State / Login Data）。

## 严重度分层

| 级别 | 含义 | 是否导致 FAIL |
|---|---|---|
| CRITICAL | 凭据/登录态/密钥进入发布产物 | 是 |
| HIGH | 测试论文残留、mock 数据 | 是 |
| LOW | 文档/说明中的测试文字 | 否（--fail-on-warn 时算） |
| LOCAL | **开发机本地数据**（Edge Profile、登录态、日志、调试/验收产物）：发布包不应包含，但开发机必须保留 | 否（仅 dev scope；release scope 下同类内容升级为 CRITICAL） |

## 作用域

- `--scope dev`（默认）：扫描开发工作区（`data/`、`runtime/`、`dist/`）。
  本地登录态/调试产物归类为 LOCAL，不阻断；用于日常自查。
- `--scope release`：扫描**发布产物**（release runtime 目录、源码快照、ZIP）。
  同类内容一律 CRITICAL —— 用于发布放行判定。

## 能力

1. **目录扫描**：`--root DIR`（可多次）；
2. **ZIP 扫描**：`--zip FILE.zip`（可多次；检查压缩包内条目名与文本内容）；
3. **源码快照检查**：`--source-snapshot DIR`（快照内不得含任何用户运行时数据）；
4. **release runtime 检查**：`--release-runtime DIR`（发布用 runtime 目录）。

输出 PASS / FAIL，`--json` 供流水线消费。

用法：
    python tools/check_release_data.py
    python tools/check_release_data.py --scope release --release-runtime dist/runtime
    python tools/check_release_data.py --scope release --zip dist/v0.18.zip
    python tools/check_release_data.py --scope release --source-snapshot out/source_snapshot
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DEFAULT_SCAN_DIRS = ("data", "runtime", "dist")

SCOPE_DEV = "dev"
SCOPE_RELEASE = "release"

# 严重度
CRITICAL = "CRITICAL"
HIGH = "HIGH"
LOW = "LOW"
LOCAL = "LOCAL"
BLOCKING_SEVERITIES = (CRITICAL, HIGH)

# 只扫文本类数据文件（代码里的常量名不算数据泄漏）
TEXT_SUFFIXES = (".json", ".jsonl", ".csv", ".txt", ".log", ".md", ".yaml", ".yml")
MAX_TEXT_BYTES = 4 * 1024 * 1024

# 1. 测试 fixture 标题（精确词，避免误伤真实论文）
TEST_TITLES = ("test paper", "deep thz study", "test title", "sample paper")
# 2. 测试标记（标签 / 备注 / 说明）
TEST_MARKERS = ("验收测试", "测试备注", "test note", "testmarker", "测试标签",
                "debug tag", "mock 数据")
# 3. mock provider
MOCK_PATTERNS = (
    (re.compile(r'"provider"\s*:\s*"mock"', re.I), "provider=mock"),
    (re.compile(r"\bmock_provider\b", re.I), "mock_provider"),
    (re.compile(r"增强分析服务未配置"), "未配置占位（不该随包发布）"),
)
# 4. 测试 API key / 明文密钥字段
API_KEY_PATTERNS = (
    (re.compile(r"sk-[A-Za-z0-9]{12,}"), "疑似真实 API Key（sk- 前缀）"),
    (re.compile(r'"(?:api_key|apikey|api_secret)"\s*:\s*"[^"]{6,}"',
                re.I), "明文密钥字段（api_key 有实际取值）"),
    (re.compile(r"\b(?:test|fake|dummy|example|invalid)-key\b", re.I),
     "测试用 key"),
    (re.compile(r"TJU_INFO_LLM_API_KEY\s*[:=]\s*\S+"), "环境变量被赋了具体值"),
)
# 5. cookie / token / 登录态内容
SECRET_PATTERNS = (
    (re.compile(r'"cookies"\s*:\s*\[', re.I), "Cookie 内容"),
    (re.compile(r"authorization\s*[:=]", re.I), "认证头字段"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer 令牌"),
    (re.compile(r'"(?:access|refresh|id)_token"\s*:\s*"[^"]{8,}"', re.I),
     "令牌字段"),
    (re.compile(r'"origins"\s*:\s*\[', re.I), "浏览器 storage_state"),
)
# 敏感文件名（发布产物中一律 CRITICAL）
SENSITIVE_FILE_NAMES = ("tju_storage_state.json", "credential.dat", "secret.json",
                        ".env", "cookies.txt", "login data", "key.txt")
# Edge profile 目录标志文件
EDGE_PROFILE_MARKERS = ("Local State", "Default/Preferences", "Default/Cookies")
# 发布产物中禁止出现的目录名
FORBIDDEN_DIR_NAMES = ("edge_profile", "logs", "browser_profile")
# 空目录占位文件名（运行时重建；不含数据，不算违规）
PLACEHOLDER_NAME = "PUT_USER_DATA_HERE.txt"

# 开发机本地数据（dev scope 下归类为 LOCAL：开发机必须保留，但不得进发布包）
DEV_LOCAL_GLOBS = (
    "*/edge_profile/*", "*/edge_profile",
    "*/tju_storage_state.json",
    "*/credential.dat",
    "*/logs/*", "*/logs",
    # 开发态调试 / 验收产物（不属于发布内容）
    "runtime/v0.*",
    "runtime/*test*.txt", "runtime/*fav*.txt", "runtime/debug*",
    "runtime/full_*", "runtime/pytest_*", "runtime/*traceback*",
    "runtime/audit*", "runtime/dogfood*",
    # 清理前的可追溯备份（含历史测试数据，仅用于回滚，绝不进发布包）
    "data/library.backup_before_*.json",
    "library.backup_before_*.json",
    "*/library.json.bak-*",
    "library.json.bak-*",
)


def _is_dev_local(*paths: str) -> bool:
    """是否属于"开发机本地数据"（dev scope 下不阻断）。

    可传入"扫描根相对路径"（data/ 作为根时是 `library.json`）与
    "带根名标签"（`data/library.json`）两种形式，任一命中即算本地数据。

    `dist/` 是发布产物目录：其中内容一律按 release 判定，不做降级，
    否则"把登录态误打进 dist" 这类真实事故会被 dev scope 静默放过。
    """
    candidates = [p for p in paths if p]
    if not candidates:
        return False
    for cand in candidates:
        parts = cand.split("/")
        if parts and parts[0] == "dist":
            return False
    for cand in candidates:
        name = cand.rsplit("/", 1)[-1]
        for pat in DEV_LOCAL_GLOBS:
            stripped = pat.lstrip("*/")
            if fnmatch(cand, pat) or fnmatch(name, stripped) or fnmatch(cand, stripped):
                return True
    return False


class Finding:
    __slots__ = ("rule", "path", "detail", "severity")

    def __init__(self, rule: str, path, detail: str,
                 severity: str = CRITICAL) -> None:
        self.rule = rule
        self.path = path
        self.detail = detail
        self.severity = severity

    def as_dict(self) -> dict:
        return {"rule": self.rule, "path": str(self.path),
                "detail": self.detail, "severity": self.severity}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Finding {self.severity} {self.rule} {self.path}>"


def _redact(text: str, limit: int = 60) -> str:
    """截断并遮蔽疑似密钥长串（报告不复制敏感值）。"""
    text = re.sub(r"(sk-[A-Za-z0-9]{4})[A-Za-z0-9]+", r"\1***", text)
    text = re.sub(r"(Bearer\s+[A-Za-z0-9._\-]{4})[A-Za-z0-9._\-]+", r"\1***", text)
    text = re.sub(
        r'("(?:api_key|apikey|api_secret|access_token|refresh_token)"\s*:\s*")[^"]+',
        r"\1***", text, flags=re.I)
    return text[:limit]


def _label(path, base: Path) -> str:
    try:
        return (Path(base.name) / Path(path).relative_to(base)).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _excluded(rel_posix: str, patterns: tuple[str, ...],
              label: str | None = None) -> bool:
    """排除匹配：候选形式为"扫描根相对路径"、"带根名标签"、文件名。"""
    if not patterns:
        return False
    name = rel_posix.rsplit("/", 1)[-1]
    candidates = [rel_posix, name]
    if label:
        candidates.append(label)
    return any(fnmatch(cand, pat) for pat in patterns for cand in candidates)


# ---------------- 内容检查 ----------------

def scan_text_content(text: str, label: str, scope: str,
                      rel_path: str | None = None) -> list[Finding]:
    """按内容规则扫描一段文本。severity 由 scope 决定。

    label：报告用路径（带扫描根名）；rel_path：根相对路径（dev-local 判定用）。
    """
    findings: list[Finding] = []
    lowered = text.lower()
    secret_sev = CRITICAL
    test_sev = HIGH
    local = scope == SCOPE_DEV and _is_dev_local(
        rel_path or "", label)

    # 1. 测试标题
    for title in TEST_TITLES:
        if title in lowered:
            findings.append(Finding(
                "test-title", label, f"含测试 fixture 标题: {title}",
                LOCAL if local else test_sev))
    # 2. 测试标记
    for marker in TEST_MARKERS:
        if marker.lower() in lowered:
            findings.append(Finding(
                "test-marker", label, f"含测试标记: {marker}",
                LOCAL if local else test_sev))
    # 3. mock provider
    for pattern, label_text in MOCK_PATTERNS:
        if pattern.search(text):
            findings.append(Finding(
                "mock-provider", label, f"mock/占位数据: {label_text}",
                LOCAL if local else test_sev))
    # 4. 测试 API key
    for pattern, label_text in API_KEY_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(Finding(
                "test-api-key", label,
                f"{label_text} → {_redact(match.group(0))}",
                LOCAL if local else secret_sev))
    # 5. cookie / token / 登录态
    for pattern, label_text in SECRET_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(Finding(
                "secret-content", label,
                f"{label_text} → {_redact(match.group(0))}",
                LOCAL if local else secret_sev))
    return findings


def scan_text_file(path: Path, base: Path, scope: str) -> list[Finding]:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return []
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return [Finding("large-file-skipped", _label(path, base),
                            "文件过大，未做内容扫描", LOW)]
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return [Finding("unreadable", _label(path, base), str(exc), LOW)]
    return scan_text_content(raw, _label(path, base), scope,
                             path.relative_to(base).as_posix())


def scan_sensitive_paths(base: Path, scope: str) -> list[Finding]:
    """Edge profile / 登录态 / 敏感文件名检测。"""
    findings: list[Finding] = []

    for profile_dir in base.rglob("edge_profile"):
        if not profile_dir.is_dir():
            continue
        label = _label(profile_dir, base)
        rel_path = profile_dir.relative_to(base).as_posix()
        markers = [m for m in EDGE_PROFILE_MARKERS
                   if (profile_dir / m).exists()]
        real_files = [p for p in profile_dir.rglob("*")
                      if p.is_file() and p.name != PLACEHOLDER_NAME]
        if not markers and not real_files:
            # 空占位目录（运行时重建），不阻断发布
            findings.append(Finding(
                "edge-profile-placeholder", label,
                "空的 edge_profile 占位目录（运行时自动重建）", LOW))
            continue
        severity = LOCAL if (scope == SCOPE_DEV
                             and _is_dev_local(rel_path, label)) else CRITICAL
        findings.append(Finding(
            "edge-profile", label,
            "发现 Edge Profile 目录"
            + (f"（含 {', '.join(markers)}）" if markers
               else f"（含 {len(real_files)} 个文件）"),
            severity))

    for name in SENSITIVE_FILE_NAMES:
        for path in base.rglob(name):
            if not path.is_file():
                continue
            label = _label(path, base)
            rel_path = path.relative_to(base).as_posix()
            severity = LOCAL if (scope == SCOPE_DEV
                                 and _is_dev_local(rel_path, label)) else CRITICAL
            findings.append(Finding(
                "sensitive-file", label, f"敏感文件名: {name}", severity))
    return findings


def scan_root(base: Path, scope: str = SCOPE_DEV,
              exclude: tuple[str, ...] = ()) -> list[Finding]:
    findings: list[Finding] = []
    if not base.is_dir():
        return findings
    findings.extend(scan_sensitive_paths(base, scope))
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(base).as_posix()
        rel = _label(path, base)
        if _excluded(rel_path, exclude, rel):
            continue
        if path.name.lower() in SENSITIVE_FILE_NAMES:
            continue  # 已由 scan_sensitive_paths 报出
        if "edge_profile" in {p.name for p in path.parents}:
            continue  # Profile 内部逐文件内容不展开
        findings.extend(scan_text_file(path, base, scope))
    return findings


# ---------------- ZIP 扫描 ----------------

def scan_zip(archive: Path, scope: str = SCOPE_RELEASE,
             exclude: tuple[str, ...] = ()) -> list[Finding]:
    """扫描压缩包：条目名（敏感文件 / Profile）+ 文本内容。"""
    findings: list[Finding] = []
    if not archive.is_file():
        return [Finding("zip-missing", str(archive), "压缩包不存在", LOW)]
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                member = info.filename.replace("\\", "/")
                label = f"{archive.name}!{member}"
                if _excluded(member, exclude):
                    continue
                lowered = member.lower()
                parts = lowered.split("/")
                name = parts[-1]

                # 空目录占位说明（运行时重建）不含数据，不算违规
                if name == PLACEHOLDER_NAME.lower():
                    continue
                if name in SENSITIVE_FILE_NAMES:
                    findings.append(Finding(
                        "sensitive-file", label,
                        f"压缩包内敏感文件: {name}", CRITICAL))
                if "edge_profile" in parts or "browser_profile" in parts:
                    findings.append(Finding(
                        "edge-profile", label,
                        "压缩包内含 Edge Profile 内容", CRITICAL))
                if any(part in parts for part in FORBIDDEN_DIR_NAMES):
                    findings.append(Finding(
                        "forbidden-dir", label,
                        "压缩包内含禁止目录内容", CRITICAL))
                if Path(name).suffix.lower() not in TEXT_SUFFIXES:
                    continue
                if info.file_size > MAX_TEXT_BYTES:
                    findings.append(Finding(
                        "large-file-skipped", label,
                        "压缩包内文件过大，未做内容扫描", LOW))
                    continue
                try:
                    raw = zf.read(info).decode("utf-8", errors="ignore")
                except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                    findings.append(Finding(
                        "unreadable", label, f"压缩包读取失败: {exc}", LOW))
                    continue
                findings.extend(scan_text_content(raw, label, scope))
    except zipfile.BadZipFile as exc:
        findings.append(Finding("bad-zip", str(archive),
                                f"非法压缩包: {exc}", LOW))
    return findings


# ---------------- 发布产物检查 ----------------

def scan_release_runtime(base: Path, scope: str = SCOPE_RELEASE,
                         exclude: tuple[str, ...] = ()) -> list[Finding]:
    """发布用 runtime 目录：只允许配置模板 + README，不得含用户数据。"""
    findings = scan_root(base, scope, exclude)
    if base.is_dir():
        for forbidden in ("edge_profile", "logs", "cache", "profile"):
            target = base / forbidden
            if not target.is_dir():
                continue
            # 空目录 + 占位说明可接受（运行时重建）；有实际内容即 CRITICAL
            real_files = [p for p in target.rglob("*")
                          if p.is_file() and p.name != PLACEHOLDER_NAME]
            if real_files:
                findings.append(Finding(
                    "release-runtime-dir", _label(target, base),
                    f"发布 runtime 的 {forbidden}/ 含 {len(real_files)} 个实体文件"
                    "（应只保留空目录说明）", CRITICAL))
    return findings


def scan_source_snapshot(base: Path, scope: str = SCOPE_RELEASE,
                         exclude: tuple[str, ...] = ()) -> list[Finding]:
    """源码快照：不得含任何用户运行时数据。"""
    findings = scan_root(base, scope, exclude)
    if base.is_dir():
        for rel in ("runtime/edge_profile", "runtime/cache", "runtime/logs",
                    "data/library.json", "runtime/research_profile.json",
                    # 调研报告（v0.18 Phase 2.9-C）：用户数据，不得进源码快照/发布包
                    "runtime/reports"):
            if (base / rel).exists():
                findings.append(Finding(
                    "snapshot-user-data", _label(base / rel, base),
                    "源码快照不得包含用户运行时数据", CRITICAL))
    return findings


# ---------------- 汇总 ----------------

def run(scan_dirs: list[Path], verbose: bool = True,
        exclude: tuple[str, ...] = (), scope: str = SCOPE_DEV,
        zips: list[Path] | None = None,
        release_runtimes: list[Path] | None = None,
        source_snapshots: list[Path] | None = None) -> tuple[bool, list[Finding]]:
    findings: list[Finding] = []
    scanned: list[str] = []
    skipped: list[str] = []

    for target in scan_dirs:
        if not target.is_dir():
            skipped.append(str(target))
            continue
        scanned.append(str(target))
        findings.extend(scan_root(target, scope, exclude))
    for target in release_runtimes or []:
        if not target.is_dir():
            skipped.append(str(target))
            continue
        scanned.append(f"release-runtime:{target}")
        findings.extend(scan_release_runtime(target, scope, exclude))
    for target in source_snapshots or []:
        if not target.is_dir():
            skipped.append(str(target))
            continue
        scanned.append(f"source-snapshot:{target}")
        findings.extend(scan_source_snapshot(target, scope, exclude))
    for archive in zips or []:
        if not archive.is_file():
            skipped.append(str(archive))
            continue
        scanned.append(f"zip:{archive}")
        findings.extend(scan_zip(archive, scope, exclude))

    blocking = [f for f in findings if f.severity in BLOCKING_SEVERITIES]
    local = [f for f in findings if f.severity == LOCAL]
    low = [f for f in findings if f.severity == LOW]

    if verbose:
        print("发布数据安全检查（Release Guard v2）")
        print(f"  作用域: {scope}")
        print(f"  已扫描: {', '.join(scanned) if scanned else '（无）'}")
        if skipped:
            print(f"  跳过（不存在）: {', '.join(skipped)}")
        print()
        if findings:
            order = {CRITICAL: 0, HIGH: 1, LOW: 2, LOCAL: 3}
            for f in sorted(findings, key=lambda x: (order.get(x.severity, 9),
                                                     x.rule, str(x.path))):
                print(f"  [{f.severity}] {f.rule}: {f.path} — {f.detail}")
        else:
            print("未发现问题。")
        print()
        print(f"CRITICAL: {sum(1 for f in findings if f.severity == CRITICAL)}"
              f" ｜ HIGH: {sum(1 for f in findings if f.severity == HIGH)}"
              f" ｜ LOW: {len(low)} ｜ LOCAL: {len(local)}")
        print(f"RELEASE DATA CHECK = {'FAIL' if blocking else 'PASS'}")

    return (not blocking), findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布前数据安全检查 v2")
    parser.add_argument("--root", action="append", default=None,
                        help="扫描根目录（可多次；默认 data/runtime/dist）")
    parser.add_argument("--zip", action="append", default=None,
                        help="扫描压缩包（可多次）")
    parser.add_argument("--release-runtime", action="append", default=None,
                        help="发布用 runtime 目录（可多次）")
    parser.add_argument("--source-snapshot", action="append", default=None,
                        help="源码快照目录（可多次）")
    parser.add_argument("--scope", choices=(SCOPE_DEV, SCOPE_RELEASE),
                        default=SCOPE_DEV, help="作用域（默认 dev）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--fail-on-warn", action="store_true",
                        help="LOW 也算失败")
    parser.add_argument("--exclude", action="append", default=None,
                        help="排除的路径 glob（可多次）")
    args = parser.parse_args(argv)

    bases = ([Path(r) for r in args.root] if args.root
             else [ROOT / name for name in DEFAULT_SCAN_DIRS])
    zips = [Path(z) for z in (args.zip or [])]
    runtimes = [Path(r) for r in (args.release_runtime or [])]
    snapshots = [Path(s) for s in (args.source_snapshot or [])]
    exclude = tuple(args.exclude or ())

    if not args.json and exclude:
        print("排除模式: " + ", ".join(exclude))
        print()

    passed, findings = run(bases, verbose=not args.json, exclude=exclude,
                           scope=args.scope, zips=zips,
                           release_runtimes=runtimes,
                           source_snapshots=snapshots)
    if args.fail_on_warn and any(f.severity == LOW for f in findings):
        passed = False
    if args.json:
        print(json.dumps({
            "scope": args.scope,
            "root": [str(b) for b in bases],
            "zips": [str(z) for z in zips],
            "passed": passed,
            "findings": [f.as_dict() for f in findings],
        }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
