"""Installation et desinstallation de Vox.

Sous Windows : installation dans %LOCALAPPDATA%\\Programs\\Vox, raccourcis,
entree de desinstallation dans le registre. Sous Linux, l'application est
distribuee en AppImage (fichier unique) : il n'y a rien a copier, seule la
gestion du demarrage automatique et des donnees s'applique.

Volontairement limite a la bibliotheque standard : ce module est importe par
l'installeur, qui ne doit surtout pas embarquer Qt ni quoi que ce soit de
lourd.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import winreg  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - hors Windows
    winreg = None  # type: ignore[assignment]

from .paths import data_dir

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Vox"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SHORTCUT_NAME = "Vox.lnk"
RECORDINGS_SHORTCUT_NAME = "Mes enregistrements.lnk"
RECORDINGS_ICON = "Enregistrements.ico"
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
    if sys.platform != "win32":
        # Hors Windows il n'y a pas de copie : on pointe l'AppImage (figee) ou
        # le lanceur installe.
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve()
        return Path(shutil.which("vox") or sys.executable).resolve()
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
$items = @(
{items}
)
foreach ($item in $items) {{
    $link = $shell.CreateShortcut($item.Path)
    $link.TargetPath = $item.Target
    $link.WorkingDirectory = $item.WorkingDirectory
    if ($item.Arguments) {{ $link.Arguments = $item.Arguments }}
    $link.Description = $item.Description
    $link.IconLocation = $item.Icon
    $link.Save()
}}
"""


def _ps_quote(value: object) -> str:
    """Litteral PowerShell entre apostrophes (une apostrophe se double)."""
    return "'" + str(value).replace("'", "''") + "'"


def shortcut_destinations() -> list[Path]:
    """Raccourcis crees a l'installation : dictee, puis enregistrements."""
    folders = (desktop_dir(), start_menu_dir())
    return [folder / SHORTCUT_NAME for folder in folders] + [
        folder / RECORDINGS_SHORTCUT_NAME for folder in folders
    ]


def create_shortcuts(exe: Path | None = None, icon: Path | None = None) -> list[Path]:
    """Cree les raccourcis Bureau et Menu Demarrer. Renvoie ceux qui existent.

    Deux entrees : « Vox » (l'application) et « Mes enregistrements », qui
    lance `Vox.exe --recordings` avec une icone de presse-papier. Le second
    raccourci n'est cree que si son icone est disponible : sans elle, les deux
    raccourcis seraient visuellement identiques et donc indechiffrables.
    """
    target = exe or installed_exe()
    workdir = target.parent
    recordings_icon = icon if icon and icon.exists() else None

    items: list[tuple[Path, str, str, str]] = []
    for folder in (desktop_dir(), start_menu_dir()):
        items.append(
            (
                folder / SHORTCUT_NAME,
                "",
                "Vox - dictee vocale (Ctrl + Maj)",
                f"{target},0",
            )
        )
    if recordings_icon:
        for folder in (desktop_dir(), start_menu_dir()):
            items.append(
                (
                    folder / RECORDINGS_SHORTCUT_NAME,
                    "--recordings",
                    "Vox - ecouter et retrouver mes dictees",
                    str(recordings_icon),
                )
            )

    blocks = []
    for path, arguments, description, icon_location in items:
        blocks.append(
            "@{\n"
            f"    Path = {_ps_quote(path)};\n"
            f"    Target = {_ps_quote(target)};\n"
            f"    WorkingDirectory = {_ps_quote(workdir)};\n"
            f"    Arguments = {_ps_quote(arguments)};\n"
            f"    Description = {_ps_quote(description)};\n"
            f"    Icon = {_ps_quote(icon_location)};\n"
            "}"
        )
    # Separes par des virgules, et surtout sans virgule finale : PowerShell
    # refuse `@( ... , )` avec « Missing expression after ',' ».
    script = _SHORTCUT_SCRIPT.format(items=",\n".join(blocks))

    destinations = [path for path, *_ in items]
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
    for path in shortcut_destinations():
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


# ----------------------------------------------------------------------
# Registre
# ----------------------------------------------------------------------
def register_uninstall(version: str, exe: Path | None = None) -> None:
    """Ajoute Vox a « Applications et fonctionnalites »."""
    target = exe or installed_exe()
    # Taille affichee dans « Applications et fonctionnalites » : somme reelle des
    # fichiers installes, en Ko (le dossier contient surtout Vox.exe).
    try:
        size_kb = max(
            1,
            sum(
                child.stat().st_size
                for child in target.parent.iterdir()
                if child.is_file()
            )
            // 1024,
        )
    except OSError:
        size_kb = 60_000
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
            winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
    except OSError:
        pass


def unregister_uninstall() -> None:
    with contextlib.suppress(OSError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)


def set_autostart(enabled: bool) -> None:
    """Active ou retire le lancement au demarrage (registre ou .desktop)."""
    if sys.platform != "win32":
        from . import autostart

        autostart.set_autostart(enabled)
        return
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
    if sys.platform != "win32":
        from . import autostart

        return autostart.is_autostart_enabled()
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
    if sys.platform != "win32":
        return _uninstall_unix(silent)

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


def _uninstall_unix(silent: bool = False) -> int:
    """Desinstallation hors Windows (AppImage ou paquet systeme).

    Il n'y a pas de dossier a effacer : l'application est un fichier unique.
    On retire le demarrage automatique et, sur demande, les donnees.
    """
    from . import autostart

    autostart.set_autostart(False)

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

    print("Vox a ete desinstalle.")
    if not remove_data and history.exists():
        print(f"Ton historique est conserve dans : {history}")
    print("Pense a supprimer le fichier de l'application (AppImage) si besoin.")
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
