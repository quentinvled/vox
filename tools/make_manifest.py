"""Genere le manifeste JSON lu par les clients Vox pour les mises a jour.

Utilise par le workflow de release, mais aussi lancable a la main :

    uv run python tools/make_manifest.py \
        --version 0.2.0 \
        --url https://github.com/quentinvled/vox/releases/download/v0.2.0/Vox-Setup-0.2.0.exe \
        --notes "Correction des tarifs" \
        --out dist-share/version.json

Le fichier produit est ensuite attache a la release GitHub sous le nom
`version.json`, ce qui donne une URL stable :
    https://github.com/quentinvled/vox/releases/latest/download/version.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vox.updates import parse_version  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Genere le manifeste de mise a jour")
    parser.add_argument("--version", required=True, help="numero publie, ex. 0.2.0")
    parser.add_argument("--url", default="", help="URL de l'installeur a telecharger")
    parser.add_argument("--notes", default="", help="nouveautes affichees aux utilisateurs")
    parser.add_argument("--out", default="release/version.json", help="fichier a ecrire")
    args = parser.parse_args()

    version = args.version.strip().lstrip("v")
    if not version:
        raise SystemExit("Version vide.")
    if parse_version(version) == (0,) and version not in {"0"}:
        raise SystemExit(f"Version illisible : {version!r}")

    payload = {
        "version": version,
        "url": args.url.strip(),
        "notes": args.notes.strip(),
        "published_at": date.today().isoformat(),
    }

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Manifeste ecrit : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
