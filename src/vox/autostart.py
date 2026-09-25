"""Lancement automatique au demarrage de la session (Windows et Linux).

Sur Windows, l'entree est ecrite dans la cle de registre `Run`. Sous Linux, on
depose un fichier `.desktop` dans `~/.config/autostart`, comme n'importe quelle
application de bureau.

Aucune dependance a Qt : ce module est aussi importe par l'installeur, qui ne
doit embarquer que la bibliotheque standard.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

from .paths import data_dir, project_root

try:
    import winreg  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - hors Windows
    winreg = None  # type: ignore[assignment]

AUTOSTART_NAME = "Vox"
_DESKTOP_FILE = "vox.desktop"


def launcher_command() -> str:
    """Commande a lancer au demarrage, entierement quotee."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}"'
    project = project_root()
    if sys.platform == "win32":
        venv_pythonw = project / ".venv" / "Scripts" / "pythonw.exe"
        interpreter = venv_pythonw if venv_pythonw.exists() else Path(sys.executable)
        launcher = data_dir() / "vox_autostart.cmd"
        launcher.write_text(
            "@echo off\r\n"
            f'cd /d "{project}"\r\n'
            f'start "" "{interpreter}" -m vox\r\n',
            encoding="utf-8",
        )
        return f'"{launcher}"'
    # Unix : un petit script shell, sans console ni terminal.
    launcher = data_dir() / "vox_autostart.sh"
    launcher.write_text(
        "#!/bin/sh\n"
        f'cd "{project}"\n'
        f'exec "{sys.executable}" -m vox\n',
        encoding="utf-8",
    )
    with contextlib.suppress(OSError):
        launcher.chmod(0o755)
    return f'"{launcher}"'


def _autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def desktop_entry_path() -> Path:
    return _autostart_dir() / _DESKTOP_FILE


def set_autostart(enabled: bool) -> None:
    """Inscrit ou retire Vox du demarrage automatique de la session."""
    if sys.platform == "win32":
        _set_windows(enabled)
    elif sys.platform.startswith("linux"):
        _set_linux(enabled)


def is_autostart_enabled() -> bool:
    if sys.platform == "win32":
        return _enabled_windows()
    if sys.platform.startswith("linux"):
        return desktop_entry_path().exists()
    return False


# ----------------------------------------------------------------------
# Windows
# ----------------------------------------------------------------------
def _set_windows(enabled: bool) -> None:
    if winreg is None:
        return
    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, launcher_command())
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, AUTOSTART_NAME)
    except OSError:
        pass


def _enabled_windows() -> bool:
    if winreg is None:
        return False
    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
        return True
    except OSError:
        return False


# ----------------------------------------------------------------------
# Linux (freedesktop)
# ----------------------------------------------------------------------
def _set_linux(enabled: bool) -> None:
    path = desktop_entry_path()
    if not enabled:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        return
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
    command = launcher_command().strip().strip('"')
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Vox\n"
        "Comment=Dictee vocale globale\n"
        f'Exec="{command}"\n'
        "Icon=vox\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    with contextlib.suppress(OSError):
        path.write_text(entry, encoding="utf-8")


__all__ = [
    "AUTOSTART_NAME",
    "desktop_entry_path",
    "is_autostart_enabled",
    "launcher_command",
    "set_autostart",
]
