"""Lancement de programmes externes sans faire clignoter de console.

Windows n'aime pas les processus lances par une application sans console (Vox
est construite avec `console=False`) : chaque `ffmpeg`, `powershell` ou `cmd`
ouvre une fenetre noire le temps de son execution. Comme Vox en lance beaucoup
(un import = plusieurs appels ffmpeg), l'effet est tres visible.

`CREATE_NO_WINDOW` supprime cette fenetre. Sur les autres systemes, il n'y a
rien a faire : `no_window()` renvoie un dictionnaire vide, a etaler dans
l'appel :

    subprocess.run([...], capture_output=True, **proc.no_window())
"""

from __future__ import annotations

import subprocess
import sys

# 0x08000000, expose par `subprocess` uniquement sous Windows.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def no_window() -> dict:
    """Options `subprocess` a ajouter pour ne pas ouvrir de console."""
    if sys.platform == "win32":
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


__all__ = ["CREATE_NO_WINDOW", "no_window"]
