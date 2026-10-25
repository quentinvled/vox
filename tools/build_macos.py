"""Construit l'application macOS de Vox (Vox.app, .zip et .dmg).

Enchaine tout :
  1. conversion de l'icone PNG en .icns (sips + iconutil, fournis par macOS) ;
  2. PyInstaller (Vox-macos.spec) -> dist-macos/Vox.app ;
  3. archive .zip (pour la mise a jour automatique) + .dmg (pour l'installation)
     et leurs empreintes SHA-256 dans dist-share/.

Le .zip est l'equivalent du `Vox-Setup-<version>.exe` Windows : c'est ce fichier
que Vox telecharge et installe depuis l'onglet « Mises à jour ».

Usage (sur un Mac) :
    uv run python tools/build_macos.py
    uv run python tools/build_macos.py --skip-build
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist-macos"
SHARE = ROOT / "dist-share"
ICON_PNG = ROOT / "assets" / "Vox.png"
ICON_ICNS = ROOT / "build" / "Vox.icns"
APP = DIST / "Vox.app"

ARCH_LABEL = "arm64" if platform.machine() == "arm64" else "x86_64"


def read_version() -> str:
    text = (ROOT / "src" / "vox" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("Version introuvable dans src/vox/__init__.py")
    return match.group(1)


def run(command: list[str], label: str, env: dict | None = None) -> None:
    print(f"  -> {label}")
    merged = {**os.environ, **(env or {})}
    result = subprocess.run(  # noqa: S603 - commandes construites localement
        command, cwd=ROOT, capture_output=True, text=True, env=merged
    )
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-15:])
        raise SystemExit(f"{label} a echoue.\n{tail}")


# ----------------------------------------------------------------------
# Icone
# ----------------------------------------------------------------------
def build_icon() -> None:
    """Fabrique build/Vox.icns a partir de assets/Vox.png (outils macOS)."""
    if not ICON_PNG.exists():
        print("  -> icon : assets/Vox.png absent, icone par defaut conservee")
        return
    iconset = ROOT / "build" / "Vox.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    # iconutil attend precisement ces dix fichiers (tailles de base ×1 et ×2).
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = base * scale
            suffix = "@2x" if scale == 2 else ""
            name = f"icon_{base}x{base}{suffix}.png"
            run(
                ["sips", "-z", str(pixels), str(pixels), str(ICON_PNG), "--out", str(iconset / name)],
                f"redimensionnement {pixels}px",
            )
    run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(ICON_ICNS)],
        "assemblage de l'icone .icns",
    )
    shutil.rmtree(iconset, ignore_errors=True)


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------
def build_app() -> None:
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            str(DIST),
            "--workpath",
            str(ROOT / "build" / "macos"),
            "Vox-macos.spec",
        ],
        "Construction de Vox.app (PyInstaller)",
    )
    if not APP.exists():
        raise SystemExit(f"Application introuvable apres construction : {APP}")


def check_plist() -> None:
    """Verifie les cles indispensable d'Info.plist (garde-fou de publication)."""
    plist = APP / "Contents" / "Info.plist"
    if not plist.exists():
        raise SystemExit(f"Info.plist introuvable : {plist}")
    payload = plistlib.loads(plist.read_bytes())
    missing = [
        key
        for key in ("CFBundleIdentifier", "CFBundleShortVersionString", "NSMicrophoneUsageDescription")
        if not payload.get(key)
    ]
    if missing:
        raise SystemExit(f"Cles absentes d'Info.plist : {', '.join(missing)}")
    if not payload.get("LSUIElement"):
        print("  -> attention : LSUIElement absent (icone dans le Dock)")


def make_zip(version: str) -> Path:
    SHARE.mkdir(parents=True, exist_ok=True)
    output = SHARE / f"Vox-{version}-macos-{ARCH_LABEL}.zip"
    if output.exists():
        output.unlink()
    # ditto preserve les liens symboliques, les ressources et les droits : un
    # `zip` classique casse le bundle.
    run(
        ["ditto", "-c", "-k", "--keepParent", str(APP), str(output)],
        "Archivage .zip (ditto)",
    )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (output.with_suffix(output.suffix + ".sha256")).write_text(
        f"{digest}  {output.name}\n", encoding="utf-8"
    )
    return output


def make_dmg(version: str) -> Path:
    SHARE.mkdir(parents=True, exist_ok=True)
    output = SHARE / f"Vox-{version}-macos-{ARCH_LABEL}.dmg"
    staging = ROOT / "build" / "dmg"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(APP, staging / "Vox.app", symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    if output.exists():
        output.unlink()
    run(
        [
            "hdiutil",
            "create",
            "-volname",
            f"Vox {version}",
            "-srcfolder",
            str(staging),
            "-ov",
            "-format",
            "UDZO",
            str(output),
        ],
        "Creation du .dmg (hdiutil)",
    )
    shutil.rmtree(staging, ignore_errors=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (output.with_suffix(output.suffix + ".sha256")).write_text(
        f"{digest}  {output.name}\n", encoding="utf-8"
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Construit l'application macOS de Vox")
    parser.add_argument("--skip-build", action="store_true", help="reutilise dist-macos/Vox.app")
    args = parser.parse_args()

    if sys.platform != "darwin":
        raise SystemExit("Ce script doit tourner sur macOS (sips, iconutil, ditto, hdiutil).")

    version = read_version()
    print(f"Vox {version} — application macOS ({ARCH_LABEL})")

    if not args.skip_build:
        build_icon()
        build_app()
    check_plist()

    archive = make_zip(version)
    disk = make_dmg(version)

    for path in (archive, disk):
        size = path.stat().st_size / (1024 * 1024)
        print(f"  {path.name} : {size:.1f} Mo")
    print(f"\nApplication prete : {APP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
