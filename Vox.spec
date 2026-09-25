# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller pour Vox.

Construction : `uv run pyinstaller --clean --noconfirm Vox.spec`
Resultat     : dist/Vox/Vox.exe

Pour diagnostiquer un plantage silencieux, construire une variante avec
console : `VOX_CONSOLE=1 uv run pyinstaller --clean --noconfirm Vox.spec`

Notes :
  * Le script d'entree est run_vox.py, pas src/vox/__main__.py (imports relatifs).
  * sounddevice embarque une DLL PortAudio qu'il faut collecter explicitement.
  * On exclut les gros modules Qt inutilises : l'app ne se sert que de
    QtCore, QtGui, QtWidgets et QtNetwork.
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

CONSOLE = os.environ.get("VOX_CONSOLE") == "1"
# VOX_ONEFILE=1 -> un seul Vox.exe (demarrage plus lent : l'archive est
# decompressee dans %TEMP% a chaque lancement).
ONEFILE = os.environ.get("VOX_ONEFILE") == "1"

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
    "keyboard",
    "keyboard._winkeyboard",
    "numpy",
    "httpx",
    "httpcore",
    "anyio",
]

ICON = "assets/Vox.ico"

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
        # Modules Qt non utilises (pyside6-addons en installe beaucoup)
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
        "PySide6.QtTest",
        "PySide6.QtUiTools",
        "PySide6.QtWebChannel",
        "PySide6.QtWebSockets",
        "PySide6.QtSerialPort",
        "PySide6.QtSvgWidgets",
        "PySide6.QtTextToSpeech",
        "PySide6.QtSpatialAudio",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtStateMachine",
        # Autres
        "tkinter",
        "unittest",
        "pydoc_data",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="Vox",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
    )
else:
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
        console=CONSOLE,
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
