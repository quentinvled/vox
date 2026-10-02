"""Decodage et decoupage des fichiers audio importes (ffmpeg).

Vox ne depend pas d'un ffmpeg systeme : le binaire fourni par `imageio-ffmpeg`
est utilise en priorite, sinon celui du PATH. Toutes les commandes passent une
liste d'arguments (jamais de shell).

Le decoupage se fait aux silences quand c'est possible : une coupure au milieu
d'une phrase gene la transcription et peut fabriquer des locuteurs fantomes.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import proc

# Duree des extraits compares pour savoir si les deux canaux sont identiques.
CORRELATION_WINDOW = 20.0
# Au-dela, les canaux sont consideres comme le meme signal (son mono duplique).
DUPLICATE_CORRELATION = 0.98
# Deux canaux qui « parlent » en meme temps presque tout le temps et qui se
# ressemblent ne sont pas deux voix separees : c'est le meme son (mixage stereo
# d'un appel mono, micros d'une meme piece...). Mesure sur un vrai enregistrement
# d'appel : correlation 0,66-0,94 et co-activite 0,91-0,95 -> mono.
SAME_AUDIO_CORRELATION = 0.6
SAME_AUDIO_CO_ACTIVITY = 0.75
# Taille des trames d'activite (secondes).
ACTIVITY_FRAME = 0.02
# Silence detectable : en dessous de -35 dBFS pendant au moins 0,5 s.
SILENCE_DB = -35
SILENCE_MIN_SECONDS = 0.5

# Conteneurs d'extraction : flac est sans perte et deux fois plus leger que le
# wav, wav sert de repli si un fournisseur refuse le flac.
FORMATS: dict[str, tuple[str, list[str]]] = {
    "flac": ("flac", []),
    "wav": ("pcm_s16le", ["-f", "wav"]),
    "mp3": ("libmp3lame", []),
}

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


class AudioError(RuntimeError):
    """ffmpeg absent, fichier illisible ou extraction impossible."""


@dataclass(frozen=True)
class AudioInfo:
    duration: float
    channels: int
    samplerate: int
    codec: str = ""

    @property
    def stereo(self) -> bool:
        return self.channels >= 2


@dataclass(frozen=True)
class ChannelProfile:
    """Ressemblance des deux canaux d'un fichier stereo.

    `correlation` : correlation au meilleur decalage (~1.0 = meme signal).
    `co_activity` : part du temps vocal ou les deux canaux sont actifs ensemble.
    `exclusive` : part du temps vocal ou un seul canal est actif (deux vrais
    interlocuteurs separees ont une exclusivite elevee, un mixage mono non).
    """

    correlation: float | None
    co_activity: float | None
    exclusive: float | None
    windows: int = 0


@dataclass(frozen=True)
class Chunk:
    """Une tranche a envoyer a l'API.

    `start`/`end` definissent la fenetre envoyee (avec recouvrement) ;
    `core_start`/`core_end` la portion conservee dans le transcript final.
    """

    index: int
    start: float
    end: float
    core_start: float
    core_end: float
    cut_on_silence: bool = True


# ----------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    """Chemin de ffmpeg : binaire embarque (imageio-ffmpeg), sinon PATH."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        binary = shutil.which("ffmpeg")
        if not binary:
            raise AudioError(
                "ffmpeg introuvable. Installe le paquet « imageio-ffmpeg » "
                "(uv sync) ou ffmpeg sur le systeme."
            ) from None
        return binary


