"""Appels enregistrés : micro (« Moi ») + son du système, réunis en un transcript.

Un appel produit deux pistes : le micro (ce que dit l'utilisateur) et le son du
système (ce que disent les interlocuteurs). Chaque piste est transcrite
séparément — la diarisation est ainsi « gratuite » pour le micro, et le canal
système est diarisé proprement — puis les deux transcripts sont recollés sur une
même ligne de temps, avec le décalage de démarrage des deux flux.

Fonctions volontairement pures (hors `process_call`) pour être testables sans
audio : `merge_tracks` ne fait que recoller des segments.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from . import imports
from .config import Settings
from .imports import Progress
from .transcript import Segment, Speaker, Transcript

MOI = "moi"


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

    micro = imports.process_file(
        mic_path,
        settings,
        diarize=False,
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
    transcript.extras["strategie"] = (
        "Appel : piste micro (Moi) + piste système (interlocuteurs)"
        if system_path
        else "Note vocale : piste micro"
    )
    return transcript


__all__ = ["MOI", "merge_tracks", "process_call"]
