"""Rangement des appels et des imports dans un dossier dedie.

Les premieres versions ecrivaient les pistes d'appel et la bibliotheque dans
le dossier de donnees technique (`%LOCALAPPDATA%\\Vox` sous Windows), difficile
a retrouver. Les appels et les imports vivent desormais dans `Documents\\Vox`,
avec deux sous-dossiers : `Appels` et `Imports`.

La migration est faite une seule fois au demarrage, sans supprimer l'ancien
index (il reste une copie de secours) et sans jamais ecraser un fichier deja
present dans le nouveau dossier.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import threading
from pathlib import Path

from .paths import (
    calls_dir,
    legacy_calls_dir,
    legacy_library_dir,
    legacy_library_index_file,
    library_dir,
    library_index_file,
    media_dir,
)

log = logging.getLogger("vox")

_LOCK = threading.Lock()


def migrate() -> list[Path]:
    """Deplace appels et imports vers le dossier dedie ; renvoie les fichiers bouges."""
    with _LOCK:
        moved: list[Path] = []
        for source, target in (
            (legacy_calls_dir(), calls_dir()),
            (legacy_library_dir(), library_dir()),
        ):
            moved.extend(_move_tree(source, target))
        moved.extend(_merge_index(legacy_library_index_file(), library_index_file()))
        if moved:
            log.info("Appels et imports ranges dans %s (%d fichier(s))", media_dir(), len(moved))
        return moved


def _move_tree(source: Path, target: Path) -> list[Path]:
    """Deplace les fichiers d'un dossier vers un autre, sans ecraser."""
    if not source.is_dir():
        return []
    moved: list[Path] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        destination = target / path.relative_to(source)
        if destination.exists():
            # Meme nom deja present : on ne touche a aucune des deux copies.
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
            moved.append(destination)
        except OSError as exc:
            log.warning("Deplacement impossible (%s → %s) : %s", path, destination, exc)
    return moved


def _merge_index(source: Path, target: Path) -> list[Path]:
    """Fusionne l'ancien index dans le nouveau, sans doublon ni perte."""
    if not source.is_file():
        return []
    entries = _read_lines(target) + _read_lines(source)
    seen: set[str] = set()
    merged: list[dict] = []
    for entry in entries:
        identifier = str(entry.get("id") or "")
        if identifier and identifier in seen:
            continue
        if identifier:
            seen.add(identifier)
        merged.append(entry)
    if not merged:
        return []
    temporary = target.with_suffix(".jsonl.tmp")
    try:
        temporary.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as handle:
            for entry in merged:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        temporary.replace(target)
    except OSError as exc:
        log.warning("Fusion de l'index impossible : %s", exc)
        return []
    # L'ancien index reste sur place, renomme : plus de fusion au demarrage
    # suivant, et une copie de secours demeure disponible.
    with contextlib.suppress(OSError):
        source.replace(source.with_name(source.name + ".migre"))
    return [target]


def _read_lines(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    entries: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                entries.append(data)
    except OSError as exc:
        log.warning("Index illisible (%s) : %s", path, exc)
    return entries
