"""Lancement automatique au demarrage de la session (Windows, macOS, Linux).

Sur Windows, l'entree est ecrite dans la cle de registre `Run`. Sous macOS, un
fichier `LaunchAgent` est depose dans `~/Library/LaunchAgents` (c'est le
mecanisme officiel, sans droit administrateur). Sous Linux, on depose un fichier
`.desktop` dans `~/.config/autostart`, comme n'importe quelle application de
bureau.

Aucune dependance a Qt : ce module est aussi importe par l'installeur, qui ne
doit embarquer que la bibliotheque standard.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

from .paths import data_dir, project_root

try:
    import winreg  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - hors Windows
    winreg = None  # type: ignore[assignment]

AUTOSTART_NAME = "Vox"
_DESKTOP_FILE = "vox.desktop"
LAUNCH_AGENT_LABEL = "fr.vled.vox"
_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{arguments}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
</dict>
</plist>
"""


def _unix_launcher_script() -> Path:
    """Petit script shell qui lance Vox sans console (macOS et Linux)."""
    launcher = data_dir() / "vox_autostart.sh"
    launcher.write_text(
        "#!/bin/sh\n"
        f'cd "{project_root()}"\n'
        f'exec "{sys.executable}" -m vox\n',
        encoding="utf-8",
    )
    with contextlib.suppress(OSError):
        launcher.chmod(0o755)
    return launcher


def launcher_argv() -> list[str]:
    """Commande de demarrage sous forme de liste (LaunchAgent macOS)."""
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        for parent in executable.parents:
            if parent.suffix == ".app":
                # `open -a` passe par LaunchServices : icone de barre de menus,
                # autorisations et activation se comportent comme un double-clic.
                return ["/usr/bin/open", "-a", str(parent)]
        return [str(executable)]
    return [str(_unix_launcher_script())]


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
    return f'"{_unix_launcher_script()}"'


def _autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def desktop_entry_path() -> Path:
    return _autostart_dir() / _DESKTOP_FILE


def launch_agent_path() -> Path:
    """Emplacement du LaunchAgent macOS."""
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def set_autostart(enabled: bool) -> None:
    """Inscrit ou retire Vox du demarrage automatique de la session."""
    if sys.platform == "win32":
        _set_windows(enabled)
    elif sys.platform == "darwin":
        _set_macos(enabled)
    elif sys.platform.startswith("linux"):
        _set_linux(enabled)


def is_autostart_enabled() -> bool:
    if sys.platform == "win32":
        return _enabled_windows()
    if sys.platform == "darwin":
        return launch_agent_path().exists()
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
# macOS (LaunchAgent)
# ----------------------------------------------------------------------
def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def plist_content(command: list[str], label: str = LAUNCH_AGENT_LABEL) -> str:
    """Contenu du LaunchAgent (fonction pure, testable hors macOS)."""
    arguments = "\n".join(
        f"        <string>{_xml_escape(part)}</string>" for part in command
    )
    return _PLIST_TEMPLATE.format(label=_xml_escape(label), arguments=arguments)


def _launchctl(*arguments: str) -> None:
    """Appelle launchctl en ignorant les erreurs (session, droits, version)."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(  # noqa: S603 - commande systeme fixe
            ["/bin/launchctl", *arguments],
            capture_output=True,
            check=False,
            timeout=10,
        )


def _set_macos(enabled: bool) -> None:
    path = launch_agent_path()
    domain = f"gui/{os.getuid()}"
    if not enabled:
        _launchctl("bootout", f"{domain}/{LAUNCH_AGENT_LABEL}")
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        return
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(plist_content(launcher_argv()), encoding="utf-8")
    except OSError:
        return
    # `bootstrap` est la commande moderne ; `load -w` couvre les anciens macOS.
    _launchctl("bootout", f"{domain}/{LAUNCH_AGENT_LABEL}")
    _launchctl("bootstrap", domain, str(path))
    if not _launch_agent_loaded(domain):
        _launchctl("load", "-w", str(path))


def _launch_agent_loaded(domain: str) -> bool:
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        result = subprocess.run(  # noqa: S603 - commande systeme fixe
            ["/bin/launchctl", "print", f"{domain}/{LAUNCH_AGENT_LABEL}"],
            capture_output=True,
            check=False,
            timeout=10,
        )
        return result.returncode == 0
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
    "LAUNCH_AGENT_LABEL",
    "desktop_entry_path",
    "is_autostart_enabled",
    "launch_agent_path",
    "launcher_argv",
    "launcher_command",
    "plist_content",
    "set_autostart",
]
