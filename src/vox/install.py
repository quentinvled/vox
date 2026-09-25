"""Installation et desinstallation de Vox (sans Qt).

Volontairement limite a la bibliotheque standard : ce module est importe par
l'installeur, qui ne doit surtout pas embarquer PySide6 pour rien.

L'installation va dans %LOCALAPPDATA%\\Programs\\Vox : aucun droit
administrateur n'est requis, et rien n'est ecrit ailleurs que dans le profil
de l'utilisateur.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import winreg
from pathlib import Path

from .paths import data_dir

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Vox"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SHORTCUT_NAME = "Vox.lnk"
PROCESS_NAME = "Vox.exe"


# ----------------------------------------------------------------------
# Chemins
# ----------------------------------------------------------------------
def install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / "Programs" / "Vox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def installed_exe() -> Path:
    return install_dir() / PROCESS_NAME


def desktop_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


def start_menu_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


# ----------------------------------------------------------------------
# Processus
# ----------------------------------------------------------------------
def _system_exe(name: str, *relative: str) -> str:
    """Chemin absolu d'un outil systeme (evite les chemins partiels)."""
    root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    candidate = Path(root).joinpath(*relative) if relative else Path(root) / "System32" / name
    if candidate.exists():
        return str(candidate)
    return shutil.which(name) or name


# --- enumeration des processus (Toolhelp32), sans dependance externe ---
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
INVALID_HANDLE = ctypes.c_void_p(-1).value


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def process_ids(name: str) -> list[tuple[int, int]]:
    """Renvoie [(pid, pid_parent)] pour les processus dont l'exe porte ce nom."""
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE:
        return []
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    found: list[tuple[int, int]] = []
    try:
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                if entry.szExeFile.lower() == name.lower():
                    found.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def stop_running(*, exclude_self: bool = True) -> int:
    """Arrete les instances de Vox. Renvoie le nombre de processus arretes.

    `exclude_self` est indispensable pour la desinstallation : le processus qui
    execute `Vox.exe --uninstall` s'appelle lui-meme Vox.exe, et un
    `taskkill /IM Vox.exe` le tuerait avant qu'il ait fait quoi que ce soit.
    On exclut donc son PID et celui de son parent (l'amorce PyInstaller).
    """
    protected: set[int] = set()
    if exclude_self:
        protected = {os.getpid(), os.getppid()}

    kernel32 = ctypes.windll.kernel32
    stopped = 0
    for pid, parent in process_ids(PROCESS_NAME):
        if pid in protected or parent in protected:
            continue
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            continue
        try:
            if kernel32.TerminateProcess(handle, 0):
                stopped += 1
        finally:
            kernel32.CloseHandle(handle)
    return stopped


# ----------------------------------------------------------------------
# Raccourcis (via PowerShell : c'est la seule API fiable sans dependance)
# ----------------------------------------------------------------------
_SHORTCUT_SCRIPT = """$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$targets = @({targets})
foreach ($path in $targets) {{
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = '{exe}'
    $link.WorkingDirectory = '{workdir}'
    $link.Description = 'Vox - dictee vocale (Ctrl + Maj)'
    $link.IconLocation = '{exe},0'
    $link.Save()
}}
"""


