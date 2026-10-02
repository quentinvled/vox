"""Bibliotheque des imports : transcript + metadonnees, sans copier l'audio.

Un import (reunion, appel, vocal) reste **ou l'utilisateur l'a range** : on ne
duplique jamais un fichier de plusieurs dizaines de Mo. La bibliotheque stocke
a cote un JSONL d'index (`bibliotheque.jsonl`) et le transcript complet de
chaque entree (dossier `bibliotheque/transcripts/`), qui porte les locuteurs,
les corrections et de quoi regenerer tous les exports.

Supprimer une entree ne supprime donc **que** le transcript et la ligne
d'index : le fichier audio d'origine n'est jamais touche.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import library_index_file, library_transcripts_dir
from .transcript import Transcript

log = logging.getLogger("vox")

STATUS_LABELS: dict[str, str] = {
    "ok": "Transcrit",
    "erreur": "Échec",
}

KIND_IMPORT = "import"
KIND_CALL = "appel"


def _count_words(transcript: Transcript) -> int:
    return sum(len(segment.text.split()) for segment in transcript.segments)


def _preview(transcript: Transcript) -> str:
    """Début du transcript, pour la recherche dans la bibliothèque."""
    text = " ".join(segment.text for segment in transcript.segments[:4])
    return " ".join(text.split())[:180]


def _apply_extras(entry: ImportEntry, transcript: Transcript) -> None:
    """Recopie les informations utiles du transcript dans l'entrée d'index."""
    entry.extras["tranches"] = transcript.extras.get("tranches", 0)
    entry.extras["strategie"] = transcript.extras.get("strategie", "")
    if transcript.extras.get("pistes"):
        entry.extras["pistes"] = transcript.extras["pistes"]
    if transcript.extras.get("nettoyage"):
        entry.extras["nettoyage"] = transcript.extras["nettoyage"]
    if transcript.warnings:
        entry.extras["avertissements"] = list(transcript.warnings)


@dataclass
class ImportEntry:
    """Un fichier importe, son transcript et son etat."""

    id: str = ""
    at: str = ""
    title: str = ""
    source: str = ""
    seconds: float = 0.0
    words: int = 0
    model: str = ""
    cost: float = 0.0
    status: str = "ok"
    error: str = ""
    speakers: list[str] = field(default_factory=list)
    language: str = ""
    # « import » (fichier choisi par l'utilisateur) ou « appel » (enregistré par Vox).
    kind: str = KIND_IMPORT
    extras: dict = field(default_factory=dict)

    # --------------------------------------------------------------
    @property
    def path(self) -> Path:
        """Fichier audio d'origine (jamais copie par Vox)."""
        return Path(self.source).expanduser() if self.source else Path()

    @property
    def exists(self) -> bool:
        try:
            return bool(self.source) and self.path.is_file()
        except OSError:
            return False

    @property
    def when(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.at)
        except (TypeError, ValueError):
            return None

    @property
    def label(self) -> str:
        if self.title:
            return self.title
        if self.source:
            return Path(self.source).stem
        return "Import"

    def when_label(self) -> str:
        moment = self.when
        if moment is None:
            return self.at or "date inconnue"
        delta = (datetime.now().date() - moment.date()).days
        if delta == 0:
            prefix = "Aujourd'hui"
        elif delta == 1:
            prefix = "Hier"
        else:
            prefix = moment.strftime("%d/%m/%Y")
        return f"{prefix} · {moment.strftime('%H:%M:%S')}"

    def short_when(self) -> str:
        moment = self.when
        if moment is None:
            return self.at or "?"
        delta = (datetime.now().date() - moment.date()).days
        if delta == 0:
            return moment.strftime("%H:%M:%S")
        if delta == 1:
            return f"hier {moment.strftime('%H:%M')}"
        if moment.year == datetime.now().year:
            return moment.strftime("%d/%m %H:%M")
        return moment.strftime("%d/%m/%y %H:%M")

    def duration_label(self) -> str:
        total = round(self.seconds)
        if total < 60:
            return f"{self.seconds:.1f} s"
        hours, minutes = divmod(total // 60, 60)
        if hours:
            return f"{hours} h {minutes:02d}"
        return f"{minutes} min {total % 60:02d}"

    def speakers_label(self) -> str:
        if not self.speakers:
            return ""
        if len(self.speakers) == 1:
            return self.speakers[0]
        return f"{len(self.speakers)} locuteurs"

    def to_dict(self) -> dict:
        data = asdict(self)
        extras = data.pop("extras", None)
        if extras:
            data.update(extras)
        return {key: value for key, value in data.items() if value not in ("", None)}

    @classmethod
    def from_dict(cls, data: dict) -> ImportEntry:
        known = {f for f in cls.__dataclass_fields__ if f != "extras"}
        args = {key: value for key, value in data.items() if key in known}
        args["extras"] = {k: v for k, v in data.items() if k not in known}
        if not isinstance(args.get("speakers"), list):
            args["speakers"] = []
        return cls(**args)


# ----------------------------------------------------------------------
# Dossier et index
# ----------------------------------------------------------------------
def transcript_path(entry_id: str) -> Path:
    return library_transcripts_dir() / f"{entry_id}.json"


def _unique_id(moment: datetime) -> str:
    base = moment.strftime("%Y-%m-%d_%H-%M-%S")
    if not transcript_path(base).exists() and not any(
        entry.id == base for entry in load()
    ):
        return base
    for index in range(2, 100):
        candidate = f"{base}-{index}"
        if not transcript_path(candidate).exists():
            return candidate
    return f"{base}-{int(time.time() * 1000) % 100000}"


def save_transcript(entry_id: str, transcript: Transcript) -> Path:
    """Ecrit le transcript complet (source de verite des exports)."""
    path = transcript_path(entry_id)
    path.write_text(
        json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_transcript(entry_id: str) -> Transcript | None:
    return _read_transcript(transcript_path(entry_id))


def raw_path(entry_id: str) -> Path:
    """Transcript d'avant nettoyage editorial, conserve pour comparaison."""
    return library_transcripts_dir() / f"{entry_id}.brut.json"


def save_raw_copy(entry_id: str, transcript: Transcript) -> Path:
    path = raw_path(entry_id)
    path.write_text(
        json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_raw_copy(entry_id: str) -> Transcript | None:
    return _read_transcript(raw_path(entry_id))


def _read_transcript(path: Path) -> Transcript | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("Transcript illisible : %s", path)
        return None
    try:
        return Transcript.from_dict(data)
    except (TypeError, ValueError):
        log.warning("Transcript invalide : %s", path)
        return None


def _from_transcript(entry_id: str, transcript: Transcript) -> ImportEntry:
    entry = ImportEntry(
        id=entry_id,
        title=transcript.title or Path(transcript.source).stem,
        seconds=transcript.duration,
        words=_count_words(transcript),
        model=transcript.model,
        cost=transcript.cost,
        speakers=transcript.speaker_labels(),
        language=transcript.language,
    )
    preview = _preview(transcript)
    if preview:
        entry.extras["apercu"] = preview
    return entry


def add(
    transcript: Transcript | None,
    source: str | Path,
    *,
    title: str = "",
    status: str = "ok",
    error: str = "",
    elapsed: float = 0.0,
    at: str = "",
    kind: str = KIND_IMPORT,
    extras: dict | None = None,
) -> ImportEntry:
    """Range un import dans la bibliotheque et renvoie son entree."""
    moment = datetime.now()
    entry_id = _unique_id(moment)
    source_path = str(Path(source).expanduser())
    if transcript is None:
        entry = ImportEntry(
            id=entry_id,
            at=at or moment.isoformat(timespec="seconds"),
            title=title or Path(source_path).stem,
            source=source_path,
            status=status,
            error=error,
            kind=kind,
        )
    else:
        transcript.source = source_path
        if title:
            transcript.title = title
        save_transcript(entry_id, transcript)
        entry = _from_transcript(entry_id, transcript)
        entry.at = at or moment.isoformat(timespec="seconds")
        entry.source = source_path
        entry.status = status or "ok"
        entry.error = error
        entry.kind = kind
    if elapsed:
        entry.extras["duree_traitement"] = round(elapsed, 1)
    if extras:
        entry.extras.update(extras)
    if transcript is not None:
        _apply_extras(entry, transcript)
    append(entry)
    return entry


def replace_transcript(
    entry_id: str,
    transcript: Transcript,
    *,
    model: str = "",
    cost: float | None = None,
) -> ImportEntry | None:
    """Remplace le transcript d'une entree (retranscription, nettoyage)."""
    entry = get(entry_id)
    if entry is None:
        return None
    save_transcript(entry_id, transcript)
    entry.seconds = transcript.duration
    entry.words = _count_words(transcript)
    entry.speakers = transcript.speaker_labels()
    entry.language = transcript.language or entry.language
    if model:
        entry.model = model
    if cost is not None:
        entry.cost = round(entry.cost + cost, 8)
        entry.extras["retranscriptions"] = int(entry.extras.get("retranscriptions", 0)) + 1
    entry.status = "ok"
    entry.error = ""
    _apply_extras(entry, transcript)
    _update(entry)
    return entry


def refresh(entry_id: str, transcript: Transcript) -> ImportEntry | None:
    """Range le transcript corrige et recalcule les metadonnees de l'entree."""
    entry = get(entry_id)
    if entry is None:
        return None
    save_transcript(entry_id, transcript)
    entry.seconds = transcript.duration
    entry.words = _count_words(transcript)
    entry.speakers = transcript.speaker_labels()
    _update(entry)
    return entry


def append(entry: ImportEntry) -> None:
    """Ajoute une ligne a l'index (sans reecrire le fichier)."""
    try:
        with library_index_file().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("Index de la bibliotheque non ecrit : %s", exc)


def load(limit: int = 0) -> list[ImportEntry]:
    """Toutes les entrees, de la plus recente a la plus ancienne."""
    path = library_index_file()
    if not path.exists():
        return []
    entries: list[ImportEntry] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(ImportEntry.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
    except OSError as exc:
        log.warning("Index de la bibliotheque illisible : %s", exc)
        return []
    entries.sort(key=lambda item: item.at, reverse=True)
    return entries[:limit] if limit else entries


def get(entry_id: str) -> ImportEntry | None:
    return next((entry for entry in load() if entry.id == entry_id), None)


def _rewrite(entries: list[ImportEntry]) -> None:
    path = library_index_file()
    try:
        with path.open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("Index de la bibliotheque non reecrit : %s", exc)


def _update(updated: ImportEntry) -> None:
    entries = load()
    changed = False
    for index, entry in enumerate(entries):
        if entry.id == updated.id:
            entries[index] = updated
            changed = True
            break
    if changed:
        _rewrite(entries)


def delete(entry_id: str) -> bool:
    """Retire une entree et son transcript. Le fichier source reste intact."""
    removed = False
    for path in (transcript_path(entry_id), raw_path(entry_id)):
        with contextlib.suppress(OSError):
            path.unlink()
            removed = True
    entries = load()
    remaining = [entry for entry in entries if entry.id != entry_id]
    if len(remaining) != len(entries):
        _rewrite(remaining)
        removed = True
    return removed


def stats() -> dict:
    """Nombre d'imports, duree cumulee et cout connu."""
    entries = load()
    return {
        "count": len(entries),
        "seconds": sum(entry.seconds for entry in entries),
        "cost": round(sum(entry.cost for entry in entries), 6),
        "speakers": sum(len(entry.speakers) for entry in entries),
    }


__all__ = [
    "KIND_CALL",
    "KIND_IMPORT",
    "STATUS_LABELS",
    "ImportEntry",
    "add",
    "append",
    "delete",
    "get",
    "load",
    "load_raw_copy",
    "load_transcript",
    "raw_path",
    "refresh",
    "replace_transcript",
    "save_raw_copy",
    "save_transcript",
    "stats",
    "transcript_path",
]
