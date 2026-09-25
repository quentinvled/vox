"""Genere le manifeste JSON lu par les clients Vox pour les mises a jour.

Utilise par le workflow de release, mais aussi lancable a la main :

    uv run python tools/make_manifest.py \
        --version 0.2.0 \
        --windows https://github.com/quentinvled/vox/releases/download/v0.2.0/Vox-Setup-0.2.0.exe \
        --linux https://github.com/quentinvled/vox/releases/download/v0.2.0/Vox-0.2.0-x86_64.AppImage \
        --notes "Correction des tarifs" \
        --out dist-share/version.json

Le fichier produit est ensuite attache a la release GitHub sous le nom
`version.json`, ce qui donne une URL stable :
    https://github.com/quentinvled/vox/releases/latest/download/version.json
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

SEMVER = re.compile(r"^\d+(\.\d+)*$")
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Genere le manifeste de mise a jour")
    parser.add_argument("--version", required=True, help="numero publie, ex. 0.2.0")
    parser.add_argument("--url", default="", help="URL Windows (compatibilite)")
    parser.add_argument("--windows", default="", help="URL de l'installeur Windows")
    parser.add_argument("--linux", default="", help="URL de l'AppImage Linux")
    parser.add_argument("--notes", default="", help="nouveautes affichees aux utilisateurs")
    parser.add_argument("--out", default="release/version.json", help="fichier a ecrire")
    args = parser.parse_args()

    version = args.version.strip().lstrip("v")
    if not version:
        raise SystemExit("Version vide.")
    if not SEMVER.match(version):
        raise SystemExit(f"Version illisible : {version!r} (format attendu X.Y.Z)")

    windows = args.windows.strip() or args.url.strip()
    linux = args.linux.strip()

    urls: dict[str, str] = {}
    if windows:
        urls["windows"] = windows
    if linux:
        urls["linux"] = linux

    payload = {
        "version": version,
        "url": windows,
        "notes": args.notes.strip(),
        "published_at": date.today().isoformat(),
    }
    if urls:
        payload["urls"] = urls

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Manifeste ecrit : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
