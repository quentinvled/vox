"""Autorisations macOS necessaires a Vox.

macOS protege trois choses dont Vox a besoin, et il les demande **par
application** :

  * **Accessibilite** : simuler des frappes et des raccourcis (insertion du
    texte). Sans elle, Vox ecrit dans le vide, sans la moindre erreur ;
  * **Surveillance de la saisie** : recevoir les touches pressees dans les
    autres applications (raccourci global, bascule Espace) ;
  * **Microphone** : macOS le demande de lui-meme au premier enregistrement.

Ce module ne fait que **lire** l'etat et ouvrir le bon panneau des Reglages
Systeme. Rien n'est demande en douce, et hors macOS toutes les fonctions
renvoient `None` ou une liste vide.
"""

from __future__ import annotations

import contextlib
import ctypes
import subprocess
import sys
from dataclasses import dataclass

IS_MAC = sys.platform == "darwin"

# Identifiants des panneaux « Confidentialite et securite » des Reglages Systeme.
PANEL_ACCESSIBILITY = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
PANEL_INPUT_MONITORING = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
PANEL_MICROPHONE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"

# kIOHIDAccessType* renvoyes par IOHIDCheckAccess.
_HID_GRANTED = 0
_HID_DENIED = 1


@dataclass(frozen=True)
class PermissionStatus:
    """Etat d'une autorisation, pret a etre affiche."""

    key: str
    label: str
    granted: bool | None  # None = impossible a determiner
    detail: str = ""

    @property
    def state(self) -> str:
        if self.granted is None:
            return "unknown"
        return "granted" if self.granted else "denied"


def _load(path: str):
    """Charge un framework systeme en restant silencieux en cas d'echec."""
    try:
        return ctypes.CDLL(path)
    except OSError:
        return None


def accessibility_trusted() -> bool | None:
    """Vrai si Vox peut simuler des frappes (autorisation « Accessibilite »)."""
    if not IS_MAC:
        return None
    framework = _load("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    if framework is None or not hasattr(framework, "AXIsProcessTrusted"):
        return None
    with contextlib.suppress(Exception):
        framework.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(framework.AXIsProcessTrusted())
    return None


def input_monitoring_status() -> bool | None:
    """Vrai si Vox recoit les frappes des autres applications.

    `IOHIDCheckAccess` existe depuis macOS 10.15 ; sur une version plus
    ancienne, la fonction est absente et l'etat reste inconnu.
    """
    if not IS_MAC:
        return None
    framework = _load("/System/Library/Frameworks/IOKit.framework/IOKit")
    if framework is None or not hasattr(framework, "IOHIDCheckAccess"):
        return None
    with contextlib.suppress(Exception):
        framework.IOHIDCheckAccess.argtypes = (ctypes.c_int,)
        framework.IOHIDCheckAccess.restype = ctypes.c_int
        # kIOHIDRequestTypeListenEvent = 1
        status = int(framework.IOHIDCheckAccess(1))
        if status == _HID_GRANTED:
            return True
        if status == _HID_DENIED:
            return False
        return None
    return None


def statuses() -> list[PermissionStatus]:
    """Les autorisations a montrer a l'utilisateur (vide hors macOS)."""
    if not IS_MAC:
        return []
    accessibility = accessibility_trusted()
    monitoring = input_monitoring_status()
    return [
        PermissionStatus(
            "accessibility",
            "Accessibilité",
            accessibility,
            "Nécessaire pour écrire le texte dans les autres applications.",
        ),
        PermissionStatus(
            "input_monitoring",
            "Surveillance de la saisie",
            monitoring,
            "Nécessaire pour entendre le raccourci clavier global.",
        ),
    ]


def missing() -> list[PermissionStatus]:
    """Les autorisations explicitement refusees (ou inconnues)."""
    return [item for item in statuses() if item.granted is not True]


def all_granted() -> bool:
    """Vrai uniquement si toutes les autorisations sont accordees."""
    items = statuses()
    return bool(items) and all(item.granted is True for item in items)


def open_settings(key: str) -> bool:
    """Ouvre le panneau des Reglages Systeme correspondant a `key`."""
    panel = {
        "accessibility": PANEL_ACCESSIBILITY,
        "input_monitoring": PANEL_INPUT_MONITORING,
        "microphone": PANEL_MICROPHONE,
    }.get(key, "")
    if not IS_MAC or not panel:
        return False
    try:
        subprocess.Popen(  # noqa: S603 - ouverture du panneau systeme, sans shell
            ["/usr/bin/open", panel],
            close_fds=True,
        )
    except OSError:
        return False
    return True


def summary(app_name: str = "Vox") -> str:
    """Phrase courte pour la notification du premier lancement."""
    items = statuses()
    if not items:
        return ""
    if all(item.granted is True for item in items):
        return ""
    missing_items = [item for item in items if item.granted is not True]
    if len(missing_items) == 1:
        besoin = f"l'autorisation « {missing_items[0].label} »"
    else:
        names = " et ".join(item.label for item in missing_items)
        besoin = f"les autorisations « {names} »"
    return (
        f"{app_name} a besoin de {besoin} pour fonctionner : "
        "Réglages Système → Confidentialité et sécurité, puis relance Vox."
    )


__all__ = [
    "IS_MAC",
    "PANEL_ACCESSIBILITY",
    "PANEL_INPUT_MONITORING",
    "PANEL_MICROPHONE",
    "PermissionStatus",
    "accessibility_trusted",
    "all_granted",
    "input_monitoring_status",
    "missing",
    "open_settings",
    "statuses",
    "summary",
]
