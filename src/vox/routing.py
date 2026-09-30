"""Choix automatique du traitement d'un audio importe.

L'utilisateur ne doit jamais avoir a comprendre le pipeline : ce module prend
le profil du fichier (mono ou stereo, canaux identiques ou non, contexte
d'appel) et renvoie un plan de traitement explicite, applique sans poser de
question. Voir `docs/assistant-audio.md`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .audiofiles import (
    DUPLICATE_CORRELATION,
    SAME_AUDIO_CO_ACTIVITY,
    SAME_AUDIO_CORRELATION,
    AudioInfo,
)

# Plafond de locuteurs : large, il evite le sur-decoupage sans interdire les
# grosses reunions. Les fournisseurs actuels ne l'exposent pas tous, il sert
# surtout de garde-fou documente.
DEFAULT_MAX_SPEAKERS = 8
# Un appel en tete-a-tete se sait : deux voix, pas trois.
CALL_MAX_SPEAKERS = 2
# Duree visee des tranches : courte devant les timeouts amont (~60 s) et les
# plafonds de diarisation (30-32 min), longue pour laisser du contexte.
DEFAULT_CHUNK_SECONDS = 600.0


@dataclass(frozen=True)
class ModelLimits:
    """Contraintes reelles d'un modele, mesurees sur de vrais fichiers."""

    chunk_seconds: float
    codec: str


# Limites mesurees le 30/09/2026 sur un appel de 97 min (OpenRouter) :
#   * mai-transcribe-2 refuse les entrees trop lourdes (~7-8 Mo) avec un 400
#     « does not support large audio inputs ». En flac (16 kHz mono), cela
#     plafonne vers 7 min ; en mp3 96 kb/s, 14 min passent encore.
#   * gemini-3.5-transcribe plafonne a 30 min avec diarisation (documentation).
MODEL_LIMITS: dict[str, ModelLimits] = {
    "microsoft/mai-transcribe-2": ModelLimits(chunk_seconds=720.0, codec="mp3"),
    "google/gemini-3.5-transcribe": ModelLimits(chunk_seconds=1500.0, codec="mp3"),
}


def limits_for(model: str) -> ModelLimits:
    """Limites du modele, ou valeurs par defaut raisonnables."""
    model = (model or "").strip()
    if model in MODEL_LIMITS:
        return MODEL_LIMITS[model]
    for known, limits in MODEL_LIMITS.items():
        if model.startswith(known.split(":", 1)[0] + ":"):
            return limits
    return ModelLimits(chunk_seconds=DEFAULT_CHUNK_SECONDS, codec="flac")


@dataclass(frozen=True)
class Track:
    """Une piste a traiter : un canal precis, ou le mix mono du fichier."""

    channel: int | None
    label: str
    diarize: bool
    max_speakers: int | None = None


@dataclass(frozen=True)
class Strategy:
    """Plan de traitement d'un fichier, et sa justification lisible."""

    kind: str  # "mono" | "canaux"
    reason: str
    tracks: tuple[Track, ...]
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS


def analyse(
    info: AudioInfo,
    correlation: float | None = None,
    co_activity: float | None = None,
    *,
    is_call: bool = False,
    labels: tuple[str, str] | None = None,
    max_speakers: int | None = None,
) -> Strategy:
    """Construit la strategie a partir du profil du fichier.

    `correlation` et `co_activity` viennent de `audiofiles.channel_profile()` :
    des canaux qui se ressemblent *et* qui parlent en meme temps portent le meme
    son (appel mono mixe en stereo) et doivent etre diarises ensemble, sinon on
    obtiendrait la meme conversation deux fois. `is_call` / `labels` viennent du
    contexte (enregistrement Vox, detection d'appel) et nomment les pistes.
    """
    cap = max_speakers if max_speakers and max_speakers > 0 else (
        CALL_MAX_SPEAKERS if is_call else DEFAULT_MAX_SPEAKERS
    )

    if not info.stereo:
        return Strategy(
            kind="mono",
            reason="Fichier mono : diarisation automatique des locuteurs.",
            tracks=(Track(channel=None, label="Locuteur", diarize=True, max_speakers=cap),),
        )
    # Canaux identiques (son mono duplique) ou presque : le mix mono reste le
    # choix sur.
    duplicated = correlation is not None and correlation >= DUPLICATE_CORRELATION
    same_audio = (
        correlation is not None
        and correlation >= SAME_AUDIO_CORRELATION
        and co_activity is not None
        and co_activity >= SAME_AUDIO_CO_ACTIVITY
    )
    if duplicated or same_audio:
        reason = (
            "Canaux identiques (son mono dupliqué) : diarisation automatique."
            if duplicated
            else "Même son sur les deux canaux : diarisation automatique."
        )
        return Strategy(
            kind="mono",
            reason=reason,
            tracks=(Track(channel=None, label="Locuteur", diarize=True, max_speakers=cap),),
        )
    if correlation is None:
        return Strategy(
            kind="mono",
            reason="Profil des canaux indécis : diarisation automatique du mix mono.",
            tracks=(Track(channel=None, label="Locuteur", diarize=True, max_speakers=cap),),
        )

    left, right = labels or ("Canal gauche", "Canal droit")
    return Strategy(
        kind="canaux",
        reason=(
            "Deux canaux distincts : un locuteur par piste. "
            "La diarisation d'un canal reste possible à la demande."
        ),
        tracks=(
            Track(channel=0, label=left, diarize=False),
            Track(channel=1, label=right, diarize=False),
        ),
    )


__all__ = [
    "CALL_MAX_SPEAKERS",
    "DEFAULT_CHUNK_SECONDS",
    "DEFAULT_MAX_SPEAKERS",
    "MODEL_LIMITS",
    "ModelLimits",
    "Strategy",
    "Track",
    "analyse",
    "limits_for",
]
