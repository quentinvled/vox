"""Injection de texte dans la fenetre active.

Le module choisit l'implementation selon la plateforme, mais l'API publique est
identique partout :

  * Windows -> `injector_win` : SendInput + presse-papier Win32 ;
  * Linux   -> `injector_x11` : pynput + presse-papier du bureau.

Les appelants (`pipeline`, `app`) n'ont donc rien a savoir du systeme.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    from .injector_win import (
        InjectorError,
        get_clipboard_text,
        get_foreground_window_title,
        inject,
        paste_text,
        press_enter,
        set_clipboard_text,
        type_text,
    )

    BACKEND = "win32"
else:
    from .injector_x11 import (
        InjectorError,
        get_clipboard_text,
        get_foreground_window_title,
        inject,
        paste_text,
        press_enter,
        set_clipboard_text,
        type_text,
    )

    BACKEND = "x11"

__all__ = [
    "BACKEND",
    "InjectorError",
    "get_clipboard_text",
    "get_foreground_window_title",
    "inject",
    "paste_text",
    "press_enter",
    "set_clipboard_text",
    "type_text",
]
