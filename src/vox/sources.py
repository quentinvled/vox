"""Entrées audio de la machine : micro et son du système (WASAPI loopback).

Deux usages :

* les indicateurs de la zone de notification (ce que Vox peut capturer) ;
* la préparation de l'enregistrement d'appel : micro et son du système seront
  capturés en deux pistes séparées.

Tout est détecté à la demande, **sans ouvrir de flux** : un appel à `detect()`
ne touche pas à l'audio, ne bloque rien et n'allume pas le micro. Le son du
système est lu via WASAPI loopback (Windows, `PyAudioWPatch`) ; les
applications qui jouent du son sont listées par `pycaw` quand il est là.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from dataclasses import dataclass

import sounddevice as sd

log = logging.getLogger("vox")

KIND_MIC = "mic"
KIND_SYSTEM = "system"

# Processus à ignorer dans la liste des applications qui jouent du son.
_IGNORED_APPS = {
    "vox",
    "audiodg",
    "svchost",
    "system sounds",
    "sons système",
    "windows",
}


@dataclass(frozen=True)
class SourceStatus:
    """État d'une entrée audio, prêt à être affiché."""

    kind: str
    label: str
    name: str = ""
    available: bool = False
    detail: str = ""
    active: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        """Ligne courte, pour le menu ou une notification."""
        if not self.available:
            return f"{self.label} : indisponible — {self.detail}"
        text = f"{self.label} : {self.name}"
        if self.detail:
            text += f" ({self.detail})"
        if self.active:
            text += f" · son en cours : {', '.join(self.active)}"
        return text


# ----------------------------------------------------------------------
# Micro
# ----------------------------------------------------------------------
def mic_status(device: int | None = None) -> SourceStatus:
    """Micro choisi dans les réglages, ou micro par défaut du système."""
    if device is not None:
        try:
            info = sd.query_devices(device)
        except Exception:
            fallback = mic_status(None)
            return SourceStatus(
                KIND_MIC,
                "Micro",
                name=fallback.name,
                available=fallback.available,
                detail="périphérique des réglages introuvable — micro par défaut",
            )
    else:
        try:
            info = sd.query_devices(kind="input")
        except Exception as exc:
            return SourceStatus(
                KIND_MIC, "Micro", available=False, detail=f"introuvable ({exc})"
            )

    channels = int(info.get("max_input_channels") or 0)
    if channels < 1:
        return SourceStatus(KIND_MIC, "Micro", available=False, detail="aucune entrée")

    name = (info.get("name") or "Micro").strip() or "Micro"
    try:
        host = sd.query_hostapis(info["hostapi"])["name"]
    except Exception:
        host = ""
    parts = [host, f"{channels} " + ("canaux" if channels > 1 else "canal")]
    detail = " · ".join(part for part in parts if part)
    return SourceStatus(KIND_MIC, "Micro", name=name, available=True, detail=detail)


# ----------------------------------------------------------------------
# Son du système (WASAPI loopback)
# ----------------------------------------------------------------------
def _wasapi_loopback() -> dict | None:
    """Périphérique WASAPI loopback par défaut, ou None."""
    if sys.platform != "win32":
        return None
    try:
        import pyaudiowpatch as pyaudio
    except Exception as exc:  # composant absent (Linux, build incomplet)
        log.info("Capture WASAPI indisponible : %s", exc)
        return None

    audio = None
    try:
        audio = pyaudio.PyAudio()
        info = audio.get_default_wasapi_loopback()
        return {
            "name": (info.get("name") or "").strip(),
            "rate": int(info.get("defaultSampleRate") or 48000),
            "channels": int(info.get("maxInputChannels") or 2),
        }
    except Exception as exc:
        log.info("Pas de périphérique loopback : %s", exc)
        return None
    finally:
        if audio is not None:
            with contextlib.suppress(Exception):
                audio.terminate()


def active_audio_apps() -> tuple[str, ...]:
    """Applications qui jouent du son à l'instant (Windows, via pycaw)."""
    if sys.platform != "win32":
        return ()
    try:
        from pycaw.utils import AudioUtilities
    except Exception:
        return ()

    try:
        sessions = list(AudioUtilities.GetAllSessions())
    except Exception:
        return ()

    names: list[str] = []
    for session in sessions:
        try:
            # 1 = session active (0 = inactive, 2 = expirée).
            if int(getattr(session, "State", 0)) != 1:
                continue
            process = session.Process
            raw = process.name() if process is not None else "Sons système"
        except Exception:
            continue
        pretty = str(raw).rsplit(".", 1)[0].strip()
        if not pretty or pretty.lower() in _IGNORED_APPS or pretty in names:
            continue
        names.append(pretty)
    return tuple(names[:3])


def system_status() -> SourceStatus:
    """Son du système : boucle WASAPI (Windows uniquement pour l'instant)."""
    if sys.platform != "win32":
        return SourceStatus(
            KIND_SYSTEM,
            "Son du système",
            available=False,
            detail="Windows pour l'instant",
        )
    try:
        import pyaudiowpatch  # noqa: F401
    except Exception:
        return SourceStatus(
            KIND_SYSTEM,
            "Son du système",
            available=False,
            detail="composant de capture absent de cette version",
        )

    info = _wasapi_loopback()
    if info is None:
        return SourceStatus(
            KIND_SYSTEM,
            "Son du système",
            available=False,
            detail="aucune sortie audio détectée",
        )
    return SourceStatus(
        KIND_SYSTEM,
        "Son du système",
        name=info["name"] or "Sortie par défaut",
        available=True,
        detail=f"WASAPI loopback · {info['rate']} Hz",
        active=active_audio_apps(),
    )


# ----------------------------------------------------------------------
# Vue d'ensemble
# ----------------------------------------------------------------------
def detect(device: int | None = None) -> list[SourceStatus]:
    """Les entrées audio à afficher : le micro, puis le son du système."""
    return [mic_status(device), system_status()]


def status_dot(statuses: list[SourceStatus]) -> str:
    """Pastille globale : tout est bon, il manque le système, ou rien."""
    if any(status.kind == KIND_MIC and not status.available for status in statuses):
        return "error"
    if any(not status.available for status in statuses):
        return "warn"
    return "ok"


__all__ = [
    "KIND_MIC",
    "KIND_SYSTEM",
    "SourceStatus",
    "active_audio_apps",
    "detect",
    "mic_status",
    "status_dot",
    "system_status",
]
