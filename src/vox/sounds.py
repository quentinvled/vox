"""Signaux sonores synthetises, ecrits sur disque puis joues.

Sous Windows, `winsound.PlaySound` joue un fichier de facon asynchrone depuis
n'importe quel thread (combiner SND_MEMORY et SND_ASYNC y leve « Cannot play
asynchronously from memory », d'ou le passage par un fichier).

Ailleurs, on reutilise `sounddevice`, deja requis pour le micro : le WAV est
decode en memoire une fois puis joue de facon asynchrone.

Le timbre est celui d'une cloche douce : quelques partiels, deux oscillateurs
legerement desaccordes (chorus) et une enveloppe exponentielle, plus agreable
qu'un bip sinusoidal pur.
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

# Partiels : la fondamentale domine, les harmoniques s'eteignent plus vite.
_PARTIALS: tuple[tuple[float, float], ...] = (
    (1.0, 1.00),
    (2.0, 0.28),
    (3.0, 0.11),
    (4.02, 0.05),
)
# Deux oscillateurs desaccordes de 0,35 % donnent un chorus chaleureux.
_DETUNES: tuple[float, ...] = (1.0, 1.0035)
_NORM = sum(amplitude for _, amplitude in _PARTIALS) * len(_DETUNES)


def _render(
    notes: list[tuple[float, float, float, float]],
    volume: float = 0.58,
) -> bytes:
    """Genere un WAV depuis des (frequence, duree, amortissement, gain).

    L'amortissement est le coefficient de l'exponentielle : plus il est grand,
    plus la note s'eteint vite (10 = percussif, 6 = resonnant).
    """
    frames = bytearray()
    for freq, duration, decay, gain in notes:
        count = int(_SAMPLE_RATE * duration)
        attack = max(1, int(_SAMPLE_RATE * 0.012))
        release = max(1, int(_SAMPLE_RATE * 0.045))
        for index in range(count):
            t = index / _SAMPLE_RATE
            # Attaque douce (pas de clic), relachement lineaire jusqu'a zero.
            if index < attack:
                envelope = (index / attack) ** 1.5
            elif index > count - release:
                envelope = max(0.0, (count - index) / release)
            else:
                envelope = 1.0
            envelope *= math.exp(-decay * t)

            value = 0.0
            for ratio, amplitude in _PARTIALS:
                for detune in _DETUNES:
                    value += amplitude * math.sin(2 * math.pi * freq * ratio * detune * t)
            clamped = max(-1.0, min(1.0, value / _NORM * envelope * gain * volume))
            frames += struct.pack("<h", int(clamped * 32767))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(_SAMPLE_RATE)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


# Chaque motif a une direction reconnaissable a l'oreille, sans regarder l'ecran.
_SOUNDS: dict[str, bytes] = {
    # Quinte montante, ronde et tenue : « je t'ecoute ». C'est le son signature,
    # le seul volontairement present.
    "start": _render([(523.25, 0.110, 9.0, 1.20), (783.99, 0.230, 7.0, 1.05)]),
    # Retour au calme, plus court et plus discret : « j'arrete ».
    "stop": _render([(659.25, 0.070, 14.0, 0.75), (493.88, 0.105, 12.0, 0.60)]),
    # Deux notes hautes et claires : « c'est insere ».
    "done": _render([(880.00, 0.080, 12.0, 0.72), (1174.66, 0.150, 9.0, 0.58)]),
    # Grave et descendant : « il y a un probleme ».
    "error": _render([(311.13, 0.130, 11.0, 0.95), (246.94, 0.210, 8.0, 0.75)]),
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
    path = ensure_files().get(name)
    if not path:
        return
    if winsound is not None:
        try:
            winsound.PlaySound(path, SND_FILENAME | SND_ASYNC | SND_NODEFAULT)
        except Exception as exc:
            log.warning("Lecture du signal %s impossible : %s", name, exc)
        return
    _play_async(path, name)


# Echantillons decodes, mis en cache : le WAV ne change pas d'un lancement a
# l'autre, inutile de le relire a chaque bip.
_samples: dict[str, tuple] = {}


def _decode(path: str) -> tuple:
    import numpy as np

    with wave.open(path, "rb") as handle:
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return data, rate


def _play_async(path: str, name: str) -> None:
    try:
        import sounddevice as sd
    except Exception:  # pragma: no cover - sounddevice est une dependance
        return
    try:
        cached = _samples.get(name)
        if cached is None:
            cached = _decode(path)
            _samples[name] = cached
        # On coupe le bip precedent pour eviter toute superposition.
        sd.stop()
        sd.play(cached[0], cached[1])
    except Exception as exc:
        log.warning("Lecture du signal %s impossible : %s", name, exc)


def dump_wavs() -> list[str]:
    """Ecrit les signaux sur disque et renvoie les chemins (pour verification)."""
    return list(ensure_files().values())


__all__ = ["dump_wavs", "ensure_files", "play", "sound_folder"]
