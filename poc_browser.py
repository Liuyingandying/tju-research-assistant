#!/usr/bin/env python3
"""PoC-0：受控 Edge + 天津大学图书馆登录会话持久化验证。

本程序只做一件事：用项目专用 Edge Profile 打开天津大学电子资源远程访问平台，
由用户手动完成身份认证，并验证关闭后重开程序仍能复用登录 Session。

安全约束（详见 README.md）：
- 不读取/保存任何账号密码
- 不打印 Cookie / token
- 不访问系统默认浏览器 Profile
- 不做批量抓取、不绕过任何认证
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parent / "src"
if _SOURCE_ROOT.is_dir() and str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - 依赖缺失时的降级提示
    sync_playwright = None

from tju_info_retrieval.app_paths import edge_profile_dir, project_root, runtime_dir

PROJECT_ROOT = project_root()
RUNTIME_DIR = runtime_dir()
EDGE_PROFILE_DIR = edge_profile_dir()

DEFAULT_URL = "https://p.lib.tju.edu.cn/"

# 用于判断“是否仍在登录页”的 URL 子串启发式（只做普通 DOM 验证，不读取任何凭证）
LOGIN_URL_HINTS = ("login", "cas", "sso", "authserver", "signin", "oauth")

# 本机 Edge 常见安装路径
def edge_executable_candidates() -> tuple[Path, ...]:
    """系统级和用户级 Edge 常见安装路径（不下载、不访问默认 Profile）。"""
    roots = [
        os.environ.get("PROGRAMFILES", ""),
        os.environ.get("PROGRAMFILES(X86)", ""),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    return tuple(
        Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        for root in roots if root
    )


# 保留既有公开常量，避免打包路径审计破坏 PoC/测试兼容性。
EDGE_EXECUTABLE_CANDIDATES = edge_executable_candidates()


def find_edge_executable() -> str | None:
    """在本机查找 Microsoft Edge 可执行文件路径，找不到返回 None。"""
    for candidate in edge_executable_candidates():
        if candidate.is_file():
            return str(candidate)
    return None


def profile_dir() -> Path:
    """返回项目专用 Edge Profile 目录（绝不指向系统默认 Profile）。"""
    return EDGE_PROFILE_DIR


def build_launch_kwargs(edge_path: str | None = None) -> dict:
    """构造 launch_persistent_context 所需参数（独立函数便于单元测试）。"""
    kwargs: dict = {
        "user_data_dir": str(EDGE_PROFILE_DIR),
        "headless": False,
        # 显式启用 Chromium sandbox，避免 Playwright 默认注入 --no-sandbox，
        # 否则 Edge 顶部会出现“不受支持的命令行标志”安全警告。
        "chromium_sandbox": True,
        "args": [
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=msEdgeFirstRunExperience",
        ],
    }
    if edge_path:
        kwargs["executable_path"] = edge_path
    return kwargs


def is_login_url(url: str) -> bool:
    """启发式判断 URL 是否仍处于登录/认证流程。"""
    lowered = url.lower()
    return any(hint in lowered for hint in LOGIN_URL_HINTS)


def _print_auth_prompt() -> None:
    print("=" * 64)
    print("请在浏览器中手动完成天津大学官方身份认证。")
    print("本程序不会读取或保存您的账号密码。")
    print("浏览器将保持运行，完成认证后回到本终端按 Enter 安全结束。")
    print("=" * 64)


def main() -> int:
    parser = argparse.ArgumentParser(description="PoC-0 受控 Edge 会话验证")
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="要打开的页面（默认天津大学电子资源远程访问平台）",
    )
    args = parser.parse_args()

    if sync_playwright is None:
        print(
            "错误：未安装 playwright。请先执行：python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    edge_path = find_edge_executable()
    if edge_path is None:
        print("错误：未找到 Microsoft Edge，请先安装 Edge 后重试。", file=sys.stderr)
        return 2

    EDGE_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    _print_auth_prompt()
    print(f"Edge 可执行文件: {edge_path}")
    print(f"专用 Profile: {EDGE_PROFILE_DIR}")
    print(f"打开页面: {args.url}")
    print()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            **build_launch_kwargs(edge_path)
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:
                print(f"警告：页面加载未完成（{exc}），继续等待您在浏览器中操作。")
            # 等待可能的跳转稳定
            page.wait_for_timeout(2500)

            # 一次普通 DOM 读取验证（不读取任何凭证）
            title = ""
            url = ""
            try:
                title = page.title()
                url = page.url
            except Exception:
                pass
            print("-" * 64)
            print(f"Page title: {title}")
            print(f"Current URL: {url}")
            if is_login_url(url):
                print("状态：当前仍在登录/认证流程，等待您手动完成认证。")
            else:
                print("状态：已离开登录页（可能已处于授权状态）。")
            print("-" * 64)
            print("浏览器将保持运行。")
            print("请完成天津大学登录及资源访问验证。")
            print("完成本次人工实验后，回到此终端按 Enter 安全结束。")
            print("也可使用 Ctrl+C 中止。")
            print("-" * 64)

            # 人工结束机制：不依赖任何 page/context 关闭事件或超时。
            # 浏览器作为独立进程持续存活，允许 SSO 跳转、新标签页、popup 等
            # 完整认证流程；只有用户在终端按 Enter（或 Ctrl+C）才结束。
            try:
                input()
            except EOFError:
                print("\nstdin 已关闭，结束本次会话。")
            except KeyboardInterrupt:
                print("\n检测到 Ctrl+C，正在关闭浏览器（会话已保存到专用 Profile）。")
            # 若用户已提前手动关闭浏览器窗口，仅提示，不视为错误
            try:
                context.pages
            except Exception:
                print("（检测到浏览器窗口已提前关闭，会话状态仍已保留在专用 Profile 中。）")
        except KeyboardInterrupt:
            print("\n检测到 Ctrl+C，正在关闭浏览器（会话已保存到专用 Profile）。")
        finally:
            try:
                context.close()
            except Exception:
                pass

    print("本次浏览器会话已正常结束，专用 Profile 已保留。")
    print("登录状态保存在 runtime/edge_profile 中，可再次运行本程序验证 Session 复用。")
    print("（若第二次仍需重新认证，请区分：Profile 未保存 vs 服务器主动使 Session 失效。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
