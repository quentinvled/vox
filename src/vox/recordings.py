"""Conservation locale des dictees : WAV sur disque + index JSONL.

Chaque enregistrement part sur le disque **avant** l'appel a l'API, pour que
rien ne soit perdu si le reseau tombe. L'index (`enregistrements.jsonl`) ajoute
les metadonnees une fois la transcription connue.

Statuts possibles :
    ok       transcription reussie
    vide     silence, rien d'exploitable
    court    plus court que `min_record_seconds`
    erreur   echec reseau ou API

Ce fichier est distinct de `history.jsonl`, qui ne journalise que les dictees
reussies et alimente les statistiques.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import recordings_dir, recordings_index_file

log = logging.getLogger("vox")

# En dessous, c'est un appui accidentel sur le raccourci : on ne garde rien.
MIN_KEPT_SECONDS = 0.3

STATUS_LABELS: dict[str, str] = {
    "ok": "Transcrit",
    "vide": "Silence",
    "court": "Trop court",
    "erreur": "Échec",
}


def _fmt_bytes(value: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} Go"


@dataclass
class Recording:
    """Une dictee : son fichier audio, son texte et ses mesures."""

    at: str = ""
    audio: str = ""
    seconds: float = 0.0
    status: str = "ok"
    text: str = ""
    transcript: str = ""
    model: str = ""
    cost: float = 0.0
    latency_ms: int = 0
    language: str | None = None
    error: str = ""
    extras: dict = field(default_factory=dict)

    # --------------------------------------------------------------
    @property
    def path(self) -> Path:
        return recordings_dir() / self.audio

    @property
    def exists(self) -> bool:
        return bool(self.audio) and self.path.exists()

    @property
    def words(self) -> int:
        return len(self.text.split())

    @property
    def when(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.at)
        except (TypeError, ValueError):
            return None

    def when_label(self) -> str:
        moment = self.when
        if moment is None:
            return self.at or "date inconnue"
        today = datetime.now().date()
        day = moment.date()
        delta = (today - day).days
        if delta == 0:
            prefix = "Aujourd'hui"
        elif delta == 1:
            prefix = "Hier"
        else:
            prefix = moment.strftime("%d/%m/%Y")
        return f"{prefix} · {moment.strftime('%H:%M:%S')}"

    def short_when(self) -> str:
        """Date compacte pour les listes etroites."""
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
        return f"{total // 60} min {total % 60:02d}"

    def preview(self, limit: int = 64) -> str:
        """Resume affiche dans la liste."""
        body = " ".join((self.text or "").split())
        if not body:
            body = self.error or STATUS_LABELS.get(self.status, self.status)
        return body if len(body) <= limit else body[: limit - 1] + "…"

    def to_dict(self) -> dict:
        data = asdict(self)
        extras = data.pop("extras", None)
        if extras:
            data.update(extras)
        return {key: value for key, value in data.items() if value not in ("", None)}

    @classmethod
    def from_dict(cls, data: dict) -> Recording:
        known = {f for f in cls.__dataclass_fields__ if f != "extras"}
        args = {key: value for key, value in data.items() if key in known}
        args["extras"] = {k: v for k, v in data.items() if k not in known}
        args.setdefault("at", data.get("at", ""))
        args.setdefault("audio", data.get("audio", ""))
        return cls(**args)


# ----------------------------------------------------------------------
# Dossier et index
# ----------------------------------------------------------------------
def audio_path(name: str) -> Path:
    return recordings_dir() / name


def _unique_name(moment: datetime) -> str:
    base = moment.strftime("%Y-%m-%d_%H-%M-%S")
    candidate = f"{base}.wav"
    if not audio_path(candidate).exists():
        return candidate
    for index in range(2, 100):
        candidate = f"{base}-{index}.wav"
        if not audio_path(candidate).exists():
            return candidate
    return f"{base}-{int(time.time() * 1000) % 100000}.wav"


def save_audio(wav: bytes, seconds: float, moment: datetime | None = None) -> str:
    """Ecrit le WAV et renvoie son nom de fichier ('' si non conserve)."""
    if not wav or seconds < MIN_KEPT_SECONDS:
        return ""
    name = _unique_name(moment or datetime.now())
    try:
        audio_path(name).write_bytes(wav)
    except OSError as exc:
        log.warning("Enregistrement non ecrit : %s", exc)
        return ""
    return name


def append(entry: Recording) -> None:
    """Ajoute une ligne a l'index (sans reecrire le fichier)."""
    try:
        with recordings_index_file().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("Index des enregistrements non ecrit : %s", exc)


