"""Choix automatique du traitement d'un audio importe.

L'utilisateur ne doit jamais avoir a comprendre le pipeline : ce module prend
le profil du fichier (mono ou stereo, canaux identiques ou non, contexte
d'appel) et renvoie un plan de traitement explicite, applique sans poser de
question. Voir `docs/assistant-audio.md`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .audiofiles import DUPLICATE_CORRELATION, AudioInfo

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
    *,
    is_call: bool = False,
    labels: tuple[str, str] | None = None,
    max_speakers: int | None = None,
) -> Strategy:
    """Construit la strategie a partir du profil du fichier.

    `correlation` est le resultat de `channel_correlation()` : proche de 1.0,
    les canaux sont identiques (son mono duplique) et le fichier est traite
    comme du mono. `is_call` / `labels` viennent du contexte (enregistrement
    fait par Vox, detection d'appel) et servent uniquement a nommer les pistes.
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
    # Canaux identiques (son mono duplique) ou profil indecis : le mix mono
    # reste le choix sur — traiter deux canaux identiques comme deux pistes
    # donnerait la meme conversation deux fois.
    if correlation is None or correlation >= DUPLICATE_CORRELATION:
        reason = (
            "Canaux identiques (son mono dupliqué) : diarisation automatique."
            if correlation is not None
            else "Profil des canaux indécis : diarisation automatique du mix mono."
        )
        return Strategy(
            kind="mono",
            reason=reason,
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
    "Strategy",
    "Track",
    "analyse",
]
