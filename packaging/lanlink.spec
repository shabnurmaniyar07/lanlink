# PyInstaller specification for LanLink.
#
# A one-*folder* build on purpose. One-file looks tidier but unpacks itself to a
# temporary directory on every launch: slower to start, and it confuses Windows
# Firewall, which then asks about a different path each time. The installer
# hides the folder anyway.
#
#     pyinstaller packaging\lanlink.spec --noconfirm
#
# Produces dist\LanLink\LanLink.exe

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is injected by PyInstaller
ICON = ROOT / "packaging" / "lanlink.ico"

hidden = [
    # zeroconf reaches for its platform backend at run time, so the analysis
    # cannot see it. Without this, discovery silently fails in the built app.
    *collect_submodules("zeroconf"),
    "lanlink.ui.theme",
    "lanlink.updates",
    "lanlink.remote",
]

analysis = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    # The icon travels with the application so the window and taskbar show it,
    # not only the file in Explorer.
    datas=[(str(ICON), ".")] if ICON.is_file() else [],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    # Qt ships a great deal LanLink never uses. Leaving it out keeps the
    # installer to a sensible size and, more usefully, keeps a browser engine
    # out of an application whose whole point is not having one.
    excludes=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebChannel",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtOpenGL",
        "PySide6.QtPdf",
        "PySide6.QtDesigner",
        "PySide6.QtTest",
        "tkinter",
        "unittest",
        "pytest",
        "matplotlib",
        "numpy",
        "PIL",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)  # noqa: F821

executable = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="LanLink",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # No console window: this is a desktop application, and a black terminal
    # behind it looks broken to anybody who is not a developer.
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICON) if ICON.is_file() else None,
    version=str(ROOT / "packaging" / "version_info.txt")
    if (ROOT / "packaging" / "version_info.txt").is_file() and sys.platform == "win32"
    else None,
)

COLLECT(  # noqa: F821
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LanLink",
)
