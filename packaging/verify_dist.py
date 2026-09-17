#!/usr/bin/env python3
"""验证 onedir/zip 中没有开发路径、会话数据库或明显凭据。"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

FORBIDDEN_NAMES = {
    "cookies", "cookies-journal", "login data", "login data-journal",
    "web data", "web data-journal", "storage_state.json",
    "tju_storage_state.json",
}
FORBIDDEN_PARTS = {"edge_profile", "runtime", "tests", "docs", ".git", ".pytest_cache"}
TEXT_SUFFIXES = {".txt", ".json", ".yaml", ".yml", ".ini", ".cfg", ".log", ".md"}
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(authorization|password|passwd|token|api[_-]?key)\s*[:=]\s*['\"]?[^\s'\"]{8,}"
)


def _path_errors(names: list[str]) -> list[str]:
    errors = []
    for name in names:
        parts = [part.casefold() for part in Path(name.replace("\\", "/")).parts]
        if any(part in FORBIDDEN_PARTS for part in parts):
            errors.append(f"forbidden path: {name}")
        if parts and parts[-1] in FORBIDDEN_NAMES:
            errors.append(f"forbidden browser/session file: {name}")
    return errors


def verify_directory(root: Path, development_root: str) -> list[str]:
    files = [path for path in root.rglob("*") if path.is_file()]
    errors = _path_errors([str(path.relative_to(root)) for path in files])
    needles = [development_root.encode("utf-8"), development_root.encode("utf-16le")]
    for path in files:
        data = path.read_bytes()
        if any(needle and needle in data for needle in needles):
            errors.append(f"development path embedded: {path.relative_to(root)}")
        if path.suffix.casefold() in TEXT_SUFFIXES:
            text = data.decode("utf-8", errors="ignore")
            if SECRET_ASSIGNMENT.search(text):
                errors.append(f"possible credential assignment: {path.relative_to(root)}")
    return errors


def verify_zip(path: Path, expected_root: str) -> list[str]:
    errors = []
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
    if not names or any(not name.replace("\\", "/").startswith(expected_root + "/")
                        for name in names):
        errors.append("zip entries are not contained by the expected root directory")
    errors.extend(_path_errors(names))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", type=Path)
    parser.add_argument("--development-root", required=True)
    parser.add_argument("--zip", type=Path)
    parser.add_argument("--expected-root", default="TJU_Info_Retrieval_v0.17-test2")
    args = parser.parse_args()
    errors = verify_directory(args.dist.resolve(), args.development_root)
    if args.zip:
        errors.extend(verify_zip(args.zip.resolve(), args.expected_root))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Sensitive data scan PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
