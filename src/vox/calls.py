"""Appels enregistrés : micro (« Moi ») + son du système, réunis en un transcript.

Un appel produit deux pistes : le micro (ce que dit l'utilisateur) et le son du
système (ce que disent les interlocuteurs). Chaque piste est transcrite
séparément — la diarisation est ainsi « gratuite » pour le micro, et le canal
système est diarisé proprement — puis les deux transcripts sont recollés sur une
même ligne de temps, avec le décalage de démarrage des deux flux.

Quand tout passe par le micro (téléphone posé en haut-parleur devant
l'ordinateur, casque dont le son n'est pas capté…), la piste micro contient
tout le monde : elle est alors **diarisée** comme un fichier importé, pour
distinguer les voix.

Le module gère aussi la **robustesse** de l'enregistrement : les pistes sont
écrites en continu dans des `.pcm` bruts, une fiche de suivi (`<stamp>.json`)
accompagne chaque appel, et `recover_calls()` les reprend au démarrage si
l'application s'est arrêtée brutalement.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import imports
from .config import Settings
from .imports import Progress
from .paths import calls_dir
from .recorder import SILENCE_PEAK
from .transcript import Segment, Speaker, Transcript

log = logging.getLogger("vox")

MOI = "moi"

# Toutes les pistes sont écrites en 16 kHz mono int16 (format des API STT).
PART_RATE = 16000


def merge_tracks(
    mic: Transcript | None,
    system: Transcript | None,
    *,
    offset: float = 0.0,
    title: str = "",
) -> Transcript:
    """Recolle les deux pistes sur une même ligne de temps.

    `offset` = début du flux système − début du flux micro (secondes) : les
    segments du système sont décalés d'autant. Sans piste système, la piste
    micro est renvoyée telle quelle (son locuteur unique reste anonyme).
    """
    if mic is None and system is None:
        return Transcript(title=title)
    if system is None:
        seule = mic or Transcript()
        if title:
            seule.title = title
        if len(seule.speakers) == 1:
            # Une seule voix dans le micro : c'est l'utilisateur.
            seule.rename(seule.speakers[0].id, "Moi")
        elif len(seule.speakers) > 1:
            seule.warnings.append(
                "Tout est passé par le micro (téléphone sur haut-parleur ?) : "
                "les locuteurs sont estimés — renomme-les dans la bibliothèque."
            )
        return seule

    transcript = Transcript(title=title, language=mic.language if mic else system.language)

    if mic is not None:
        transcript.segments.extend(
            Segment(segment.start, segment.end, MOI, segment.text)
            for segment in mic.segments
        )
        transcript.speakers.append(Speaker(id=MOI, name="Moi"))

    renaming: dict[str, str] = {}
    for index, speaker in enumerate(system.speakers):
        new_id = f"sys:{speaker.id}"
        renaming[speaker.id] = new_id
        transcript.speakers.append(
            Speaker(id=new_id, name=speaker.name or f"Interlocuteur {index + 1}")
        )
    for segment in system.segments:
        transcript.segments.append(
            Segment(
                segment.start + offset,
                segment.end + offset,
                renaming.get(segment.speaker, f"sys:{segment.speaker}"),
                segment.text,
            )
        )

    transcript.segments.sort(key=lambda item: (item.start, item.end))

    mic_duration = mic.duration if mic else 0.0
    system_end = (system.duration if system else 0.0) + max(0.0, offset)
    transcript.duration = max(mic_duration, system_end, 0.0)

    transcript.cost = round(
        (mic.cost if mic else 0.0) + (system.cost if system else 0.0), 8
    )
    mic_model = mic.model if mic else ""
    system_model = system.model if system else ""
    if mic_model and system_model and mic_model != system_model:
        transcript.model = f"{mic_model} + {system_model}"
    else:
        transcript.model = mic_model or system_model

    for source in (mic, system):
        if source is not None and source.warnings:
            transcript.warnings.extend(source.warnings)
    if system is not None and system.speakers:
        transcript.extras["diarisation_systeme"] = len(system.speakers)
    return transcript


def process_call(
    mic_path: Path | str,
    system_path: Path | str | None,
    settings: Settings,
    *,
    offset: float = 0.0,
    title: str = "",
    progress: Callable[[Progress], None] | None = None,
    cancel: threading.Event | None = None,
) -> Transcript:
    """Transcrit les deux pistes d'un appel et les recolle en un transcript."""

    def forward(label: str):
        def _on(item: Progress) -> None:
            if progress is not None:
                message = f"{label} — {item.message}" if item.message else label
                progress(Progress(item.stage, item.done, item.total, message))
        return _on

    def cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    # Le micro est « moi » quand le système porte les interlocuteurs. S'il est
    # la seule piste (téléphone sur haut-parleur, système muet), il contient
    # tout le monde : on le diarise pour distinguer les voix.
    mic_diarize = bool(settings.import_diarize) and system_path is None
    micro = imports.process_file(
        mic_path,
        settings,
        diarize=mic_diarize,
        expected_speakers=(settings.import_speakers or None) if mic_diarize else None,
        progress=forward("micro"),
        cancel=cancel,
    )
    if cancelled():
        raise imports.ImportCancelled("Transcription annulée")

    system = None
    system_result = None
    if system_path:
        system_result = imports.process_file(
            system_path,
            settings,
            diarize=settings.import_diarize,
            expected_speakers=settings.import_speakers or None,
            progress=forward("interlocuteurs"),
            cancel=cancel,
        )
        system = system_result.transcript

    transcript = merge_tracks(micro.transcript, system, offset=offset, title=title)
    transcript.source = str(mic_path)
    transcript.extras["pistes"] = {
        "micro": str(mic_path),
        "systeme": str(system_path) if system_path else "",
    }
    transcript.extras["decalage"] = round(offset, 3)
    transcript.extras["tranches"] = micro.chunks + (system_result.chunks if system_path else 0)
    if system_path:
        strategie = "Appel : piste micro (Moi) + piste système (interlocuteurs)"
    elif mic_diarize:
        strategie = "Appel : tout passe par le micro, locuteurs estimés par diarisation"
    else:
        strategie = "Appel : piste micro seule"
    transcript.extras["strategie"] = strategie
    return transcript


