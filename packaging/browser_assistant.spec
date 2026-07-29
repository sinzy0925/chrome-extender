# -*- mode: python ; coding: utf-8 -*-
# Windows onefile build. Run via: powershell -File scripts/build_windows.ps1

import os
from pathlib import Path

SPECDIR = Path(SPECPATH)  # noqa: F821 — provided by PyInstaller
ROOT = SPECDIR.parent

block_cipher = None

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[(str(ROOT / ".env.example"), ".")],
    hiddenimports=[
        "browser_assistant",
        "browser_assistant.__main__",
        "browser_assistant.agent",
        "browser_assistant.browser",
        "browser_assistant.config",
        "browser_assistant.executor",
        "browser_assistant.export",
        "browser_assistant.gemini_client",
        "browser_assistant.observe",
        "browser_assistant.paths",
        "browser_assistant.safety",
        "browser_assistant.schemas",
        "browser_assistant.startup_check",
        "browser_assistant.ui_app",
        "google.genai",
        "playwright",
        "dotenv",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Never ship a real .env
a.datas = [
    d
    for d in a.datas
    if not str(d[0]).replace("\\", "/").endswith("/.env") and Path(str(d[0])).name != ".env"
]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="browser-assistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
