# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller de l'installeur Vox (interface tkinter).

Le vrai `Vox.exe` est embarque comme ressource : l'installeur est donc un
unique fichier que tes amis double-cliquent.

Prerequis : avoir construit l'application, puis lance :
    VOX_ONEFILE=1 uv run pyinstaller --clean --noconfirm --distpath dist-single Vox.spec
    uv run python tools/build_installer.py        <- prepare build/setup-payload/
    uv run pyinstaller --clean --noconfirm --distpath dist-share Vox-Setup.spec

`tools/build_installer.py` enchaîne tout : c'est la commande a utiliser.

Note : PySide6 est exclu. L'installeur ne doit embarquer que Python, tkinter et
les quelques modules de `vox` dont il a besoin (vox.install, vox.paths).
"""

import os

ONEFILE = os.environ.get("VOX_SETUP_DIR") != "1"
PAYLOAD = "build/setup-payload"

hiddenimports = ["vox", "vox.install", "vox.paths", "tkinter", "tkinter.ttk"]

excludes = [
    # Aucun besoin de Qt dans l'installeur : c'est la moitie du poids.
    "PySide6",
    "shiboken6",
    "numpy",
    "sounddevice",
    "keyboard",
    "httpx",
    "httpcore",
    "anyio",
    "h11",
    "certifi",
    "serilog",
    "unittest",
    "pydoc_data",
    "xml",
    "email",
    "pytest",
]

a = Analysis(
    ["installer/vox_setup.py"],
    pathex=["src", "."],
    binaries=[],
    datas=[
        (f"{PAYLOAD}/Vox.exe", "."),
        (f"{PAYLOAD}/LISEZ-MOI.txt", "."),
        (f"{PAYLOAD}/.env.example", "."),
        (f"{PAYLOAD}/Vox.ico", "."),
        (f"{PAYLOAD}/Vox.png", "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Vox-Setup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/Vox.ico",
)