# ----------------------------------------------------------------------
# Robustesse : pistes brutes et reprise apres un arret brutal
# ----------------------------------------------------------------------
@dataclass
class RecoveredCall:
    """Un appel interrompu, prêt à être transcrit au démarrage suivant."""

    title: str
    mic: Path
    system: Path | None
    offset: float
    meta: Path
    started: str = ""


def finalize_part(part: Path | str, target: Path | str) -> bool:
    """Enveloppe un `.pcm` brut (16 kHz mono int16) dans un WAV lisible."""
    source, destination = Path(part), Path(target)
    if not source.exists() or source.stat().st_size < 2:
        return False
    try:
        with wave.open(str(destination), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(PART_RATE)
            with source.open("rb") as stream:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    handle.writeframesraw(block[: len(block) - (len(block) % 2)])
    except (OSError, wave.Error) as exc:
        log.warning("Finalisation impossible (%s) : %s", source, exc)
        return False
    return destination.exists() and destination.stat().st_size > 44


def _chunks_peak(blocks) -> float:
    """Crête maximale d'une suite de blocs bruts, sans tout charger en mémoire."""
    peak = 0.0
    for raw in blocks:
        if len(raw) < 2:
            continue
        samples = np.frombuffer(raw[: len(raw) - (len(raw) % 2)], dtype=np.int16)
        if samples.size:
            peak = max(peak, float(np.max(np.abs(samples))) / 32768.0)
            if peak >= SILENCE_PEAK:
                break
    return peak


def media_has_speech(path: Path | str) -> bool:
    """Vrai si un `.pcm` ou un `.wav` contient autre chose que du silence."""
    source = Path(path)
    if not source.exists():
        return False
    try:
        if source.suffix.lower() == ".wav":
            with wave.open(str(source), "rb") as handle:
                return _chunks_peak(iter(lambda: handle.readframes(1 << 19), b"")) >= SILENCE_PEAK
        with source.open("rb") as stream:
            return _chunks_peak(iter(lambda: stream.read(1 << 20), b"")) >= SILENCE_PEAK
    except (OSError, wave.Error):
        return False


def write_sidecar(path: Path | str, payload: dict) -> None:
    """Écrit la fiche de suivi d'un appel (reprise après arrêt brutal)."""
    try:
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        log.warning("Fiche de suivi non écrite (%s) : %s", path, exc)


def read_sidecar(path: Path | str) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def delete_sidecar(path: Path | str) -> None:
    with contextlib.suppress(OSError):
        Path(path).unlink()


def _remove(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def recover_calls() -> list[RecoveredCall]:
    """Finalise les appels interrompus et les rend prêts à transcrire.

    Appelée au démarrage : chaque fiche de suivi restée en place (l'application
    s'est arrêtée avant la fin de la transcription) est reprise. Les `.pcm` sont
    enveloppés en WAV, les pistes muettes écartées, et les fichiers inutiles
    nettoyés — un enregistrement exploitable n'est jamais perdu.
    """
    folder = calls_dir()
    recovered: list[RecoveredCall] = []
    for meta_path in sorted(folder.glob("*.json")):
        data = read_sidecar(meta_path)
        if not data:
            continue
        stamp = str(data.get("stamp") or meta_path.stem)
        mic_wav = folder / f"{stamp}_micro.wav"
        system_wav = folder / f"{stamp}_systeme.wav"
        mic_part = folder / str(data.get("micro_part") or f"{stamp}_micro.pcm")
        system_part = folder / str(data.get("systeme_part") or "")

        if not mic_wav.exists() and finalize_part(mic_part, mic_wav):
            _remove(mic_part)
        if system_part and not system_wav.exists() and finalize_part(system_part, system_wav):
            _remove(system_part)

        mic_ok = media_has_speech(mic_wav)
        system_ok = media_has_speech(system_wav)
        if not mic_ok and not system_ok:
            _remove(mic_wav)
            _remove(system_wav)
            delete_sidecar(meta_path)
            continue

        title = str(data.get("titre") or f"Appel — {stamp}")
        recovered.append(
            RecoveredCall(
                title=f"{title} (récupéré)",
                # Une piste micro muette mais un système qui parle : c'est le
                # système qui portait tout le monde.
                mic=mic_wav if mic_ok else system_wav,
                system=system_wav if (mic_ok and system_ok) else None,
                offset=float(data.get("decalage") or 0.0),
                meta=meta_path,
                started=str(data.get("started") or ""),
            )
        )
    return recovered


__all__ = [
    "PART_RATE",
    "RecoveredCall",
    "delete_sidecar",
    "finalize_part",
    "media_has_speech",
    "merge_tracks",
    "process_call",
    "read_sidecar",
    "recover_calls",
    "write_sidecar",
]
