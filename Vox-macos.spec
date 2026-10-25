# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller de Vox pour macOS.

Construit `dist-macos/Vox.app` : application autonome, sans icone dans le Dock
(`LSUIElement`), qui vit dans la barre des menus comme le reste des utilitaires
mac. L'emballage final (zip, dmg, empreintes) est fait par
`tools/build_macos.py`.

Notes :
  * L'icone `.icns` est preparee par `tools/build_macos.py` dans build/Vox.icns
    (sips + iconutil, fournis par macOS) ; sans elle, l'application garde
    l'icone générique.
  * pynput tire PyObjC (Quartz, AppKit) : ce sont ces modules qui donnent la
    frappe clavier et l'application active.
"""

import os
import re

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

ROOT = os.path.abspath(os.getcwd())


def read_version() -> str:
    with open(os.path.join(ROOT, "src", "vox", "__init__.py"), encoding="utf-8") as handle:
        text = handle.read()
    found = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return found.group(1) if found else "0.0.0"


datas, binaries, hiddenimports = [], [], []

# PortAudio + donnees de sounddevice
sd_datas, sd_binaries, sd_hidden = collect_all("sounddevice")
datas += sd_datas
binaries += sd_binaries
hiddenimports += sd_hidden
binaries += collect_dynamic_libs("sounddevice")

hiddenimports += [
    "vox",
    "vox.__main__",
    "numpy",
    "httpx",
    "httpcore",
    "anyio",
    # Raccourci global, frappe clavier et application active.
    "pynput",
    "pynput.keyboard",
    "pynput.keyboard._darwin",
    "pynput._util",
    "pynput._util.darwin",
    "Quartz",
    "AppKit",
    "objc",
    "ApplicationServices",
]

ICON = os.path.join(ROOT, "build", "Vox.icns")
if not os.path.exists(ICON):
    ICON = None

a = Analysis(
    ["run_vox.py"],
    pathex=["src", "."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtQml",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtBluetooth",
        "PySide6.QtDesigner",
        "PySide6.QtHelp",
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtPositioning",
        "PySide6.QtSql",
        "PySide6.QtSerialPort",
        "PySide6.QtSvgWidgets",
        "PySide6.QtTextToSpeech",
        "PySide6.QtSpatialAudio",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtStateMachine",
        "tkinter",
        "unittest",
        "pydoc_data",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Vox",
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
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Vox",
)

VERSION = read_version()

app = BUNDLE(
    coll,
    name="Vox.app",
    icon=ICON,
    bundle_identifier="fr.vled.vox",
    version=VERSION,
    info_plist={
        # Utilitaire de barre de menus : pas d'icone dans le Dock.
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "CFBundleDisplayName": "Vox",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSMicrophoneUsageDescription": (
            "Vox enregistre ta voix uniquement pendant l'appui sur le raccourci, "
            "pour la transcrire puis écrire le texte dans la fenêtre active."
        ),
        "NSAppleEventsUsageDescription": (
            "Vox utilise le presse-papier pour insérer le texte dicté."
        ),
    },
)
