"""Entrées audio de la machine : micro et son du système (WASAPI loopback).

Deux usages :

* les indicateurs de la zone de notification (ce que Vox peut capturer) ;
* l'enregistrement d'appel : micro et son du système capturés en deux pistes.

Tout est détecté à la demande, **sans ouvrir de flux** : un appel à `detect()`
ne touche pas à l'audio, ne bloque rien et n'allume pas le micro. Le son du
système est lu via WASAPI loopback (Windows, `PyAudioWPatch`) ; les
applications qui jouent du son sont listées par `pycaw` quand il est là.

L'instance `PyAudio` est créée une fois puis réutilisée : le test peut donc
être refait en continu (toutes les quelques secondes) sans coût visible.
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

# Instance PyAudioWPatch réutilisée (créée au premier besoin, fermée à l'arrêt).
_wasapi_handle = None


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
def _handle():
    """Instance PyAudioWPatch partagée (ou None si indisponible)."""
    global _wasapi_handle
    if _wasapi_handle is None and sys.platform == "win32":
        try:
            import pyaudiowpatch as pyaudio

            _wasapi_handle = pyaudio.PyAudio()
        except Exception as exc:
            log.info("Capture WASAPI indisponible : %s", exc)
            _wasapi_handle = None
    return _wasapi_handle


def wasapi_loopback() -> dict | None:
    """Périphérique loopback par défaut (index inclus), ou None.

    Sert aussi à l'enregistrement d'appel : `index`, `rate` et `channels`
    sont exactement ce qu'il faut pour ouvrir le flux de capture.
    """
    handle = _handle()
    if handle is None:
        return None
    try:
        info = handle.get_default_wasapi_loopback()
    except Exception as exc:
        log.info("Pas de périphérique loopback : %s", exc)
        return None
    return {
        "index": int(info.get("index", -1)),
        "name": (info.get("name") or "").strip(),
        "rate": int(info.get("defaultSampleRate") or 48000),
        "channels": max(1, int(info.get("maxInputChannels") or 2)),
    }


def reset() -> None:
    """Oublie l'instance et l'état mémorisé (tests, changement de machine)."""
    global _wasapi_handle
    if _wasapi_handle is not None:
        with contextlib.suppress(Exception):
            _wasapi_handle.terminate()
        _wasapi_handle = None


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

    info = wasapi_loopback()
    if info is None:
        try:
            import pyaudiowpatch  # noqa: F401
        except Exception:
            detail = "composant de capture absent de cette version"
        else:
            detail = "aucune sortie audio détectée"
        return SourceStatus(KIND_SYSTEM, "Son du système", available=False, detail=detail)
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
    "reset",
    "status_dot",
    "system_status",
    "wasapi_loopback",
]
