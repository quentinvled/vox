"""Capture micro -> WAV en memoire (16 kHz mono, format attendu par les API STT)."""

from __future__ import annotations

import io
import time
import wave

import numpy as np
import sounddevice as sd


class RecorderError(RuntimeError):
    pass


# Crete minimale (fraction de la pleine echelle) en dessous de laquelle on
# considere qu'il n'y a pas de parole. ~ -34 dBFS.
SILENCE_PEAK = 0.020


class Recorder:
    """Enregistreur non bloquant. Le niveau RMS est expose pour l'UI."""

    def __init__(
        self,
        samplerate: int = 16000,
        device: int | None = None,
        max_seconds: int = 300,
    ) -> None:
        self.samplerate = samplerate
        self.device = device
        self.max_seconds = max_seconds

        self._stream: sd.InputStream | None = None
        self._frames: list[np.ndarray] = []
        self._started_at = 0.0
        self._level = 0.0
        self._peak = 0.0
        self._max_peak = 0.0
        self._hit_max = False

    # ------------------------------------------------------------------
    @property
    def level(self) -> float:
        """Niveau RMS lisse, 0.0 -> 1.0."""
        return self._level

    @property
    def peak(self) -> float:
        return self._peak

    @property
    def max_peak(self) -> float:
        """Crete maximale depuis le debut (ne decroit pas)."""
        return self._max_peak

    @property
    def has_speech(self) -> bool:
        """Vrai si le signal depasse le plancher de bruit.

        Evite d'envoyer du silence a l'API : les modeles Whisper halucinent
        alors du texte (du chinois, « Sous-titres », etc.).
        """
        return self._max_peak >= SILENCE_PEAK

    @property
    def recording(self) -> bool:
        return self._stream is not None

    @property
    def hit_max(self) -> bool:
        """Vrai si l'enregistrement a ete coupe par la limite de duree."""
        return self._hit_max

    def elapsed(self) -> float:
        if not self._stream:
            return 0.0
        return time.monotonic() - self._started_at

    # ------------------------------------------------------------------
    def _callback(self, indata, _frames, _time_info, status) -> None:
        if self._stream is None:
            return
        chunk = indata.copy()
        self._frames.append(chunk)

        samples = chunk.astype(np.float32) / 32768.0
        if samples.size:
            rms = float(np.sqrt(np.mean(np.square(samples))))
            peak = float(np.max(np.abs(samples)))
            # lissage : montee rapide, descente douce
            self._level = max(rms, self._level * 0.75)
            self._peak = max(peak, self._peak * 0.9)
            self._max_peak = max(peak, self._max_peak)

        if self.elapsed() >= self.max_seconds:
            self._hit_max = True
            self.stop()

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._stream is not None:
            return
        self._frames = []
        self._level = 0.0
        self._peak = 0.0
        self._max_peak = 0.0
        self._hit_max = False
        try:
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                dtype="int16",
                device=self.device,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise RecorderError(
                f"Impossible d'ouvrir le micro ({exc}). Vérifie le périphérique "
                "d'entrée dans les réglages."
            ) from exc
        self._started_at = time.monotonic()

    # ------------------------------------------------------------------
    def _detach(self) -> list[np.ndarray]:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        frames, self._frames = self._frames, []
        return frames

    def stop(self) -> bytes:
        """Arrete et renvoie le WAV (bytes)."""
        frames = self._detach()
        self._level = 0.0
        if not frames:
            return b""
        return _encode_wav(np.concatenate(frames, axis=0), self.samplerate)

    def cancel(self) -> None:
        self._detach()
        self._level = 0.0


def _encode_wav(samples: np.ndarray, samplerate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(samplerate)
        handle.writeframes(samples.tobytes())
    return buffer.getvalue()


def list_input_devices() -> list[dict]:
    """Périphériques d'entrée utilisables, dédoublonnés par nom."""
    devices: list[dict] = []
    try:
        for index, info in enumerate(sd.query_devices()):
            if info.get("max_input_channels", 0) < 1:
                continue
            name = (info.get("name") or "").strip()
            if not name or name.lower() in {"input ()", "headset ()"}:
                continue
            devices.append(
                {
                    "index": index,
                    "name": name,
                    "samplerate": int(info.get("default_samplerate") or 44100),
                    "hostapi": sd.query_hostapis(info["hostapi"])["name"],
                }
            )
    except Exception:
        return []

    # On garde en priorite les entrees WASAPI, puis on dedoublonne par nom.
    devices.sort(key=lambda d: (0 if "WASAPI" in d["hostapi"] else 1, d["index"]))
    seen: set[str] = set()
    unique: list[dict] = []
    for device in devices:
        key = device["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(device)
    return unique
