"""Tests du decoupage et de l'analyse des fichiers audio (ffmpeg)."""

from __future__ import annotations

import wave
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from vox import audiofiles
from vox.audiofiles import AudioError, plan_chunks

RATE = 16000


def _ffmpeg_available() -> bool:
    try:
        audiofiles.ffmpeg_path()
    except AudioError:
        return False
    return True


# Les tests de decodage reel sont ignores si ffmpeg n'est pas disponible.
needs_ffmpeg = pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg absent de la machine")


# ----------------------------------------------------------------------
# Decoupage (logique pure)
# ----------------------------------------------------------------------
def test_short_file_single_chunk() -> None:
    chunks = plan_chunks(300.0, [])
    assert len(chunks) == 1
    assert chunks[0].start == 0.0
    assert chunks[0].core_end == 300.0


def test_three_chunks_cover_and_overlap() -> None:
    chunks = plan_chunks(1800.0, [], target=600.0, minimum=240.0, maximum=840.0, overlap=2.5)
    assert len(chunks) == 3
    assert chunks[0].core_start == 0.0
    assert chunks[-1].core_end == 1800.0
    for previous, following in pairwise(chunks):
        assert previous.core_end == following.core_start
        assert following.start < previous.core_end  # recouvrement present
    assert all(chunk.core_end - chunk.core_start <= 840.0 for chunk in chunks)
    assert all(chunk.start >= 0.0 and chunk.end <= 1800.0 for chunk in chunks)


def test_cuts_snap_to_silence() -> None:
    silences = [(590.0, 610.0), (1180.0, 1220.0)]
    chunks = plan_chunks(1800.0, silences, target=600.0)
    assert chunks[0].core_end == pytest.approx(600.0)
    assert chunks[1].core_end == pytest.approx(1200.0)
    assert all(chunk.cut_on_silence for chunk in chunks)


def test_no_silence_still_covers_everything() -> None:
    chunks = plan_chunks(2000.0, [], target=600.0)
    assert chunks[0].core_start == 0.0
    assert chunks[-1].core_end == 2000.0
    for previous, following in pairwise(chunks):
        assert previous.core_end == following.core_start
    assert all(not chunk.cut_on_silence for chunk in chunks[1:])


def test_invalid_parameters() -> None:
    with pytest.raises(AudioError):
        plan_chunks(0.0, [])
    with pytest.raises(AudioError):
        plan_chunks(1000.0, [], target=600.0, minimum=600.0, maximum=300.0)


# ----------------------------------------------------------------------
# Decodage reel
# ----------------------------------------------------------------------
def _tone(seconds: float, frequency: float = 220.0, amplitude: float = 0.3) -> np.ndarray:
    time = np.arange(int(RATE * seconds)) / RATE
    return (amplitude * np.sin(2 * np.pi * frequency * time)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds), dtype=np.float32)


def _write_wav(path: Path, samples: np.ndarray, channels: int = 1) -> Path:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(pcm.tobytes())
    return path


@needs_ffmpeg
def test_probe_reads_duration_and_channels(tmp_path: Path) -> None:
    path = _write_wav(tmp_path / "simple.wav", np.concatenate([_tone(1.0), _silence(1.0)]))
    info = audiofiles.probe(path)
    assert info.channels == 1
    assert info.samplerate == RATE
    assert info.duration == pytest.approx(2.0, abs=0.2)
    assert not info.stereo


@needs_ffmpeg
def test_correlation_detects_duplicated_channels(tmp_path: Path) -> None:
    mono = _tone(2.0)
    path = _write_wav(tmp_path / "duplique.wav", np.repeat(mono, 2), channels=2)
    correlation = audiofiles.channel_correlation(path, 2.0)
    assert correlation is not None
    assert correlation > audiofiles.DUPLICATE_CORRELATION


@needs_ffmpeg
def test_correlation_detects_distinct_channels(tmp_path: Path) -> None:
    left = _tone(2.0, 220.0)
    right = _tone(2.0, 660.0)
    path = _write_wav(tmp_path / "stereo.wav", np.column_stack([left, right]).ravel(), channels=2)
    correlation = audiofiles.channel_correlation(path, 2.0)
    assert correlation is not None
    assert correlation < 0.5


@needs_ffmpeg
def test_detect_silences(tmp_path: Path) -> None:
    samples = np.concatenate([_tone(1.0), _silence(2.0), _tone(1.0)])
    path = _write_wav(tmp_path / "silences.wav", samples)
    silences = audiofiles.detect_silences(path)
    assert silences
    start, end = silences[0]
    assert start == pytest.approx(1.0, abs=0.3)
    assert end == pytest.approx(3.0, abs=0.3)


@needs_ffmpeg
def test_extract_writes_readable_chunk(tmp_path: Path) -> None:
    path = _write_wav(tmp_path / "source.wav", _tone(3.0))
    target = audiofiles.extract(path, 1.0, 1.5, tmp_path / "tranche.flac", fmt="flac")
    assert target.exists()
    assert target.stat().st_size > 500
    assert audiofiles.probe(target).duration == pytest.approx(1.5, abs=0.2)


@needs_ffmpeg
def test_extract_single_channel(tmp_path: Path) -> None:
    left = _tone(2.0, 220.0)
    right = _tone(2.0, 660.0)
    path = _write_wav(tmp_path / "stereo.wav", np.column_stack([left, right]).ravel(), channels=2)
    target = audiofiles.extract(path, 0.0, 1.0, tmp_path / "gauche.flac", channel=0, fmt="flac")
    assert target.exists()
    assert audiofiles.probe(target).channels == 1
