"""Capture micro -> WAV en memoire (16 kHz mono, format attendu par les API STT)."""

from __future__ import annotations

import contextlib
import io
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd


class RecorderError(RuntimeError):
    pass


# Crete minimale (fraction de la pleine echelle) en dessous de laquelle on
# considere qu'il n'y a pas de parole. ~ -34 dBFS.
SILENCE_PEAK = 0.020


class Recorder:
    """Enregistreur non bloquant. Le niveau RMS est expose pour l'UI.

    `sink` ecrit les echantillons dans un fichier brut (`.pcm`, 16 kHz mono
    int16) au fil de l'eau : rien n'est garde en memoire, et un arret brutal de
    l'application ne perd pas l'enregistrement (il sera finalise au demarrage
    suivant). Sans `sink`, le comportement historique reste : tout en memoire,
    WAV renvoye par `stop()` (dictees courtes).
    """

    def __init__(
        self,
        samplerate: int = 16000,
        device: int | None = None,
        max_seconds: int = 300,
        sink: Path | str | None = None,
    ) -> None:
        self.samplerate = samplerate
        self.device = device
        self.max_seconds = max_seconds
        self.sink_path = Path(sink) if sink else None

        # Le peripherique reste ouvert entre les dictees (« micro chaud ») :
        # c'est l'ouverture de PortAudio qui coute cher, pas le demarrage de la
        # capture. On evite ainsi le delai ressenti a chaque appui.
        self._stream: sd.InputStream | None = None
        self._active = False
        self._frames: list[np.ndarray] = []
        self._sink = None
        self._samples_written = 0
        self.sink_error = False
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
        return self._active

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
        if not self._active:
            return
        chunk = indata.copy()
        if self._sink is not None:
            # Ecriture au fil de l'eau : rien en memoire, survit a un arret brutal.
            try:
                self._sink.write(chunk.tobytes())
                self._samples_written += int(chunk.size)
            except OSError:
                # Disque plein, fichier retire : on le signale, l'application
                # arrete proprement l'enregistrement (rien d'autre n'est perdu).
                self.sink_error = True
                with contextlib.suppress(Exception):
                    self._sink.close()
                self._sink = None
        else:
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
    def _open_stream(self) -> sd.InputStream:
        if self._stream is None:
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                dtype="int16",
                device=self.device,
                callback=self._callback,
            )
        return self._stream

    def warm(self) -> None:
        """Ouvre le peripherique a l'avance, sans capturer.

        L'ouverture de PortAudio est ce qui coute cher ; la faire au demarrage
        supprime le delai ressenti au premier appui sur le raccourci.
        """
        try:
            self._open_stream()
        except Exception:
            self._stream = None

    def configure(self, samplerate: int, device: int | None, max_seconds: int) -> None:
        """Met a jour les parametres ; rouvre le peripherique si necessaire."""
        if samplerate != self.samplerate or device != self.device:
            self._close_stream()
        self.samplerate = samplerate
        self.device = device
        self.max_seconds = max_seconds

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def close(self) -> None:
        """Libere le peripherique (a l'arret de l'application)."""
        self._active = False
        self._close_stream()

    def start(self) -> None:
        if self._active:
            return
        self._frames = []
        self._level = 0.0
        self._peak = 0.0
        self._max_peak = 0.0
        self._hit_max = False
        if self.sink_path is not None:
            try:
                self.sink_path.parent.mkdir(parents=True, exist_ok=True)
                self._sink = self.sink_path.open("wb", buffering=256 * 1024)
            except OSError as exc:
                self._sink = None
                raise RecorderError(f"Impossible d'écrire l'enregistrement ({exc}).") from exc
            self._samples_written = 0
        try:
            stream = self._open_stream()
            stream.start()
        except Exception as exc:
            self._close_stream()
            self._close_sink()
            raise RecorderError(
                f"Impossible d'ouvrir le micro ({exc}). Vérifie le périphérique "
                "d'entrée dans les réglages."
            ) from exc
        self._active = True
        self._started_at = time.monotonic()

    def _stop_stream(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()

    def stop(self) -> bytes:
        """Arrete la capture et renvoie le WAV (bytes).

        Avec un `sink`, rien n'est renvoye : les donnees sont deja sur le
        disque (fichier brut), l'appelant les finalise.
        """
        self._active = False
        self._stop_stream()
        frames, self._frames = self._frames, []
        self._level = 0.0
        self._close_sink()
        if self.sink_path is not None:
            return b""
        if not frames:
            return b""
        return _encode_wav(np.concatenate(frames, axis=0), self.samplerate)

    def cancel(self) -> None:
        self._active = False
        self._stop_stream()
        self._frames = []
        self._level = 0.0
        self._close_sink()

    def flush(self) -> None:
        """Force l'ecriture sur disque (appele regulierement pendant un appel)."""
        if self._sink is not None:
            with contextlib.suppress(Exception):
                self._sink.flush()

    def _close_sink(self) -> None:
        sink, self._sink = self._sink, None
        if sink is not None:
            with contextlib.suppress(Exception):
                sink.close()

    @property
    def written_seconds(self) -> float:
        """Duree reellement ecrite dans le fichier de sortie."""
        if self.samplerate <= 0:
            return 0.0
        return self._samples_written / float(self.samplerate)


def _encode_wav(samples: np.ndarray, samplerate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(samplerate)
        handle.writeframes(samples.tobytes())
    return buffer.getvalue()


def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Reechantillonne en int16 (interpolation lineaire, suffisant pour la parole)."""
    if samples.size == 0 or source_rate <= 0 or source_rate == target_rate:
        return samples
    count = round(samples.size * target_rate / source_rate)
    if count <= 1:
        return samples[:1]
    positions = np.linspace(0.0, samples.size - 1, samples.size, dtype=np.float64)
    targets = np.linspace(0.0, samples.size - 1, count, dtype=np.float64)
    return np.interp(targets, positions, samples.astype(np.float32)).astype(np.int16)


class StreamResampler:
    """Reechantillonnage en flux, bloc par bloc, sans memoire.

    Chaque bloc est converti vers `target_rate` en conservant entre les blocs le
    dernier echantillon et la position fractionnaire : le signal reste continu
    (aucun trou, aucun saut) et rien n'est accumule en memoire. C'est ce qui
    permet d'ecrire l'enregistrement au fil de l'eau.
    """

    def __init__(self, source_rate: int, target_rate: int) -> None:
        self.target_rate = target_rate
        self._step = float(source_rate) / float(target_rate) if target_rate else 1.0
        self._tail: float | None = None
        self._position = 0.0

    @property
    def identity(self) -> bool:
        return self._step == 1.0

    def process(self, samples: np.ndarray) -> np.ndarray:
        if samples.size == 0:
            return np.empty(0, dtype=np.int16)
        if self.identity:
            return samples.astype(np.int16, copy=False)

        block = samples.astype(np.float32, copy=False)
        if self._tail is None:
            self._tail = float(block[0])
            self._position = 0.0
        buffer = np.concatenate((np.array([self._tail], dtype=np.float32), block))
        last = buffer.size - 1

        if self._position >= last:
            self._position -= last
            self._tail = float(block[-1])
            return np.empty(0, dtype=np.int16)

        positions = np.arange(self._position, float(last), self._step, dtype=np.float64)
        if positions.size == 0:
            self._position -= last
            self._tail = float(block[-1])
            return np.empty(0, dtype=np.int16)
        lower = np.floor(positions).astype(np.int64)
        fraction = positions - lower
        values = buffer[lower] * (1.0 - fraction) + buffer[lower + 1] * fraction

        self._position = float(positions[-1]) + self._step - last
        self._tail = float(block[-1])
        return np.clip(values, -32768.0, 32767.0).astype(np.int16)


class SystemRecorder:
    """Capture le son du systeme (WASAPI loopback) en mono 16 kHz.

    Windows uniquement (PyAudioWPatch). Le flux est ouvert au demarrage ; le
    niveau RMS est expose pour l'UI, comme pour le micro. L'enregistrement sert
    aux appels : cette piste contient les interlocuteurs.

    Comme pour le micro, `sink` ecrit le PCM (16 kHz mono int16) au fil de
    l'eau : rien en memoire, et rien de perdu si l'application s'arrete mal.
    """

    def __init__(
        self,
        info: dict | None = None,
        target_rate: int = 16000,
        max_seconds: int = 4 * 3600,
        sink: Path | str | None = None,
    ) -> None:
        self.info = dict(info or {})
        self.target_rate = target_rate
        self.max_seconds = max_seconds
        self.sink_path = Path(sink) if sink else None
        self._pa = None
        self._stream = None
        self._active = False
        self._frames: list[np.ndarray] = []
        self._sink = None
        self._samples_written = 0
        self.sink_error = False
        self._resampler = StreamResampler(
            int(self.info.get("rate") or 48000), self.target_rate
        )
        self._started_at = 0.0
        self._level = 0.0
        self._max_peak = 0.0
        self._hit_max = False

    # ------------------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self._active

    @property
    def level(self) -> float:
        return self._level

    @property
    def has_speech(self) -> bool:
        return self._max_peak >= SILENCE_PEAK

    @property
    def hit_max(self) -> bool:
        return self._hit_max

    @property
    def available(self) -> bool:
        return bool(self.info)

    def elapsed(self) -> float:
        if not self._active:
            return 0.0
        return time.monotonic() - self._started_at

    # ------------------------------------------------------------------
    def _callback(self, in_data, _frame_count, _time_info, _status):
        if self._active:
            samples = np.frombuffer(in_data, dtype=np.int16)
            channels = max(1, int(self.info.get("channels") or 1))
            if channels > 1 and samples.size >= channels:
                samples = samples.reshape(-1, channels).mean(axis=1)
            chunk = self._resampler.process(samples.astype(np.int16))
            if chunk.size:
                if self._sink is not None:
                    try:
                        self._sink.write(chunk.tobytes())
                        self._samples_written += int(chunk.size)
                    except OSError:
                        self.sink_error = True
                        with contextlib.suppress(Exception):
                            self._sink.close()
                        self._sink = None
                else:
                    self._frames.append(chunk)
                floats = chunk.astype(np.float32) / 32768.0
                rms = float(np.sqrt(np.mean(np.square(floats))))
                peak = float(np.max(np.abs(floats)))
                self._level = max(rms, self._level * 0.75)
                self._max_peak = max(peak, self._max_peak)
            if self.elapsed() >= self.max_seconds:
                self._hit_max = True
        return (None, 0)  # pyaudio.paContinue

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._active:
            return
        if not self.info:
            raise RecorderError("Son du système indisponible sur cette machine.")
        try:
            import pyaudiowpatch as pyaudio
        except Exception as exc:  # composant absent du build
            raise RecorderError(
                "Capture du son du système indisponible dans cette version."
            ) from exc

        self._frames = []
        self._level = 0.0
        self._max_peak = 0.0
        self._hit_max = False
        self._resampler = StreamResampler(
            int(self.info.get("rate") or 48000), self.target_rate
        )
        if self.sink_path is not None:
            try:
                self.sink_path.parent.mkdir(parents=True, exist_ok=True)
                self._sink = self.sink_path.open("wb", buffering=256 * 1024)
            except OSError as exc:
                self._sink = None
                raise RecorderError(f"Impossible d'écrire l'enregistrement ({exc}).") from exc
            self._samples_written = 0
        try:
            self._pa = self._pa or pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=int(self.info.get("channels") or 2),
                rate=int(self.info.get("rate") or 48000),
                input=True,
                input_device_index=int(self.info.get("index", -1)),
                frames_per_buffer=1024,
                stream_callback=self._callback,
            )
            self._stream.start_stream()
        except Exception as exc:
            self._close_stream()
            self._close_sink()
            raise RecorderError(
                f"Impossible de capturer le son du système ({exc})."
            ) from exc
        self._active = True
        self._started_at = time.monotonic()

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            for method in ("stop_stream", "close"):
                with contextlib.suppress(Exception):
                    getattr(stream, method)()

    def stop(self) -> bytes:
        """Arrete la capture et renvoie le WAV (bytes), ou rien avec un `sink`."""
        self._active = False
        self._close_stream()
        frames, self._frames = self._frames, []
        self._level = 0.0
        self._close_sink()
        if self.sink_path is not None:
            return b""
        if not frames:
            return b""
        return _encode_wav(np.concatenate(frames, axis=0), self.target_rate)

    def cancel(self) -> None:
        self._active = False
        self._close_stream()
        self._frames = []
        self._level = 0.0
        self._close_sink()

    def flush(self) -> None:
        if self._sink is not None:
            with contextlib.suppress(Exception):
                self._sink.flush()

    def _close_sink(self) -> None:
        sink, self._sink = self._sink, None
        if sink is not None:
            with contextlib.suppress(Exception):
                sink.close()

    @property
    def written_seconds(self) -> float:
        if self.target_rate <= 0:
            return 0.0
        return self._samples_written / float(self.target_rate)

    def close(self) -> None:
        self.cancel()
        if self._pa is not None:
            with contextlib.suppress(Exception):
                self._pa.terminate()
            self._pa = None


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