def note(
    audio: str,
    status: str,
    *,
    seconds: float = 0.0,
    text: str = "",
    transcript: str = "",
    model: str = "",
    cost: float = 0.0,
    latency_ms: int = 0,
    language: str | None = None,
    error: str = "",
    at: str = "",
) -> Recording:
    """Construit puis enregistre une entree d'index."""
    entry = Recording(
        at=at or datetime.now().isoformat(timespec="seconds"),
        audio=audio,
        seconds=seconds,
        status=status,
        text=text,
        transcript=transcript,
        model=model,
        cost=cost,
        latency_ms=latency_ms,
        language=language,
        error=error,
    )
    append(entry)
    return entry


def load(limit: int = 0) -> list[Recording]:
    """Toutes les entrees, de la plus recente a la plus ancienne."""
    path = recordings_index_file()
    if not path.exists():
        return []
    entries: list[Recording] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(Recording.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
    except OSError as exc:
        log.warning("Index des enregistrements illisible : %s", exc)
        return []
    entries.sort(key=lambda item: item.at, reverse=True)
    return entries[:limit] if limit else entries


def _rewrite(entries: list[Recording]) -> None:
    path = recordings_index_file()
    try:
        with path.open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("Index des enregistrements non reecrit : %s", exc)


def update_text(audio: str, text: str, model: str = "", latency_ms: int = 0) -> None:
    """Remplace le texte d'une entree (apres une retranscription)."""
    entries = load()
    changed = False
    for entry in entries:
        if entry.audio != audio:
            continue
        entry.text = text
        entry.transcript = text
        entry.status = "ok"
        entry.error = ""
        if model:
            entry.model = model
        if latency_ms:
            entry.latency_ms = latency_ms
        changed = True
    if changed:
        _rewrite(entries)


def delete(audio: str) -> bool:
    """Supprime le WAV et son entree d'index."""
    removed = False
    with contextlib.suppress(OSError):
        audio_path(audio).unlink()
        removed = True
    entries = load()
    remaining = [entry for entry in entries if entry.audio != audio]
    if len(remaining) != len(entries):
        _rewrite(remaining)
        removed = True
    return removed


def prune(days: int) -> int:
    """Supprime les enregistrements plus vieux que `days` jours (0 = rien)."""
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    entries = load()
    kept: list[Recording] = []
    removed = 0
    for entry in entries:
        path = entry.path
        try:
            too_old = path.exists() and path.stat().st_mtime < cutoff
        except OSError:
            too_old = False
        if too_old:
            with contextlib.suppress(OSError):
                path.unlink()
            removed += 1
            continue
        kept.append(entry)
    # Fichiers orphelins (WAV sans ligne d'index).
    known = {entry.audio for entry in kept}
    with contextlib.suppress(OSError):
        for path in recordings_dir().glob("*.wav"):
            if path.name in known:
                continue
            with contextlib.suppress(OSError):
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
    if removed:
        _rewrite(kept)
        log.info("%d enregistrement(s) purge(s) (> %d jours)", removed, days)
    return removed


def stats() -> dict:
    """Nombre d'entrees, taille sur disque, duree cumulee."""
    entries = load()
    total = 0
    with contextlib.suppress(OSError):
        total = sum(path.stat().st_size for path in recordings_dir().glob("*.wav"))
    return {
        "count": len(entries),
        "bytes": total,
        "size": _fmt_bytes(total),
        "seconds": sum(entry.seconds for entry in entries),
    }


def human_size(value: float) -> str:
    return _fmt_bytes(value)


__all__ = [
    "MIN_KEPT_SECONDS",
    "STATUS_LABELS",
    "Recording",
    "append",
    "audio_path",
    "delete",
    "human_size",
    "load",
    "note",
    "prune",
    "save_audio",
    "stats",
    "update_text",
]
