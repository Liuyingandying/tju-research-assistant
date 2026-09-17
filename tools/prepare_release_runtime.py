#!/usr/bin/env python3
"""生成"发布用 runtime"目录（v0.18 Phase 2.9-B.2-B）。

目的：发布包**绝不能**携带本机登录态与缓存。本工具以白名单方式复制
runtime，只保留"配置模板 + 说明"，其余（Profile / 登录态 / 缓存 / 日志）
一律排除，并在目标目录写入 `README_release.txt` 说明运行时行为。

排除（无论源目录里有没有，都不会被复制）：

- `edge_profile/`（浏览器 Profile：Cookie / Login Data / 缓存）
- `*storage_state*.json`（Playwright 门户认证状态）
- `cache/`、`enhanced_summary/`（AI 分析缓存）
- `logs/`、`*.log`
- `credential.dat` / `secret.json` / `.env`

保留（白名单）：

- `*.example.json` / `*_template.json`（配置模板）
- `README*` / `*.txt` 说明文件（可关掉）
- 目标目录会自动创建 `cache/`、`logs/`、`edge_profile/` **空目录**（运行时重建）

用法：
    python tools/prepare_release_runtime.py --dry-run            # 默认
    python tools/prepare_release_runtime.py --source runtime \
        --dest dist/runtime --execute

安全：默认 dry-run；`--execute` 前若目标目录已存在会要求 `--force`；
本工具**只读源目录**，绝不删除或修改本机真实数据。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

EXCLUDE_DIR_NAMES = ("edge_profile", "cache", "logs", "enhanced_summary",
                     "browser_profile", "Default",
                     # v0.18 Phase 2.9-C：调研报告是用户数据，绝不进发布包
                     "reports", "research_reports")
EXCLUDE_FILE_NAMES = ("credential.dat", "secret.json", ".env")
EXCLUDE_FILE_GLOBS = ("*storage_state*.json", "*.log", "storage_state*.json")

KEEP_FILE_GLOBS = ("*.example.json", "*_template.json", "*_example.json",
                   "README*")

README_NAME = "README_release.txt"
README_TEXT = """TJU_Info_Retrieval 发布用 runtime（自动生成，请勿手工修改）
生成时间：{timestamp}
生成工具：tools/prepare_release_runtime.py

本目录只包含配置模板与说明，**不包含任何用户数据**。
以下内容在本机运行时首次启动时自动创建：

  cache/                 AI 增强分析缓存（增强分析结果按需写入）
  logs/                  运行日志
  profile/               科研画像（%LOCALAPPDATA% 下的用户数据根，见下）
  edge_profile/          浏览器 Profile（登录后由浏览器生成）

用户数据位置（打包版）：
  %LOCALAPPDATA%\\TJU_Info_Retrieval\\
      cache\\library.json             我的论文库
      profile\\research_profile.json  科研画像
      cache\\tju_storage_state.json   门户登录态（用户登录后生成）
      cache\\enhanced_summary\\       AI 分析缓存

安全说明：
  * 发布包不含 Cookie / Login Data / 门户登录态 / API Key；
  * API Key 由用户在"设置"中填写，存储于 Windows 凭据管理器
    （不可用时退化为 DPAPI 加密文件，再退化为仅本次运行有效）；
  * 本目录中的 *.example.json 仅为格式示例，不含任何凭据。