def _run(args: list[str], *, timeout: float, binary: bool = False):
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=not binary,
            errors=None if binary else "replace",
            timeout=timeout,
            check=False,
            # Sans cela, chaque ffmpeg ouvre une console qui clignote.
            **proc.no_window(),
        )
    except FileNotFoundError as exc:
        raise AudioError(f"ffmpeg introuvable : {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"ffmpeg a depasse {timeout:.0f} s") from exc


# ----------------------------------------------------------------------
def probe(path: Path | str) -> AudioInfo:
    """Duree, canaux, frequence et codec d'un fichier, via ffmpeg."""
    result = _run([ffmpeg_path(), "-hide_banner", "-i", str(path)], timeout=60)
    text = result.stderr or ""
    duration = 0.0
    if match := _DURATION_RE.search(text):
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    stream = next((line for line in text.splitlines() if ": Audio:" in line), "")
    if not stream:
        raise AudioError(f"Aucune piste audio detectee dans {Path(path).name}")

    codec = ""
    if match := re.search(r"Audio:\s*([A-Za-z0-9_]+)", stream):
        codec = match.group(1)
    samplerate = 0
    if match := re.search(r"(\d+)\s*Hz", stream):
        samplerate = int(match.group(1))
    channels = 1
    if match := re.search(r"(\d+)\s*channels", stream):
        channels = int(match.group(1))
    elif "stereo" in stream:
        channels = 2
    return AudioInfo(duration=duration, channels=channels, samplerate=samplerate, codec=codec)


def _pcm_pair(path: Path | str, start: float, seconds: float) -> tuple[np.ndarray, np.ndarray] | None:
    """Extrait un court extrait stereo brut (s16le, 8 kHz) et le separe."""
    result = _run(
        [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{seconds:.3f}",
            "-i",
            str(path),
            "-ac",
            "2",
            "-ar",
            "8000",
            "-f",
            "s16le",
            "-",
        ],
        timeout=120,
        binary=True,
    )
    raw = result.stdout or b""
    if len(raw) % 4:
        raw = raw[: len(raw) - (len(raw) % 4)]
    if len(raw) < 8000:  # moins d'une demi-seconde de signal
        return None
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    return data[0::2], data[1::2]


def _best_lag_correlation(left: np.ndarray, right: np.ndarray, rate: int) -> float | None:
    """Correlation maximale sur un petit decalage (+-10 ms)."""
    span = max(1, int(0.010 * rate))
    size = min(len(left), len(right))
    if size < rate // 4:
        return None
    left, right = left[:size], right[:size]
    best: float | None = None
    for lag in range(-span, span + 1):
        a = left[max(0, lag) : size + min(0, lag)]
        b = right[max(0, -lag) : size + min(0, -lag)]
        if len(a) < rate // 4 or a.std() < 1e-6 or b.std() < 1e-6:
            continue
        value = float(np.corrcoef(a, b)[0, 1])
        if np.isfinite(value) and (best is None or value > best):
            best = value
    return best


def channel_profile(path: Path | str, duration: float, *, windows: int = 3) -> ChannelProfile:
    """Profil des deux canaux : ressemblance et activite simultanee.

    Renvoie un profil vide (tout a None) si le fichier est trop silencieux ou
    trop court pour juger : l'appelant retombe alors sur un traitement mono.
    """
    length = min(CORRELATION_WINDOW, max(duration, 1.0))
    span = max(0.0, duration - length)
    starts = np.linspace(0.0, span, num=windows) if span > 0.5 else [0.0]
    correlations: list[float] = []
    both = only_left = only_right = union = 0.0
    used = 0

    for start in starts:
        pair = _pcm_pair(path, float(start), length)
        if pair is None:
            continue
        left, right = pair
        size = (min(len(left), len(right)) // int(ACTIVITY_FRAME * 8000)) * int(ACTIVITY_FRAME * 8000)
        if size <= 0:
            continue
        frame = int(ACTIVITY_FRAME * 8000)
        left_frames = np.sqrt((left[:size] ** 2).reshape(-1, frame).mean(axis=1))
        right_frames = np.sqrt((right[:size] ** 2).reshape(-1, frame).mean(axis=1))
        all_frames = np.concatenate([left_frames, right_frames])
        floor = float(np.percentile(all_frames, 10))
        peak = float(np.percentile(all_frames, 95))
        # Seuil d'activite : le plancher de bruit multiplie par 3, borne par le
        # niveau du signal (un son continu n'a pas de plancher de bruit).
        threshold = max(30.0, min(floor * 3.0, peak * 0.5), peak * 0.1)
        left_active = left_frames > threshold
        right_active = right_frames > threshold
        union += float((left_active | right_active).sum())
        both += float((left_active & right_active).sum())
        only_left += float((left_active & ~right_active).sum())
        only_right += float((right_active & ~left_active).sum())
        mask = np.repeat(left_active | right_active, frame)
        if mask.sum() > 8000:
            value = _best_lag_correlation(left[:size][mask], right[:size][mask], 8000)
            if value is not None:
                correlations.append(value)
        used += 1

    if not used or union <= 0:
        return ChannelProfile(None, None, None, used)
    correlation = float(np.median(correlations)) if correlations else None
    co_activity = both / union
    return ChannelProfile(
        correlation=correlation,
        co_activity=co_activity,
        exclusive=1.0 - co_activity,
        windows=used,
    )


def channel_correlation(path: Path | str, duration: float, *, windows: int = 3) -> float | None:
    """Correlation entre canaux : ~1.0 si les deux canaux sont identiques.

    Renvoie None si le fichier est trop silencieux ou trop court pour juger
    (l'appelant retombe alors sur un traitement mono classique).
    """
    return channel_profile(path, duration, windows=windows).correlation


def detect_silences(
    path: Path | str,
    *,
    noise_db: int = SILENCE_DB,
    min_seconds: float = SILENCE_MIN_SECONDS,
    limit: float | None = None,
    timeout: float = 1800,
) -> list[tuple[float, float]]:
    """Intervalles de silence (start, end) detectes par ffmpeg.

    `limit` borne l'analyse aux premieres secondes (utile pour un essai sur un
    extrait sans decoder tout le fichier).
    """
    args = [ffmpeg_path(), "-hide_banner", "-nostats"]
    if limit and limit > 0:
        args += ["-t", f"{limit:.3f}"]
    args += [
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_seconds}",
        "-f",
        "null",
        "-",
    ]
    result = _run(args, timeout=timeout)
    text = result.stderr or ""
    starts = [float(value) for value in _SILENCE_START_RE.findall(text)]
    ends = [float(value) for value in _SILENCE_END_RE.findall(text)]
    return list(zip(starts, ends, strict=False))


def _closest_silence(
    silences: list[tuple[float, float]], ideal: float, low: float, high: float
) -> float | None:
    """Milieu du silence le plus proche de `ideal` dans [low, high]."""
    best: tuple[float, float] | None = None
    for start, end in silences:
        middle = (start + end) / 2.0
        if not low <= middle <= high:
            continue
        distance = abs(middle - ideal)
        if best is None or distance < best[0]:
            best = (distance, middle)
    return best[1] if best else None


def plan_chunks(
    duration: float,
    silences: list[tuple[float, float]],
    *,
    target: float = 600.0,
    minimum: float = 240.0,
    maximum: float = 840.0,
    overlap: float = 2.5,
) -> list[Chunk]:
    """Decoupe `duration` en tranches, de preference dans les silences.

    `overlap` sert a donner du contexte a l'API de chaque cote de la coupure
    (et a raccorder les locuteurs) ; les segments hors `core` sont ecartes du
    transcript final, donc rien n'est compte deux fois.
    """
    if duration <= 0:
        raise AudioError("Fichier audio de duree nulle")
    if target <= 0 or minimum <= 0 or maximum < minimum:
        raise AudioError("Parametres de decoupage invalides")
    if duration <= maximum:
        return [Chunk(0, 0.0, duration, 0.0, duration)]

    cuts: list[tuple[float, bool]] = []
    previous = 0.0
    for _ in range(1000):  # garde-fou : jamais de boucle infinie
        if duration - previous <= maximum:
            break
        ideal = previous + target
        low = previous + minimum
        high = min(previous + maximum, duration)
        chosen = _closest_silence(silences, ideal, low, high)
        on_silence = chosen is not None
        if chosen is None:
            chosen = min(ideal, duration)
        if chosen <= previous + 1.0:  # securite : la coupe doit avancer
            chosen = min(previous + target, duration)
            on_silence = False
        cuts.append((chosen, on_silence))
        previous = chosen
    else:
        raise AudioError("Decoupage impossible (fichier trop long ou duree aberrante)")

    bounds = [0.0, *(cut for cut, _ in cuts), duration]
    flags = [True, *(on_silence for _, on_silence in cuts)]
    chunks: list[Chunk] = []
    for index in range(len(bounds) - 1):
        core_start, core_end = bounds[index], bounds[index + 1]
        chunks.append(
            Chunk(
                index=index,
                start=max(0.0, core_start - overlap),
                end=min(duration, core_end + overlap),
                core_start=core_start,
                core_end=core_end,
                cut_on_silence=flags[index],
            )
        )
    return chunks


def extract(
    path: Path | str,
    start: float,
    duration: float,
    destination: Path | str,
    *,
    channel: int | None = None,
    channels: int = 1,
    samplerate: int = 16000,
    fmt: str = "flac",
    bitrate: str = "96k",
) -> Path:
    """Ecrit une tranche mono (ou un canal precis) dans `destination`."""
    if fmt not in FORMATS:
        raise AudioError(f"Format inconnu : {fmt}")
    codec, extra = FORMATS[fmt]
    args = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(path),
    ]
    if channel is not None:
        args += ["-af", f"pan=mono|c0=c{channel}"]
    args += ["-ac", str(channels), "-ar", str(samplerate), "-c:a", codec]
    if fmt == "mp3":
        args += ["-b:a", bitrate]
    args += [*extra, str(destination)]
    _run(args, timeout=900)
    target = Path(destination)
    if not target.exists() or target.stat().st_size == 0:
        raise AudioError(f"Extraction vide ({fmt}) pour {Path(path).name}")
    return target


__all__ = [
    "CORRELATION_WINDOW",
    "DUPLICATE_CORRELATION",
    "FORMATS",
    "AudioError",
    "AudioInfo",
    "Chunk",
    "channel_correlation",
    "detect_silences",
    "extract",
    "ffmpeg_path",
    "plan_chunks",
    "probe",
]
