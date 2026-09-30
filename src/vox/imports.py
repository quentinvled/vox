"""Traitement d'un fichier audio importe : analyse, decoupage, diarisation, fusion.

Utilise par l'interface (bouton « Importer ») et par la ligne de commande
(`vox --import`). Chaque etape signale sa progression ; l'annulation est
cooperative via un `threading.Event`.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import audiofiles, routing
from .api import ApiError, Client, diarization_options
from .config import DEFAULT_DIARIZATION_MODEL, Settings, key_for
from .transcript import ChunkResult, Transcript, assemble, segments_from_payload

log = logging.getLogger("vox")

# Conteneurs essayes dans l'ordre : flac (sans perte, moitie d'un wav), puis wav.
FORMAT_FALLBACK = ("flac", "wav")
# Erreurs passageres : on retente avant d'abandonner une tranche.
RETRY_STATUSES = (429, 500, 502, 503, 504)
RETRY_DELAYS = (2.0, 6.0)
# Erreurs ou l'on retente avec un autre conteneur (le fournisseur refuse le flac).
FORMAT_STATUSES = (400, 415, 422)


class ImportCancelled(RuntimeError):
    """Import interrompu par l'utilisateur."""


@dataclass
class Progress:
    stage: str  # analyse | silences | decoupage | transcription | fusion | termine
    done: int = 0
    total: int = 0
    message: str = ""


@dataclass
class ImportResult:
    transcript: Transcript
    info: audiofiles.AudioInfo
    strategy: routing.Strategy
    model: str = ""
    chunks: int = 0
    warnings: list[str] = field(default_factory=list)
    elapsed: float = 0.0


ProgressFn = Callable[[Progress], None]


