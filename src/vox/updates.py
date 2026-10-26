"""Verification des mises a jour a partir d'un manifeste JSON heberge.

Principe : l'application interroge une petite URL publique qui decrit la
derniere version publiee. Si elle est plus recente, Vox le signale et propose
d'ouvrir la page de telechargement.

Choix de conception : Vox n'installe **jamais** une mise a jour sans action
explicite de l'utilisateur. Depuis l'onglet « Mises a jour », un seul clic
telecharge la nouvelle version dans un dossier prive, l'installe et redemarre.
Cela evite de dependre d'une signature de code.

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

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import proc

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
    key = platform_key()
    selected = ""
    if isinstance(urls, dict):
        selected = str(urls.get(key) or "").strip()
    # L'ancien champ « url » ne concerne que Windows : s'en servir pour macOS
    # ou Linux ferait telecharger un .exe inutilisable.
    fallback = str(payload.get("url") or "").strip() if key == "windows" else ""
    if not (selected or fallback):
        return None, f"aucun fichier publié pour « {key} »"

    info = UpdateInfo(
        version=version,
        url=selected or fallback,
        notes=str(payload.get("notes") or "").strip(),
        published_at=str(payload.get("published_at") or "").strip(),
    )
    if not info.is_newer_than(current_version):
        return None, f"à jour ({current_version})"
    return info, ""


def suggested_filename(url: str, fallback: str = "Vox-mise-a-jour") -> str:
    """Nom de fichier propose a partir de l'URL de telechargement."""
    name = (url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return name or fallback


def download(
    url: str,
    destination: Path,
    on_progress=None,
    timeout: float = 120.0,
) -> Path:
    """Telecharge un fichier en flux, en signalant la progression.

    `on_progress(recu, total)` est appele a chaque bloc ; `total` vaut 0 quand
    le serveur ne fournit pas de Content-Length. Le fichier n'est renomme a sa
    destination finale qu'une fois le transfert termine (fichier « .part »
    sinon), et un echec nettoie le temporaire.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            with part.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=65536):
                    handle.write(chunk)
                    received += len(chunk)
                    if on_progress is not None:
                        on_progress(received, total)
    except Exception:
        with contextlib.suppress(OSError):
            part.unlink(missing_ok=True)
        raise
    part.replace(destination)
    return destination


def cached_file(url: str, version: str = "") -> Path:
    """Emplacement du fichier de mise a jour dans le dossier prive de Vox."""
    from .paths import updates_dir

    fallback = f"Vox-{version}" if version else "Vox-mise-a-jour"
    return updates_dir() / suggested_filename(url, fallback=fallback)


def cleanup(keep: Path | None = None) -> None:
    """Vide le dossier des mises a jour, sauf le fichier `keep` s'il est donne."""
    from .paths import updates_dir

    directory = updates_dir()
    keep_resolved = Path(keep).resolve() if keep is not None else None
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        try:
            if keep_resolved is not None and entry.resolve() == keep_resolved:
                continue
            entry.unlink()
        except OSError:
            continue


def _relaunch_script(pid: int, target: Path) -> str:
    """Script shell : attend la fin de `pid`, puis lance `target`."""
    return (
        "i=0\n"
        f"while kill -0 {pid} 2>/dev/null; do\n"
        "  i=$((i+1))\n"
        "  if [ $i -gt 150 ]; then break; fi\n"
        "  sleep 0.2\n"
        "done\n"
        f"exec {shlex.quote(str(target))}\n"
    )


# Variables injectees par le lanceur AppImage. On les retire a la relance pour
# que la nouvelle instance monte bien *son* fichier et non l'ancien montage.
_APPIMAGE_ENV = (
    "APPIMAGE",
    "APPDIR",
    "OWD",
    "ARGV0",
    "APPIMAGE_EXTRACT_AND_RUN",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PYTHONHOME",
    "PYTHONPATH",
)


def _relaunch_later(target: Path) -> None:
    """Relance `target` des que le processus courant aura disparu.

    Indispensable : Vox n'accepte qu'une seule instance. Lancer la nouvelle
    version tout de suite la ferait passer pour une « deuxieme instance » : elle
    transmettrait « show » a celle-ci (encore vivante) puis se fermerait — Vox ne
    redemarrerait jamais. On attend donc la disparition de ce processus.
    """
    script = _relaunch_script(os.getpid(), target)
    env = {key: value for key, value in proc.independent_env().items() if key not in _APPIMAGE_ENV}
    subprocess.Popen(  # noqa: S603
        ["/bin/sh", "-c", script],
        env=env,
        cwd=str(target.parent),
        start_new_session=True,
        close_fds=True,
    )


def install(downloaded: Path) -> tuple[bool, str]:
    """Installe une mise a jour telechargee et relance Vox.

    Renvoie `(True, "")` quand l'application doit se fermer tout de suite : la
    nouvelle version sera lancee des que ce processus aura disparu (Linux,
    macOS) ou par l'installeur (Windows). Sinon `(False, raison)`.
    """
    downloaded = Path(downloaded)
    if not downloaded.exists():
        return False, "le fichier téléchargé est introuvable"

    if sys.platform == "win32":
        # Une fenetre montre l'installation et reste ouverte en cas d'echec.
        # Le mode silencieux masquait toutes les erreurs apres la fermeture de Vox.
        try:
            subprocess.Popen(  # noqa: S603
                [str(downloaded), "--update"],
                close_fds=True,
                env=proc.independent_env(),
                **proc.no_window(),
            )
        except OSError as exc:
            return False, f"lancement de l'installeur impossible ({exc})"
        return True, ""

    if sys.platform == "darwin":
        return _install_macos(downloaded)

    appimage = os.environ.get("APPIMAGE", "")
    target = Path(appimage) if appimage else None
    if target is not None and target.is_file():
        # Remplacement atomique : Vox tourne encore sur l'ancien fichier monte,
        # la nouvelle version servira au redemarrage.
        temporary = target.with_name(target.name + ".new")
        try:
            shutil.copy2(downloaded, temporary)
            temporary.chmod(0o755)
            os.replace(temporary, target)
        except OSError as exc:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
            return False, f"remplacement de l'AppImage impossible ({exc})"
        with contextlib.suppress(OSError):
            downloaded.unlink()
        try:
            _relaunch_later(target)
        except OSError as exc:
            return False, f"relance impossible ({exc})"
        return True, ""

    return False, (
        "Vox n'est pas lancé depuis une AppImage : installe la nouvelle version "
        "manuellement."
    )


# ----------------------------------------------------------------------
# macOS : remplacement du bundle .app
# ----------------------------------------------------------------------
def macos_bundle(executable: Path | None = None) -> Path | None:
    """Bundle `.app` qui contient l'executable donne (None hors bundle)."""
    path = Path(executable or sys.executable).resolve()
    for parent in path.parents:
        if parent.suffix == ".app":
            return parent
    return None


def macos_update_script(pid: int, archive: Path, target: Path) -> str:
    """Script shell : attend la fin de Vox, remplace le bundle, relance.

    Le remplacement se fait **apres** l'arret du processus : macOS garde ouverts
    les fichiers d'une application qui tourne, et un `ditto` par-dessus un bundle
    vivant laisse un melange des deux versions.
    """
    workdir = target.with_name(target.name + ".new")
    old = target.with_name(target.name + ".old")
    return (
        "set -e\n"
        f"WORK={shlex.quote(str(workdir))}\n"
        f"OLD={shlex.quote(str(old))}\n"
        f"TARGET={shlex.quote(str(target))}\n"
        f"ARCHIVE={shlex.quote(str(archive))}\n"
        "i=0\n"
        f"while kill -0 {pid} 2>/dev/null; do\n"
        "  i=$((i+1))\n"
        "  if [ $i -gt 300 ]; then break; fi\n"
        "  sleep 0.2\n"
        "done\n"
        'rm -rf "$WORK"\n'
        'mkdir -p "$WORK"\n'
        '/usr/bin/ditto -x -k "$ARCHIVE" "$WORK"\n'
        'APP="$WORK/Vox.app"\n'
        'if [ ! -d "$APP" ]; then\n'
        '  APP="$(find "$WORK" -maxdepth 2 -name Vox.app -type d | head -1)"\n'
        "fi\n"
        'rm -rf "$OLD"\n'
        'mv "$TARGET" "$OLD"\n'
        '/usr/bin/ditto "$APP" "$TARGET"\n'
        'rm -rf "$WORK" "$OLD" "$ARCHIVE"\n'
        # Le telechargement est marque « en quarantaine » par le navigateur :
        # sans ce retrait, Gatekeeper refuse de relancer une application non
        # signee (« Vox est endommagé »).
        '/usr/bin/xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null || true\n'
        '/usr/bin/open "$TARGET"\n'
    )


def _install_macos(downloaded: Path) -> tuple[bool, str]:
    target = macos_bundle()
    if target is None:
        return False, (
            "Vox ne tourne pas depuis une application macOS (.app) : "
            "télécharge la nouvelle version et ouvre-la à la main."
        )
    if downloaded.suffix.lower() != ".zip":
        return False, (
            "Cette mise à jour n'est pas une archive macOS (.zip) : "
            "télécharge la nouvelle version depuis la page des versions."
        )
    if not os.access(target.parent, os.W_OK):
        return False, (
            f"Le dossier {target.parent} n'est pas modifiable par Vox. "
            "Déplace Vox dans « Applications » de ton dossier personnel, "
            "ou installe la mise à jour à la main."
        )
    try:
        subprocess.Popen(  # noqa: S603 - script construit ici, sans entree externe
            ["/bin/sh", "-c", macos_update_script(os.getpid(), downloaded, target)],
            start_new_session=True,
            close_fds=True,
            env=proc.independent_env(),
        )
    except OSError as exc:
        return False, f"lancement de la mise à jour impossible ({exc})"
    return True, ""


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
            "macos": f"https://exemple.tld/vox/Vox-{version}-macos-arm64.zip",
        },
        "notes": notes or "Decris ici ce qui change.",
        "published_at": date.today().isoformat(),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
