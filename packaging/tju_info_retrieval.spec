# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve()
if not (PROJECT_ROOT / "main.py").is_file():
    PROJECT_ROOT = PROJECT_ROOT.parent
SOURCE_ROOT = PROJECT_ROOT / "src"
PACKAGE_VERSION = os.environ.get("TJU_PACKAGE_VERSION", "v0.17-test2")
PACKAGE_NAME = f"TJU_Info_Retrieval_{PACKAGE_VERSION}"

datas = [
    (
        str(SOURCE_ROOT / "tju_info_retrieval" / "ui" / "assets" / "tju_logo.png"),
        "tju_info_retrieval/ui/assets",
    ),
]

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT), str(SOURCE_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[str(PROJECT_ROOT / "packaging" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia",
        "PySide6.QtNetworkAuth",
        "PySide6.QtPdf",
        "PySide6.QtPositioning",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtSql",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
    ],
    noarchive=False,
    optimize=0,
)

# Qt uses the Windows ICU shim. A Poppler directory on the build machine's PATH
# can otherwise make PyInstaller collect an incompatible private ICU build.
a.binaries = [
    entry
    for entry in a.binaries
    if Path(entry[0]).name.lower() not in {"icuuc.dll", "icudt78.dll"}
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="信息自动检索整理系统",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="_internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=PACKAGE_NAME,
)
