"""Construit l'installeur Windows de Vox.

Enchaine tout :
  1. (optionnel) PyInstaller sur Vox.spec pour produire dist-single/Vox.exe
  2. preparation de build/setup-payload (Vox.exe, notice, .env.example, icones)
  3. PyInstaller sur Vox-Setup.spec pour produire Vox-Setup-<version>.exe
  4. renommage avec la version + empreinte SHA-256

Aucune dependance externe : l'installeur est un programme Python/tkinter
construit par PyInstaller, qui embarque Vox.exe comme ressource.

Usage :
    uv run python tools/build_installer.py            # reutilise dist-single/Vox.exe
    uv run python tools/build_installer.py --rebuild  # reconstruit aussi l'appli
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
sys.path.insert(0, str(ROOT / "src"))

PAYLOAD = ROOT / "build" / "setup-payload"
SHARE = ROOT / "dist-share"
TEMPLATES = ROOT / "tools" / "installer"


def read_version() -> str:
    text = (ROOT / "src" / "vox" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("Version introuvable dans src/vox/__init__.py")
    return match.group(1)


def run(command: list[str], label: str, cwd: Path | None = None, env: dict | None = None) -> None:
    print(f"  -> {label}")
    merged = {**os.environ, **(env or {})}
    result = subprocess.run(  # noqa: S603 - commandes construites localement
        command, cwd=cwd or ROOT, capture_output=True, text=True, env=merged
    )
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-25:])
        raise SystemExit(f"Echec de « {label} » :\n{tail}")


def build_app() -> None:
    print("1. Construction de Vox.exe")
    if (ROOT / "dist-single" / "Vox.exe").exists() and not REBUILD_APP:
        print("  -> dist-single/Vox.exe deja present (utilise --rebuild pour le refaire)")
        return
    # VOX_ONEFILE=1 est indispensable : sans lui, Vox.spec produit une version
    # en dossier (dist-single/Vox/Vox.exe) et l'ancien exe reste en place, ce qui
    # embarque silencieusement une vieille version dans l'installeur.
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            "dist-single",
            "Vox.spec",
        ],
        "PyInstaller (Vox.spec, un seul fichier)",
        env={"VOX_ONEFILE": "1"},
    )
    produced = ROOT / "dist-single" / "Vox.exe"
    if not produced.exists():
        raise SystemExit(
            "PyInstaller n'a pas produit dist-single/Vox.exe. "
            "Verifie que VOX_ONEFILE=1 est bien pris en compte par Vox.spec."
        )


def stage_payload(version: str) -> None:
    print("2. Preparation des ressources de l'installeur")
    if PAYLOAD.exists():
        shutil.rmtree(PAYLOAD)
    PAYLOAD.mkdir(parents=True)

    app = ROOT / "dist-single" / "Vox.exe"
    if not app.exists():
        raise SystemExit(
            "dist-single/Vox.exe absent. Lance d'abord :\n"
            "  VOX_ONEFILE=1 uv run pyinstaller --clean --noconfirm "
            "--distpath dist-single Vox.spec"
        )
    shutil.copy2(app, PAYLOAD / "Vox.exe")

    notice = (TEMPLATES / "LISEZ-MOI.txt").read_text(encoding="utf-8")
    (PAYLOAD / "LISEZ-MOI.txt").write_text(
        notice.replace("@VERSION@", version), encoding="utf-8"
    )
    shutil.copy2(ROOT / ".env.example", PAYLOAD / ".env.example")

    # Icones regenerees si besoin (tkinter ne lit pas l'ICO, d'ou le PNG).
    for name in ("Vox.ico", "Vox.png", "Enregistrements.ico", "Enregistrements.png"):
        source = ROOT / "assets" / name
        if not source.exists():
            run([sys.executable, "tools/make_icon.py"], "generation des icones")
        if source.exists():
            shutil.copy2(source, PAYLOAD / name)

    for name in (
        "Vox.exe",
        "LISEZ-MOI.txt",
        ".env.example",
        "Vox.ico",
        "Vox.png",
        "Enregistrements.ico",
        "Enregistrements.png",
    ):
        staged = PAYLOAD / name
        if not staged.exists() or staged.stat().st_size == 0:
            raise SystemExit(f"Ressource manquante ou vide : {name}")

    print(f"  -> {len(list(PAYLOAD.iterdir()))} ressources pretes dans {PAYLOAD}")


def build_setup(version: str) -> Path:
    print("3. Construction de l'installeur")
    run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
         "--distpath", "dist-setup", "Vox-Setup.spec"],
        "PyInstaller (Vox-Setup.spec)",
    )

    produced = ROOT / "dist-setup" / "Vox-Setup.exe"
    if not produced.exists():
        raise SystemExit("Vox-Setup.exe n'a pas ete produit.")

    SHARE.mkdir(parents=True, exist_ok=True)
    final = SHARE / f"Vox-Setup-{version}.exe"
    shutil.copy2(produced, final)

    digest = hashlib.sha256(final.read_bytes()).hexdigest()
    final.with_suffix(".exe.sha256").write_text(
        f"{digest}  {final.name}\n", encoding="utf-8"
    )

    if final.read_bytes()[:2] != b"MZ":
        raise SystemExit("Le fichier produit n'est pas un executable Windows.")

    print()
    print(f"  installeur : {final}")
    print(f"  taille     : {final.stat().st_size / 1048576:.1f} Mo")
    print(f"  sha256     : {digest}")
    return final


REBUILD_APP = False


def main() -> int:
    global REBUILD_APP
    parser = argparse.ArgumentParser(description="Construit l'installeur de Vox")
    parser.add_argument(
        "--rebuild", action="store_true", help="reconstruit aussi Vox.exe avant l'installeur"
    )
    args = parser.parse_args()
    REBUILD_APP = args.rebuild

    version = read_version()
    print(f"Installeur Vox {version}\n")
    build_app()
    stage_payload(version)
    build_setup(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
