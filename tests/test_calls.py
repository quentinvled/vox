"""Tests du recollage des deux pistes d'un appel (micro + systeme)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from vox import calls, imports
from vox.config import Settings
from vox.transcript import Segment, Speaker, Transcript


def _mic() -> Transcript:
    return Transcript(
        segments=[
            Segment(0.0, 1.0, "t0:c0:s0", "Bonjour à tous."),
            Segment(10.0, 12.0, "t0:c0:s0", "Je te confirme."),
        ],
        speakers=[Speaker(id="t0:c0:s0")],
        duration=12.0,
        model="microsoft/mai-transcribe-2",
        cost=0.01,
        language="fr",
    )


def _system() -> Transcript:
    return Transcript(
        segments=[
            Segment(0.5, 3.0, "t0:c0:s0", "Salut, on commence."),
            Segment(3.5, 5.0, "t0:c0:s1", "Oui, très bien."),
        ],
        speakers=[Speaker(id="t0:c0:s0"), Speaker(id="t0:c0:s1")],
        duration=5.0,
        model="microsoft/mai-transcribe-2",
        cost=0.02,
        warnings=["petit souci"],
    )


def test_merge_tracks_offsets_and_labels() -> None:
    transcript = calls.merge_tracks(_mic(), _system(), offset=2.0, title="Appel test")

    assert transcript.title == "Appel test"
    assert transcript.speaker_labels() == [
        "Moi",
        "Interlocuteur 1",
        "Interlocuteur 2",
    ]
    # Ordre chronologique, segments systeme decales de 2 s.
    starts = [round(segment.start, 2) for segment in transcript.segments]
    assert starts == [0.0, 2.5, 5.5, 10.0]
    system_first = next(item for item in transcript.segments if "Salut" in item.text)
    assert system_first.start == 2.5
    assert system_first.speaker != calls.MOI
    assert transcript.cost == pytest.approx(0.03)
    assert transcript.duration == pytest.approx(12.0)
    assert transcript.warnings == ["petit souci"]
    assert transcript.extras["diarisation_systeme"] == 2


def test_merge_tracks_without_system_names_the_single_voice_me() -> None:
    transcript = calls.merge_tracks(_mic(), None, title="Note")
    assert transcript.title == "Note"
    # Une seule voix dans le micro : c'est l'utilisateur.
    assert transcript.speaker_labels() == ["Moi"]
    assert len(transcript.segments) == 2


def test_merge_tracks_without_system_warns_when_several_voices() -> None:
    """Téléphone sur haut-parleur : tout passe par le micro."""
    micro = _mic()
    micro.speakers.append(Speaker(id="t0:c0:s1"))
    transcript = calls.merge_tracks(micro, None, title="Appel")
    assert transcript.speaker_labels() == ["Locuteur 1", "Locuteur 2"]
    assert any("haut-parleur" in warning for warning in transcript.warnings)


def test_merge_tracks_keeps_known_system_speaker_names() -> None:
    system = _system()
    system.rename("t0:c0:s0", "Jonas")
    transcript = calls.merge_tracks(_mic(), system, offset=0.0)
    labels = transcript.speaker_labels()
    assert "Jonas" in labels
    assert "Interlocuteur 2" in labels


def test_merge_tracks_empty_returns_empty_transcript() -> None:
    transcript = calls.merge_tracks(None, None, title="Vide")
    assert transcript.title == "Vide"
    assert transcript.segments == []
    assert transcript.speakers == []


# ----------------------------------------------------------------------
# Diarisation adaptative : tout passe par le micro (téléphone sur haut-parleur)
# ----------------------------------------------------------------------
def _import_result(transcript: Transcript) -> imports.ImportResult:
    from vox import audiofiles, routing

    return imports.ImportResult(
        transcript=transcript,
        info=audiofiles.AudioInfo(duration=1.0, channels=1, samplerate=16000, codec="wav"),
        strategy=routing.Strategy(kind="mono", reason="test", tracks=()),
        model="m",
        chunks=1,
    )


def test_process_call_diarizes_the_mic_when_it_is_the_only_track(monkeypatch, tmp_path) -> None:
    seen: list[tuple[str, bool]] = []

    def _fake_process(path, _settings, **kwargs):
        seen.append((Path(path).name, bool(kwargs.get("diarize"))))
        return _import_result(Transcript())

    monkeypatch.setattr(calls.imports, "process_file", _fake_process)
    mic = tmp_path / "mic.wav"

    calls.process_call(mic, None, Settings())
    assert seen == [("mic.wav", True)]

    seen.clear()
    calls.process_call(mic, tmp_path / "systeme.wav", Settings())
    assert seen == [("mic.wav", False), ("systeme.wav", True)]

    seen.clear()
    calls.process_call(mic, None, Settings(import_diarize=False))
    assert seen == [("mic.wav", False)]


def test_process_call_writes_the_strategy(monkeypatch, tmp_path) -> None:
    def _fake_process(path, _settings, **_kwargs):
        return _import_result(Transcript(duration=1.0))

    monkeypatch.setattr(calls.imports, "process_file", _fake_process)
    transcript = calls.process_call(tmp_path / "mic.wav", None, Settings())
    assert "estimés" in transcript.extras["strategie"]


# ----------------------------------------------------------------------
# Robustesse : pistes brutes, finalisation, reprise
# ----------------------------------------------------------------------
def _tone_bytes(seconds: float) -> bytes:
    count = int(calls.PART_RATE * seconds)
    time_axis = np.arange(count, dtype=np.float32) / calls.PART_RATE
    return (np.sin(2 * np.pi * 440 * time_axis) * 8000).astype(np.int16).tobytes()


def test_finalize_part_wraps_raw_pcm(tmp_path) -> None:
    part = tmp_path / "micro.pcm"
    part.write_bytes(_tone_bytes(1.0))
    target = tmp_path / "micro.wav"

    assert calls.finalize_part(part, target)
    with wave.open(str(target), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == calls.PART_RATE
        assert handle.getnframes() == calls.PART_RATE
    assert calls.media_has_speech(target)
    assert not calls.finalize_part(tmp_path / "absent.pcm", tmp_path / "x.wav")


def test_media_has_speech_detects_silence(tmp_path) -> None:
    silence = tmp_path / "silence.pcm"
    silence.write_bytes(b"\x00\x00" * 8000)
    assert not calls.media_has_speech(silence)
    assert calls.media_has_speech(tmp_path / "absent.pcm") is False


def test_recover_calls_finalizes_and_groups(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOX_DATA_DIR", str(tmp_path / "donnees"))
    from vox.paths import calls_dir

    folder = calls_dir()
    stamp = "2026-10-02_15-04-11"
    (folder / f"{stamp}_micro.pcm").write_bytes(_tone_bytes(2.0))
    (folder / f"{stamp}_systeme.pcm").write_bytes(_tone_bytes(1.0))
    calls.write_sidecar(
        folder / f"{stamp}.json",
        {
            "stamp": stamp,
            "titre": "Appel — 02/10 15:04",
            "decalage": 0.25,
            "micro_part": f"{stamp}_micro.pcm",
            "systeme_part": f"{stamp}_systeme.pcm",
        },
    )

    recovered = calls.recover_calls()
    assert len(recovered) == 1
    item = recovered[0]
    assert item.title == "Appel — 02/10 15:04 (récupéré)"
    assert item.mic.name == f"{stamp}_micro.wav" and item.mic.exists()
    assert item.system is not None and item.system.name == f"{stamp}_systeme.wav"
    assert item.offset == pytest.approx(0.25)
    # Les .pcm ont disparu (le WAV est la copie durable), la fiche reste.
    assert not (folder / f"{stamp}_micro.pcm").exists()
    assert not (folder / f"{stamp}_systeme.pcm").exists()
    assert item.meta.exists()
    # Deuxième passage : toujours récupérable tant que la fiche est là.
    assert len(calls.recover_calls()) == 1


def test_recover_calls_discards_silent_recordings(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOX_DATA_DIR", str(tmp_path / "donnees"))
    from vox.paths import calls_dir

    folder = calls_dir()
    (folder / "s_micro.pcm").write_bytes(b"\x00\x00" * 32000)
    calls.write_sidecar(folder / "s.json", {"stamp": "s", "micro_part": "s_micro.pcm"})

    assert calls.recover_calls() == []
    assert not (folder / "s_micro.wav").exists()
    assert not (folder / "s.json").exists()


def test_sidecar_roundtrip(tmp_path) -> None:
    path = tmp_path / "fiche.json"
    calls.write_sidecar(path, {"stamp": "x", "decalage": 1.5})
    assert calls.read_sidecar(path) == {"stamp": "x", "decalage": 1.5}
    calls.delete_sidecar(path)
    assert calls.read_sidecar(path) is None
