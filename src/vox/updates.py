"""Verification des mises a jour a partir d'un manifeste JSON heberge.

Principe : l'application interroge une petite URL publique qui decrit la
derniere version publiee. Si elle est plus recente, Vox le signale et propose
d'ouvrir la page de telechargement.

Choix de conception : Vox ne telecharge **jamais** et n'execute **jamais**
automatiquement un binaire distant. Il se contente d'informer et d'ouvrir le
navigateur. C'est plus sur, et ca evite de dependre d'une signature de code.

Format du manifeste (JSON) :

    {
      "version": "0.2.0",
      "url": "https://exemple.tld/vox/Vox-Setup-0.2.0.exe",
      "urls": {
        "windows": "https://exemple.tld/vox/Vox-Setup-0.2.0.exe",
        "linux": "https://exemple.tld/vox/Vox-0.2.0-x86_64.AppImage"
      },
      "notes": "Correction des tarifs, installateur Windows",
      "published_at": "2026-10-01"
    }

`url` reste le telechargement Windows pour la compatibilite ; `urls` permet de
servir plusieurs systemes depuis un seul manifeste.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

import httpx

DEFAULT_TIMEOUT = 15.0


def platform_key() -> str:
    """Cle de telechargement correspondant au systeme courant."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


@dataclass
class UpdateInfo:
    """Description d'une version publiee."""

    version: str
    url: str = ""
    notes: str = ""
    published_at: str = ""

    def is_newer_than(self, current: str) -> bool:
        return parse_version(self.version) > parse_version(current)


def parse_version(text: str) -> tuple[int, ...]:
    """« v0.2.1-beta » -> (0, 2, 1). Tolerant aux suffixes."""
    if not text:
        return (0,)
    numbers = re.findall(r"\d+", str(text).split("-")[0])
    if not numbers:
        return (0,)
    return tuple(int(part) for part in numbers)


def check(
    manifest_url: str,
    current_version: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[UpdateInfo | None, str]:
    """Interroge le manifeste.

    Renvoie `(info, "")` si une version plus recente existe, `(None, raison)`
    sinon. Ne leve jamais : une mise a jour ratee ne doit pas generer d'erreur
    visible.
    """
    if not manifest_url or not manifest_url.strip():
        return None, "aucune URL de mise à jour configurée"

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(
                manifest_url.strip(),
                headers={"Accept": "application/json", "User-Agent": "Vox-Updater"},
            )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return None, f"manifeste injoignable ({exc})"

    if not isinstance(payload, dict):
        return None, "manifeste illisible"

    version = str(payload.get("version") or "").strip()
    if not version:
        return None, "manifeste sans champ « version »"

    urls = payload.get("urls")
    selected = ""
    if isinstance(urls, dict):
        selected = str(urls.get(platform_key()) or "").strip()

    info = UpdateInfo(
        version=version,
        url=selected or str(payload.get("url") or "").strip(),
        notes=str(payload.get("notes") or "").strip(),
        published_at=str(payload.get("published_at") or "").strip(),
    )
    if not info.is_newer_than(current_version):
        return None, f"à jour ({current_version})"
    return info, ""


def manifest_example(version: str, url: str = "", notes: str = "") -> str:
    """Gabarit pret a publier (utile pour `vox --write-manifest`)."""
    from datetime import date

    windows = url or f"https://exemple.tld/vox/Vox-Setup-{version}.exe"
    payload = {
        "version": version,
        "url": windows,
        "urls": {
            "windows": windows,
            "linux": f"https://exemple.tld/vox/Vox-{version}-x86_64.AppImage",
        },
        "notes": notes or "Decris ici ce qui change.",
        "published_at": date.today().isoformat(),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