def create_shortcuts(exe: Path | None = None) -> list[Path]:
    """Cree les raccourcis Bureau et Menu Demarrer. Renvoie ceux qui existent."""
    target = exe or installed_exe()
    destinations = [desktop_dir() / SHORTCUT_NAME, start_menu_dir() / SHORTCUT_NAME]

    quoted = ", ".join(f"'{path}'" for path in destinations)
    script = _SHORTCUT_SCRIPT.format(targets=quoted, exe=target, workdir=target.parent)

    script_path = Path(os.environ.get("TEMP", ".")) / "vox-shortcuts.ps1"
    # UTF-8 avec BOM : PowerShell 5.1 ne lit l'UTF-8 sans BOM de facon fiable
    # que s'il est en ASCII pur.
    script_path.write_text(script, encoding="utf-8-sig")
    try:
        subprocess.run(  # noqa: S603 - chemin systeme resolu, script genere localement
            [
                _system_exe("powershell.exe", "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        script_path.unlink(missing_ok=True)

    return [path for path in destinations if path.exists()]


def remove_shortcuts() -> None:
    for path in (desktop_dir() / SHORTCUT_NAME, start_menu_dir() / SHORTCUT_NAME):
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


# ----------------------------------------------------------------------
# Registre
# ----------------------------------------------------------------------
def register_uninstall(version: str, exe: Path | None = None) -> None:
    """Ajoute Vox a « Applications et fonctionnalites »."""
    target = exe or installed_exe()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_WRITE) as key:
            values = {
                "DisplayName": f"Vox {version}",
                "DisplayVersion": version,
                "Publisher": "Quentin VLED",
                "InstallLocation": str(target.parent),
                "DisplayIcon": str(target),
                "UninstallString": f'"{target}" --uninstall',
                "QuietUninstallString": f'"{target}" --uninstall --silent',
            }
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            for name in ("NoModify", "NoRepair"):
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, 60_000)
    except OSError:
        pass


def unregister_uninstall() -> None:
    with contextlib.suppress(OSError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)


def set_autostart(enabled: bool) -> None:
    """Active ou retire le lancement au demarrage de Windows."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, "Vox", 0, winreg.REG_SZ, f'"{installed_exe()}"')
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, "Vox")
    except OSError:
        pass


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, "Vox")
            return True
    except OSError:
        return False


# ----------------------------------------------------------------------
# Boites de dialogue natives (sans Qt)
# ----------------------------------------------------------------------
MB_YESNO = 0x04
MB_OK = 0x00
MB_ICONQUESTION = 0x20
MB_ICONINFORMATION = 0x40
MB_ICONWARNING = 0x30
IDYES = 6


def message_box(text: str, title: str = "Vox", flags: int = MB_OK) -> int:
    try:
        return int(ctypes.windll.user32.MessageBoxW(None, text, title, flags))
    except Exception:
        return 0


# ----------------------------------------------------------------------
# Desinstallation
# ----------------------------------------------------------------------
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _schedule_directory_removal(target: Path) -> None:
    """Supprime un dossier apres la fin du processus courant.

    Impossible autrement : l'executable en cours d'execution est verrouille, et
    `Vox.exe --uninstall` ne peut donc pas effacer son propre fichier. On confie
    le menage a un `cmd.exe` detache.

    Le script passe par un fichier .cmd temporaire plutot qu'une commande en
    ligne : `cmd /c` interprete tres mal les esperluettes et les guillemets
    imbriques, et `timeout` echoue quand il n'y a pas de console (processus
    detache). On utilise donc `ping` pour patienter, et le script s'auto-detruit.
    """
    script = Path(tempfile.gettempdir()) / "vox-cleanup.cmd"
    body = (
        "@echo off\r\n"
        "rem Laisse le temps au processus appelant de se terminer.\r\n"
        "ping -n 4 127.0.0.1 >nul\r\n"
        f'rmdir /s /q "{target}"\r\n'
        'del "%~f0"\r\n'
    )
    try:
        script.write_text(body, encoding="ascii")
    except OSError:
        return
    with contextlib.suppress(OSError):
        subprocess.Popen(  # noqa: S603 - script genere localement
            [_system_exe("cmd.exe"), "/c", str(script)],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )


def uninstall(silent: bool = False) -> int:
    """Desinstalle Vox. Demande si l'historique doit aussi partir."""
    # 1. Les autres instances, mais surtout pas la notre.
    stop_running(exclude_self=True)

    # 2. Ce qui se retire immediatement.
    remove_shortcuts()
    unregister_uninstall()
    set_autostart(False)

    # 3. Les donnees personnelles, sur demande.
    history = data_dir()
    remove_data = False
    if history.exists() and not silent:
        answer = message_box(
            "Vox va etre desinstalle.\n\n"
            "Supprimer aussi ton historique de dictees, tes reglages et ta cle API ?\n\n"
            f"Dossier : {history}",
            "Desinstallation de Vox",
            MB_YESNO | MB_ICONQUESTION,
        )
        remove_data = answer == IDYES
    if remove_data:
        shutil.rmtree(history, ignore_errors=True)

    # 4. Le dossier d'installation : ce qui n'est pas verrouille tout de suite,
    #    le reste apres la sortie du processus.
    target = install_dir()
    for child in target.iterdir() if target.exists() else []:
        if child.name.lower() == PROCESS_NAME.lower():
            continue  # en cours d'execution
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                child.unlink(missing_ok=True)
    _schedule_directory_removal(target)

    if not silent:
        detail = (
            "Historique et reglages supprimes."
            if remove_data
            else f"Ton historique est conserve dans :\n{history}"
        )
        message_box(
            f"Vox a ete desinstalle.\n\n{detail}", "Vox", MB_OK | MB_ICONINFORMATION
        )
    return 0


def summary() -> str:
    """Petit recapitulatif, pratique en ligne de commande."""
    return (
        f"programme : {installed_exe()}\n"
        f"donnees   : {data_dir()}\n"
        f"demarrage : {'active' if is_autostart_enabled() else 'desactive'}"
    )


if __name__ == "__main__":  # pragma: no cover
    print(summary())
    sys.exit(0)
