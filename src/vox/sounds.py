"""Signaux sonores synthetises, ecrits sur disque puis joues par Windows.

Pourquoi des fichiers et pas la memoire : `winsound.PlaySound` leve
« Cannot play asynchronously from memory » quand on combine SND_MEMORY et
SND_ASYNC. En passant par un fichier (SND_FILENAME), la lecture est reellement
asynchrone et n'importe quel thread peut l'appeler sans bloquer l'interface.
"""

from __future__ import annotations

import contextlib
import io
import logging
import math
import struct
import wave
from pathlib import Path

try:
    import winsound
except ImportError:  # pragma: no cover - hors Windows
    winsound = None  # type: ignore[assignment]

from .paths import data_dir

SND_FILENAME = 0x00020000
SND_ASYNC = 0x0001
SND_NODEFAULT = 0x0002

log = logging.getLogger("vox")

_SAMPLE_RATE = 22050


def _render(notes: list[tuple[float, float]], volume: float = 0.55) -> bytes:
    """Genere un WAV a partir d'une suite de (frequence, duree en secondes)."""
    frames = bytearray()
    for freq, duration in notes:
        count = int(_SAMPLE_RATE * duration)
        attack = max(1, int(_SAMPLE_RATE * 0.008))
        release = max(1, int(_SAMPLE_RATE * 0.035))
        for index in range(count):
            if index < attack:
                envelope = index / attack
            elif index > count - release:
                envelope = max(0.0, (count - index) / release)
            else:
                envelope = 1.0
            phase = 2 * math.pi * freq * (index / _SAMPLE_RATE)
            # sinusoide + harmonique 2 : timbre plus rond qu'un bip pur
            value = math.sin(phase) + 0.18 * math.sin(2 * phase)
            clamped = max(-1.0, min(1.0, value * envelope * volume))
            frames += struct.pack("<h", int(clamped * 32767))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(_SAMPLE_RATE)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


# Deux notes montantes = « j'ecoute ». Deux notes descendantes = « j'ai fini ».
_SOUNDS: dict[str, bytes] = {
    "start": _render([(587.33, 0.090), (987.77, 0.150)]),
    "stop": _render([(880.0, 0.080), (587.33, 0.120)]),
    "done": _render([(783.99, 0.075), (1174.66, 0.130)]),
    "error": _render([(349.23, 0.120), (233.08, 0.200)]),
}

_files: dict[str, str] = {}


def sound_folder() -> Path:
    return data_dir() / "sons"


def ensure_files() -> dict[str, str]:
    """Ecrit les WAV sur disque (une seule fois) et renvoie leurs chemins."""
    if _files:
        return _files
    folder = sound_folder()
    with contextlib.suppress(OSError):
        folder.mkdir(parents=True, exist_ok=True)
    for name, payload in _SOUNDS.items():
        path = folder / f"{name}.wav"
        try:
            if not path.exists() or path.stat().st_size != len(payload):
                path.write_bytes(payload)
            _files[name] = str(path)
        except OSError as exc:
            log.warning("Signal %s non ecrit : %s", name, exc)
    return _files


def play(name: str) -> None:
    """Joue un signal sans bloquer. Utilisable depuis n'importe quel thread."""
    if winsound is None:
        return
    path = ensure_files().get(name)
    if not path:
        return
    try:
        winsound.PlaySound(path, SND_FILENAME | SND_ASYNC | SND_NODEFAULT)
    except Exception as exc:
        log.warning("Lecture du signal %s impossible : %s", name, exc)


def dump_wavs() -> list[str]:
    """Ecrit les signaux sur disque et renvoie les chemins (pour verification)."""
    return list(ensure_files().values())
