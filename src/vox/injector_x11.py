"""Injection de texte sous Linux : frappe et presse-papier.

`pynput` fournit a la fois la frappe de texte (y compris les caracteres
accentues, via un remappage de keysym) et la simulation de combinaisons, sans
privilege particulier sous X11.

Le presse-papier, lui, n'a pas d'API Python standard : on s'appuie sur les
outils du bureau quand ils sont presents (`wl-copy`/`wl-paste` sous Wayland,
`xclip` ou `xsel` sous X11). Si aucun n'est disponible, le collage retombe
automatiquement sur la frappe directe : l'insertion fonctionne quand meme, en
plus lent.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time


class InjectorError(RuntimeError):
    pass


# ----------------------------------------------------------------------
# Presse-papier : outils externes, du plus adapte au plus ancien
# ----------------------------------------------------------------------
def _has(program: str) -> bool:
    return shutil.which(program) is not None


def _wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _clipboard_writers() -> list[list[str]]:
    writers: list[list[str]] = []
    if _wayland() and _has("wl-copy"):
        writers.append(["wl-copy"])
    if _has("xclip"):
        writers.append(["xclip", "-selection", "clipboard"])
    if _has("xsel"):
        writers.append(["xsel", "--clipboard", "--input"])
    if not _wayland() and _has("wl-copy"):
        writers.append(["wl-copy"])
    return writers


def _clipboard_readers() -> list[list[str]]:
    readers: list[list[str]] = []
    if _wayland() and _has("wl-paste"):
        readers.append(["wl-paste", "--no-newline"])
    if _has("xclip"):
        readers.append(["xclip", "-selection", "clipboard", "-out"])
    if _has("xsel"):
        readers.append(["xsel", "--clipboard", "--output"])
    if not _wayland() and _has("wl-paste"):
        readers.append(["wl-paste", "--no-newline"])
    return readers


def clipboard_available() -> bool:
    """Vrai si un outil permet d'ecrire dans le presse-papier."""
    return bool(_clipboard_writers())


def get_clipboard_text() -> str:
    for command in _clipboard_readers():
        try:
            result = subprocess.run(command, capture_output=True, check=False)
        except OSError:
            continue
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace")
    return ""


def set_clipboard_text(text: str) -> None:
    data = text.encode("utf-8")
    for command in _clipboard_writers():
        try:
            result = subprocess.run(command, input=data, capture_output=True, check=False)
        except OSError:
            continue
        if result.returncode == 0:
            return
    raise InjectorError(
        "Aucun outil de presse-papier trouve (installe xclip, xsel ou wl-clipboard)."
    )


# ----------------------------------------------------------------------
# Clavier (pynput)
# ----------------------------------------------------------------------
def _controller():
    try:
        from pynput.keyboard import Controller
    except Exception as exc:  # pragma: no cover - depend de l'environnement
        raise InjectorError(f"Saisie clavier indisponible (pynput) : {exc}") from exc
    return Controller()


def _chunks(text: str, size: int) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)]


def type_text(text: str, per_chunk: int = 120, delay: float = 0.006) -> None:
    """Tape le texte caractere par caractere (independant du clavier)."""
    if not text:
        return
    controller = _controller()
    chunks = _chunks(text, per_chunk)
    for index, chunk in enumerate(chunks):
        controller.type(chunk)
        if delay and index < len(chunks) - 1:
            time.sleep(delay)


def _pynput():
    """Importe pynput en convertissant tout echec en InjectorError."""
    try:
        from pynput import keyboard
    except Exception as exc:
        raise InjectorError(f"Saisie clavier indisponible (pynput) : {exc}") from exc
    return keyboard


_MODIFIER_TOKENS = {"ctrl", "control", "shift", "alt", "win", "super", "cmd", "meta"}
_KEY_TOKENS = {"insert", "ins", "enter", "return", "tab", "space"}


def _press_combo(combo: str) -> None:
    names = [part.strip().lower() for part in combo.split("+") if part.strip()]
    if not names:
        raise InjectorError("Raccourci de collage vide.")

    # On valide d'abord la combinaison, independamment de pynput.
    modifiers = [name for name in names if name in _MODIFIER_TOKENS]
    finals = [name for name in names if name not in _MODIFIER_TOKENS]
    if not finals:
        raise InjectorError("Le raccourci de collage doit contenir une touche finale.")
    if len(finals) != 1 or not (len(finals[0]) == 1 or finals[0] in _KEY_TOKENS):
        raise InjectorError(f"Touche inconnue dans le raccourci : {finals[0]!r}")
    final_name = finals[0]

    keyboard = _pynput()
    Key = keyboard.Key
    modifier_map = {
        "ctrl": Key.ctrl,
        "control": Key.ctrl,
        "shift": Key.shift,
        "alt": Key.alt,
        "win": Key.cmd,
        "super": Key.cmd,
        "cmd": Key.cmd,
        "meta": Key.cmd,
    }
    key_map = {
        "insert": Key.insert,
        "ins": Key.insert,
        "enter": Key.enter,
        "return": Key.enter,
        "tab": Key.tab,
        "space": Key.space,
    }

    controller = keyboard.Controller()
    final = key_map.get(final_name, final_name)
    pressed = [modifier_map[name] for name in modifiers]
    for modifier in pressed:
        controller.press(modifier)
    try:
        controller.press(final)
        controller.release(final)
    finally:
        for modifier in reversed(pressed):
            controller.release(modifier)


def paste_text(text: str, paste_keys: str = "ctrl+v", restore: bool = True) -> None:
    """Copie le texte puis simule le raccourci de collage."""
    if not text:
        return
    if not clipboard_available():
        # Pas d'outil de presse-papier : on tape directement.
        type_text(text)
        return
    previous = get_clipboard_text() if restore else ""
    try:
        set_clipboard_text(text)
    except InjectorError:
        # L'outil de presse-papier est present mais a echoue : on tape le texte.
        type_text(text)
        return
    time.sleep(0.05)
    _press_combo(paste_keys)
    if restore:
        time.sleep(0.35)
        with contextlib.suppress(InjectorError):
            set_clipboard_text(previous)


def press_enter() -> None:
    keyboard = _pynput()
    controller = keyboard.Controller()
    controller.press(keyboard.Key.enter)
    controller.release(keyboard.Key.enter)


def inject(text: str, method: str = "paste", paste_keys: str = "ctrl+v") -> None:
    if not text:
        return
    if method == "type" or not clipboard_available():
        type_text(text)
    else:
        paste_text(text, paste_keys=paste_keys)


def get_foreground_window_title() -> str:
    """Titre de la fenetre active (X11 uniquement, sinon chaine vide)."""
    try:
        from Xlib import display
    except Exception:
        return ""
    try:
        connection = display.Display()
        try:
            window = connection.get_input_focus().focus
            name = window.get_wm_name()
        finally:
            connection.close()
        return name or ""
    except Exception:
        return ""


__all__ = [
    "InjectorError",
    "clipboard_available",
    "get_clipboard_text",
    "get_foreground_window_title",
    "inject",
    "paste_text",
    "press_enter",
    "set_clipboard_text",
    "type_text",
]