配置模板：{templates}
"""


def _is_excluded_dir(name: str) -> bool:
    return name in EXCLUDE_DIR_NAMES


def _is_excluded_file(name: str) -> bool:
    from fnmatch import fnmatch

    lowered = name.lower()
    if lowered in EXCLUDE_FILE_NAMES:
        return True
    return any(fnmatch(lowered, pat.lower()) for pat in EXCLUDE_FILE_GLOBS)


def _is_kept_file(name: str) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(name, pat) for pat in KEEP_FILE_GLOBS)


def classify(rel: Path, include_txt: bool = False) -> str:
    """返回条目分类：keep / profile / storage_state / cache / logs /
    credentials / dev-artifact。"""
    lowered_parts = [p.lower() for p in rel.parts]
    name = rel.name.lower()

    if any(_is_excluded_dir(part) for part in lowered_parts[:-1]) or (
            rel.is_dir() and _is_excluded_dir(rel.name)):
        for part in lowered_parts:
            if part in ("edge_profile", "browser_profile"):
                return "profile"
            if part == "cache" or part == "enhanced_summary":
                return "cache"
            if part == "logs":
                return "logs"
        return "profile"
    if "storage_state" in name:
        return "storage_state"
    if name.endswith(".log"):
        return "logs"
    if _is_excluded_file(rel.name):
        return "credentials"
    if rel.is_dir():
        return "dev-artifact"
    if include_txt and rel.suffix.lower() == ".txt":
        return "keep"
    return "keep" if _is_kept_file(rel.name) else "dev-artifact"


def collect_plan(source: Path, include_txt: bool = False
                 ) -> tuple[list[Path], dict[str, list[Path]]]:
    """白名单计划：只保留配置模板与 README；其余按类归档（便于审计）。

    「保留：配置模板 + 空目录说明」——因此默认**不复制**开发期探针脚本、
    审计 JSON、截图、日志、__pycache__ 等任何非模板内容。
    """
    keep: list[Path] = []
    excluded: dict[str, list[Path]] = {}
    if not source.is_dir():
        return keep, excluded
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if "__pycache__" in rel.parts or rel.suffix.lower() == ".pyc":
            excluded.setdefault("dev-artifact", []).append(rel)
            continue
        category = classify(rel, include_txt)
        if category == "keep":
            if path.is_file():
                keep.append(rel)
        else:
            excluded.setdefault(category, []).append(rel)
    return keep, excluded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成发布用 runtime（默认 dry-run）")
    parser.add_argument("--source", default=str(ROOT / "runtime"),
                        help="源 runtime 目录（默认 <repo>/runtime）")
    parser.add_argument("--dest", default=str(ROOT / "dist" / "runtime"),
                        help="目标发布 runtime 目录")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="只列出计划（默认）")
    parser.add_argument("--execute", action="store_true", help="真正复制")
    parser.add_argument("--force", action="store_true",
                        help="目标目录已存在时允许覆盖")
    parser.add_argument("--empty-dirs", action="store_true", default=True,
                        help="创建 cache/ logs/ edge_profile/ 空目录（默认）")
    parser.add_argument("--include-txt", action="store_true",
                        help="同时复制 .txt 说明文件（默认只带 README/模板）")
    args = parser.parse_args(argv)

    source = Path(args.source)
    dest = Path(args.dest)

    print("发布 runtime 准备")
    print(f"  源目录  : {source}")
    print(f"  目标目录: {dest}")
    print(f"  模式    : {'EXECUTE' if args.execute else 'DRY-RUN'}")
    print()

    if not source.is_dir():
        print(f"源目录不存在：{source}")
        return 1

    keep, excluded = collect_plan(source, include_txt=args.include_txt)
    templates = [str(p) for p in keep if p.suffix == ".json"]

    print(f"白名单复制 {len(keep)} 个文件（配置模板 + 说明）：")
    for rel in keep:
        print(f"  + {rel}")
    if not keep:
        print("  （无匹配模板；仍会生成 README_release.txt 与空目录说明）")
    print()

    total_excluded = sum(len(v) for v in excluded.values())
    print(f"排除 {total_excluded} 个条目，按类别：")
    for category in ("profile", "storage_state", "cache", "logs",
                     "credentials", "dev-artifact"):
        items = excluded.get(category, [])
        if not items:
            continue
        print(f"  - {category}: {len(items)} 项"
              + (f"，例如 {items[0]}" if items else ""))
    print()

    if not args.execute:
        print("DRY-RUN：未写入任何文件。")
        print("确认后加 --execute 生成发布 runtime。")
        return 0

    if dest.exists():
        if not args.force:
            print(f"目标已存在，未覆盖（如需覆盖请加 --force）：{dest}")
            return 1
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    copied = 0
    for rel in keep:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, target)
        copied += 1

    if args.empty_dirs:
        for name in ("cache", "logs", "edge_profile", "profile"):
            (dest / name).mkdir(parents=True, exist_ok=True)
        # 空目录无法随 git/zip 保留，放说明占位（不含数据）
        for name in ("cache", "logs", "edge_profile", "profile"):
            marker = dest / name / "PUT_USER_DATA_HERE.txt"
            marker.write_text(
                "运行时自动创建用户数据；发布包内保持为空。\n",
                encoding="utf-8")

    readme = dest / README_NAME
    readme.write_text(README_TEXT.format(
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        templates=", ".join(templates) if templates else "（无）"),
        encoding="utf-8")

    print(f"已复制 {copied} 个文件 → {dest}")
    print(f"已写入 {README_NAME}")
    print("提示：接着运行 `python tools/check_release_data.py --scope release "
          f"--release-runtime {dest}` 校验。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
