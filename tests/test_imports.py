"""Tests de bout en bout de l'import, avec un client STT simule (sans reseau)."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from vox import api, imports
from vox.api import ApiError
from vox.config import Settings

RATE = 16000


def _ffmpeg_available() -> bool:
    try:
        from vox import audiofiles

        audiofiles.ffmpeg_path()
    except Exception:
        return False
    return True


needs_ffmpeg = pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg absent de la machine")


def _click(seconds: float, frequency: float = 1.0) -> np.ndarray:
    """Signal « parle » : onde carree modulee, largement au-dessus du seuil."""
    index = np.arange(int(RATE * seconds), dtype=np.float32)
    carrier = np.where((index % 160) < 80, 0.4, -0.4)
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * index * frequency / RATE)
    return (carrier * envelope).astype(np.float32)


def _write_pattern(path: Path, pattern: list[tuple[str, float]]) -> Path:
    """Ecrit un WAV long bloc par bloc (voix / silence), sans tenir en memoire."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        for kind, total in pattern:
            remaining = total
            while remaining > 0:
                seconds = min(30.0, remaining)
                remaining -= seconds
                samples = _click(seconds) if kind == "voix" else np.zeros(int(RATE * seconds), np.float32)
                handle.writeframes((samples * 32767).astype("<i2").tobytes())
    return path


class FakeClient:
    """Client STT simule : appelle l'API par tranche, avec panne programmable."""

    calls: ClassVar[list[dict]] = []
    failures: ClassVar[dict[int, int]] = {}

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def close(self) -> None:
        pass

    def transcribe(self, data: bytes, model: str, **kwargs):
        index = len(self.calls)
        self.calls.append({"model": model, "bytes": len(data), **kwargs})
        status = self.failures.get(index)
        if status:
            raise ApiError("panne simulee", status=status)
        # Deux prises de parole bien a l'interieur du « coeur » de la tranche.
        # Sans diarisation demandee, le fournisseur ne renvoie pas de locuteur :
        # on imite ce comportement pour rester fidele au reel.
        diarized = bool(kwargs.get("provider_options"))
        first: dict = {"start": 30.0, "end": 32.0, "text": f"phrase {index} A"}
        second: dict = {"start": 40.0, "end": 42.0, "text": f"phrase {index} B"}
        if diarized:
            first["speaker"] = 0
            second["speaker"] = 1
        payload = {"segments": [first, second]}
        return api.Transcript(text=f"phrase {index}", model=model, seconds=30.0, cost=0.005, raw=payload)


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeClient.calls = []
    FakeClient.failures = {}


def _long_call(path: Path) -> Path:
    # 16 minutes : deux tranches (cible 600 s, plafond 840 s), coupe au silence.
    return _write_pattern(path, [("voix", 595.0), ("silence", 10.0), ("voix", 355.0)])


@needs_ffmpeg
def test_process_file_end_to_end(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(imports, "Client", FakeClient)
    source = _long_call(tmp_path / "appel.wav")
    settings = Settings(import_chunk_seconds=600, import_parallel=1, language="fr")
    steps = []
    result = imports.process_file(source, settings, progress=steps.append)

    assert result.chunks == 2
    assert len(FakeClient.calls) == 2
    assert result.transcript.cost == pytest.approx(0.01)
    assert result.transcript.duration == pytest.approx(960.0, abs=1.0)
    assert len(result.transcript.segments) == 4
    assert result.transcript.segments[0].start == pytest.approx(30.0, abs=0.1)
    assert max(segment.start for segment in result.transcript.segments) > 600
    assert next(iter(steps)).stage == "analyse"
    assert steps[-1].stage == "termine"
    # Sans prise de parole dans la zone de recouvrement, les etiquettes des deux
    # tranches ne peuvent pas etre raccordees par similarite : on prefere
    # sur-decouper (fusionnable en un clic) plutot que fusionner a tort.
    assert len(result.transcript.speakers) == 4


@needs_ffmpeg
def test_process_file_reports_progress_and_strategy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(imports, "Client", FakeClient)
    source = _long_call(tmp_path / "appel.wav")
    result = imports.process_file(source, Settings(import_parallel=1))
    assert "diarisation" in result.strategy.reason.lower()
    assert result.transcript.extras["tranches"] == 2
    assert result.model == "microsoft/mai-transcribe-2"


@needs_ffmpeg
def test_process_file_survives_permanent_chunk_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(imports, "Client", FakeClient)
    FakeClient.failures = {0: 404}  # erreur non rejouee : la tranche 0 est perdue
    source = _long_call(tmp_path / "appel.wav")
    result = imports.process_file(source, Settings(import_parallel=1))
    assert result.chunks == 2
    assert len(result.transcript.segments) == 2  # seule la tranche 1 a repondu
    assert result.transcript.warnings
    assert result.transcript.segments[0].start > 600


@needs_ffmpeg
def test_process_file_retries_transient_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(imports, "Client", FakeClient)
    FakeClient.failures = {0: 503}  # premiere tentative en echec, la suivante passe
    source = _long_call(tmp_path / "appel.wav")
    result = imports.process_file(source, Settings(import_parallel=1))
    assert len(result.transcript.segments) == 4
    assert len(FakeClient.calls) == 3  # 2 pour la tranche 0 (dont 1 essai), 1 pour la tranche 1


@needs_ffmpeg
def test_process_file_honours_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(imports, "Client", FakeClient)
    source = _long_call(tmp_path / "appel.wav")
    result = imports.process_file(source, Settings(import_parallel=1), limit_seconds=90.0)
    # Un essai sur 90 s ne doit payer qu'une tranche, pas les 16 minutes.
    assert result.chunks == 1
    assert len(FakeClient.calls) == 1
    assert result.transcript.duration == pytest.approx(90.0, abs=1.0)
    assert result.transcript.extras["limite_secondes"] == pytest.approx(90.0, abs=0.5)


@needs_ffmpeg
def test_process_file_skips_diarization_for_two_channels(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(imports, "Client", FakeClient)
    left = _click(3.0, 1.0)
    right = _click(3.0, 3.7)
    source = tmp_path / "stereo.wav"
    with wave.open(str(source), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes((np.column_stack([left, right]).ravel() * 32767).astype("<i2").tobytes())

    result = imports.process_file(source, Settings(import_parallel=1))
    # Deux pistes, deux appels STT, et aucune diarisation demandee a l'API.
    assert result.strategy.kind == "canaux"
    assert len(FakeClient.calls) == 2
    assert all(call["provider_options"] == {} for call in FakeClient.calls)
    assert result.transcript.speaker_labels() == ["Canal gauche", "Canal droit"]
