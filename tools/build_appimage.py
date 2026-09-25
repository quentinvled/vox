"""Construit l'AppImage Linux de Vox.

Enchaine tout :
  1. PyInstaller (Vox.spec, mode onefile) -> dist-linux/Vox
  2. preparation d'un AppDir (binaire, .desktop, icone, AppRun)
  3. appimagetool -> dist-share/Vox-<version>-x86_64.AppImage (+ SHA-256)

L'AppImage est le pendant Linux du `Vox-Setup-<version>.exe` : un fichier
unique, executable apres un `chmod +x`, sans installation.

Prerequis :
    * avoir construit PyInstaller (fait automatiquement, sauf --skip-build) ;
    * `appimagetool` disponible (`APPIMAGETOOL=/chemin/vers/appimagetool`, ou
      dans le PATH). En CI, il est telecharge juste avant.

Usage :
    uv run python tools/build_appimage.py
    uv run python tools/build_appimage.py --skip-build
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist-linux"
APPDIR = ROOT / "build" / "appimage" / "Vox.AppDir"
SHARE = ROOT / "dist-share"
ICON = ROOT / "assets" / "Vox.png"

DESKTOP = """[Desktop Entry]
Type=Application
Name=Vox
GenericName=Dictee vocale
Comment=Dictee vocale globale : Ctrl + Maj, on parle, le texte s'ecrit.
Exec=Vox
Icon=vox
Terminal=false
Categories=Utility;Accessibility;
Keywords=dictation;voice;speech;transcription;openrouter;
X-AppImage-Version={version}
"""

APPRUN = """#!/bin/sh
# Point d'entree de l'AppImage : lance le binaire embarque en transmettant
# les arguments (utile pour --recordings, --uninstall, etc.).
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/Vox" "$@"
"""


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


def appimagetool() -> str:
    override = os.environ.get("APPIMAGETOOL")
    if override:
        return override
    found = shutil.which("appimagetool")
    if not found:
        raise SystemExit(
            "appimagetool est introuvable. Telecharge-le :\n"
            "  curl -L -o /tmp/appimagetool "
            "https://github.com/AppImage/appimagetool/releases/download/continuous/"
            "appimagetool-x86_64.AppImage\n"
            "  chmod +x /tmp/appimagetool\n"
            "puis relance avec APPIMAGETOOL=/tmp/appimagetool"
        )
    return found


def build_binary() -> None:
    run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
         "--distpath", str(DIST), "Vox.spec"],
        "Construction du binaire Vox (PyInstaller, onefile)",
        env={"VOX_ONEFILE": "1"},
    )


def build_appdir(version: str) -> None:
    if APPDIR.exists():
        shutil.rmtree(APPDIR)
    (APPDIR / "usr" / "bin").mkdir(parents=True)
    (APPDIR / "usr" / "share" / "applications").mkdir(parents=True)
    icons = APPDIR / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icons.mkdir(parents=True)

    binary = DIST / "Vox"
    if not binary.exists():
        raise SystemExit(f"Binaire introuvable : {binary}")
    shutil.copy2(binary, APPDIR / "usr" / "bin" / "Vox")
    (APPDIR / "usr" / "bin" / "Vox").chmod(0o755)

    shutil.copy2(ICON, APPDIR / "vox.png")
    shutil.copy2(ICON, icons / "vox.png")

    desktop = DESKTOP.format(version=version)
    (APPDIR / "vox.desktop").write_text(desktop, encoding="utf-8")
    (APPDIR / "usr" / "share" / "applications" / "vox.desktop").write_text(
        desktop, encoding="utf-8"
    )

    apprun = APPDIR / "AppRun"
    apprun.write_text(APPRUN, encoding="utf-8")
    apprun.chmod(0o755)


def make_appimage(version: str) -> Path:
    tool = appimagetool()
    SHARE.mkdir(parents=True, exist_ok=True)
    output = SHARE / f"Vox-{version}-x86_64.AppImage"
    if output.exists():
        output.unlink()
    env = {"ARCH": "x86_64", "VERSION": version, "APPIMAGE_EXTRACT_AND_RUN": "1"}
    # appimagetool est lui-meme une AppImage : APPIMAGE_EXTRACT_AND_RUN evite
    # de dependre de FUSE (absent de certaines distributions et images CI).
    run([tool, "--no-appstream", str(APPDIR), str(output)], "Generation de l'AppImage", env)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (output.with_suffix(output.suffix + ".sha256")).write_text(
        f"{digest}  {output.name}\n", encoding="utf-8"
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Construit l'AppImage Linux de Vox")
    parser.add_argument("--skip-build", action="store_true", help="reutilise dist-linux/Vox")
    args = parser.parse_args()

    version = read_version()
    print(f"Vox {version} — AppImage Linux")

    if not args.skip_build:
        build_binary()
    build_appdir(version)
    output = make_appimage(version)

    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"\nAppImage prete : {output} ({size_mb:.1f} Mo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