def process_file(
    path: Path | str,
    settings: Settings,
    *,
    model: str = "",
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    workdir: Path | str | None = None,
    max_workers: int | None = None,
    limit_seconds: float | None = None,
) -> ImportResult:
    """Transcrit et diarise un fichier, en s'adaptant a ce qu'il contient.

    `limit_seconds` ne traite que le debut du fichier : pratique pour un essai
    sur quelques minutes avant de payer l'heure entiere.
    """
    started = time.perf_counter()
    source = Path(path)
    if not source.exists():
        raise audiofiles.AudioError(f"Fichier introuvable : {source}")
    notify = progress or (lambda _progress: None)

    def check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            raise ImportCancelled("Import annulé")

    notify(Progress("analyse", 0, 0, "Analyse du fichier…"))
    info = audiofiles.probe(source)
    correlation = audiofiles.channel_correlation(source, info.duration) if info.stereo else None
    strategy = routing.analyse(info, correlation)
    resolved_model = (model or settings.diarization_model or DEFAULT_DIARIZATION_MODEL).strip()

    effective = info.duration
    if limit_seconds and limit_seconds > 0:
        effective = min(info.duration, float(limit_seconds))

    check_cancel()
    notify(Progress("silences", 0, 0, "Repérage des silences…"))
    silences = audiofiles.detect_silences(source, limit=effective)
    chunk_seconds = float(settings.import_chunk_seconds or routing.DEFAULT_CHUNK_SECONDS)
    chunks = audiofiles.plan_chunks(effective, silences, target=chunk_seconds)

    tasks = [(index, chunk) for index in range(len(strategy.tracks)) for chunk in chunks]
    total = len(tasks)
    done = 0
    lock = threading.Lock()
    warnings: list[str] = []
    results: list[ChunkResult] = []
    workers = max_workers or max(1, int(settings.import_parallel or 3))

    def report(stage: str, message: str) -> None:
        with lock:
            notify(Progress(stage, done, total, message))

    report("decoupage", f"{total} tranche(s) à traiter")
    client = Client("openrouter", key_for("openrouter", settings), timeout=900.0)
    folder = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="vox-import-"))
    folder.mkdir(parents=True, exist_ok=True)
    keep_folder = workdir is not None

    def run(track_index: int, chunk: audiofiles.Chunk) -> ChunkResult:
        nonlocal done
        check_cancel()
        track = strategy.tracks[track_index]
        result = ChunkResult(
            index=chunk.index,
            track=track_index,
            window_start=chunk.start,
            core_start=chunk.core_start,
            core_end=chunk.core_end,
            window_end=chunk.end,
            track_label=track.label,
            fixed_speaker=not track.diarize,
        )
        audio_format = FORMAT_FALLBACK[0]
        target = folder / f"piste{track_index}-tranche{chunk.index}.{audio_format}"
        audiofiles.extract(
            source,
            chunk.start,
            chunk.end - chunk.start,
            target,
            channel=track.channel,
            fmt=audio_format,
        )
        result.language = settings.language or ""
        try:
            transcription = _transcribe(
                client,
                target.read_bytes(),
                audio_format,
                resolved_model,
                settings,
                diarize=track.diarize,
                cancel=cancel,
            )
        except ApiError as exc:
            if exc.status not in FORMAT_STATUSES or audio_format == "wav":
                raise
            # Le fournisseur refuse le conteneur : on retente en wav.
            audio_format = "wav"
            target = folder / f"piste{track_index}-tranche{chunk.index}.wav"
            audiofiles.extract(
                source,
                chunk.start,
                chunk.end - chunk.start,
                target,
                channel=track.channel,
                fmt=audio_format,
            )
            transcription = _transcribe(
                client,
                target.read_bytes(),
                audio_format,
                resolved_model,
                settings,
                diarize=track.diarize,
                cancel=cancel,
            )
        result.segments = segments_from_payload(transcription.raw, track_label=track.label)
        result.model = transcription.model
        result.cost = transcription.cost or 0.0
        result.seconds = transcription.seconds or 0.0
        if transcription.language:
            result.language = transcription.language
        if not keep_folder:
            with contextlib.suppress(OSError):
                target.unlink()
        with lock:
            done += 1
            notify(
                Progress(
                    "transcription",
                    done,
                    total,
                    f"Tranche {done}/{total} — {track.label}",
                )
            )
        return result

    first_error: Exception | None = None
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vox-import") as pool:
            futures = {
                pool.submit(run, track_index, chunk): (track_index, chunk)
                for track_index, chunk in tasks
            }
            for future in futures:
                track_index, chunk = futures[future]
                try:
                    results.append(future.result())
                except ImportCancelled:
                    raise
                except Exception as exc:  # une tranche peut echouer sans tout perdre
                    log.warning(
                        "Tranche %s (piste %s) en echec : %s", chunk.index, track_index, exc
                    )
                    if first_error is None:
                        first_error = exc
                    warnings.append(
                        f"Tranche {chunk.index + 1} ({strategy.tracks[track_index].label}) : {exc}"
                    )
    finally:
        client.close()
        if not keep_folder:
            shutil.rmtree(folder, ignore_errors=True)

    check_cancel()
    if not results:
        raise first_error or audiofiles.AudioError("Aucune tranche n'a pu être transcrite")

    notify(Progress("fusion", total, total, "Assemblage du transcript…"))
    transcript = assemble(results, duration=effective, title=source.stem)
    transcript.model = resolved_model
    transcript.source = str(source)
    transcript.warnings.extend(warnings)
    transcript.extras.update(
        {
            "strategie": strategy.reason,
            "tranches": total,
            "canaux": info.channels,
        }
    )
    if effective < info.duration:
        transcript.extras["limite_secondes"] = round(effective, 1)
    notify(Progress("termine", total, total, "Terminé"))
    return ImportResult(
        transcript=transcript,
        info=info,
        strategy=strategy,
        model=resolved_model,
        chunks=total,
        warnings=warnings,
        elapsed=time.perf_counter() - started,
    )


# ----------------------------------------------------------------------
def _transcribe(
    client: Client,
    data: bytes,
    audio_format: str,
    model: str,
    settings: Settings,
    *,
    diarize: bool,
    cancel: threading.Event | None,
):
    """Appel STT avec repli `verbose_json` -> json simple, et retentes."""
    options = diarization_options(model) if diarize else {}
    verbose = {
        "response_format": "verbose_json",
        "timestamp_granularities": ["segment", "word"],
    }
    attempts: list[dict] = [
        {"audio_format": audio_format, **verbose},
        {"audio_format": audio_format},
    ]
    last: Exception | None = None
    for attempt in attempts:
        for pause in (0.0, *RETRY_DELAYS):
            if pause:
                if cancel is not None and cancel.is_set():
                    raise ImportCancelled("Import annulé")
                time.sleep(pause)
            try:
                return client.transcribe(
                    data,
                    model,
                    language=settings.language or None,
                    vocabulary=settings.vocabulary_prompt,
                    provider_options=options,
                    **attempt,
                )
            except ApiError as exc:
                last = exc
                if exc.status in RETRY_STATUSES:
                    continue
                if exc.status in FORMAT_STATUSES and not attempt.get("response_format"):
                    break  # inutile de retenter sans verbose_json
                if exc.status in FORMAT_STATUSES:
                    break  # on passe a l'essai suivant
                raise
            except Exception as exc:  # reseau : on retente
                last = exc
                continue
    raise last or ApiError("Transcription impossible")


__all__ = [
    "FORMAT_FALLBACK",
    "ImportCancelled",
    "ImportResult",
    "Progress",
    "process_file",
]
