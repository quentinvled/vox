"""Injection de texte sous macOS : frappe, presse-papier et application active.

macOS embarque ses propres outils de presse-papier (`pbcopy` pour ecrire,
`pbpaste` pour lire) : aucune dependance Python n'est necessaire, et ils sont
toujours presents.

La frappe et la simulation de raccourcis passent par `pynput`, qui s'appuie sur
Quartz. macOS exige pour cela l'autorisation « Accessibilite » (voir
`permissions.py`) : sans elle, les frappes sont silencieusement ignorees, d'ou
l'interet de la verifier et de le dire a l'utilisateur.

L'application active est identifiee par son PID, via `lsappinfo` (outil systeme,
sans autorisation particuliere) ou AppKit quand PyObjC est disponible. Le
**titre** de la fenetre n'est volontairement pas lu : macOS le reserve aux
applications autorisees a enregistrer l'ecran, et le PID suffit a verifier que
l'insertion visait bien la meme application.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import time
from pathlib import Path


class InjectorError(RuntimeError):
    pass


log = logging.getLogger("vox")


# ----------------------------------------------------------------------
# Presse-papier : pbcopy / pbpaste
# ----------------------------------------------------------------------
# Chemins absolus : macOS les installe toujours la, et cela evite de dependre
# du PATH (different quand Vox est lancee depuis le Finder).
PBCOPY = "/usr/bin/pbcopy"
PBPASTE = "/usr/bin/pbpaste"
LSAPPINFO = "/usr/bin/lsappinfo"


def _has(program: str) -> bool:
    return Path(program).is_file() or shutil.which(program) is not None


def clipboard_available() -> bool:
    """Vrai si les outils systeme de presse-papier sont disponibles."""
    return _has(PBCOPY) and _has(PBPASTE)


def get_clipboard_text() -> str:
    if not _has(PBPASTE):
        return ""
    try:
        result = subprocess.run(  # noqa: S603 - chemin absolu, sans shell
            [PBPASTE], capture_output=True, check=False, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="replace")


def set_clipboard_text(text: str) -> None:
    if not _has(PBCOPY):
        raise InjectorError("Outil système 'pbcopy' introuvable.")
    try:
        result = subprocess.run(  # noqa: S603 - chemin absolu, sans shell
            [PBCOPY],
            input=text.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InjectorError(f"Écriture dans le presse-papier impossible : {exc}") from exc
    if result.returncode != 0:
        raise InjectorError("Écriture dans le presse-papier impossible (pbcopy a échoué).")


# ----------------------------------------------------------------------
# Clavier (pynput / Quartz)
# ----------------------------------------------------------------------
def _pynput():
    """Importe pynput en convertissant tout echec en InjectorError."""
    try:
        from pynput import keyboard
    except Exception as exc:  # pragma: no cover - depend de l'environnement
        raise InjectorError(f"Saisie clavier indisponible (pynput) : {exc}") from exc
    return keyboard


def _controller():
    return _pynput().Controller()


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


_MODIFIER_TOKENS = {
    "ctrl",
    "control",
    "shift",
    "alt",
    "win",
    "super",
    "cmd",
    "command",
    "meta",
}
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
        "command": Key.cmd,
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


def paste_text(text: str, paste_keys: str = "cmd+v", restore: bool = True) -> None:
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


def inject(text: str, method: str = "paste", paste_keys: str = "cmd+v") -> None:
    if not text:
        return
    if method == "type" or not clipboard_available():
        type_text(text)
    else:
        paste_text(text, paste_keys=paste_keys)


# ----------------------------------------------------------------------
# Application active
# ----------------------------------------------------------------------
def _run(command: list[str], timeout: float = 2.0) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - chemin absolu, sans shell
            command, capture_output=True, check=False, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="replace").strip()


def _frontmost_pid_appkit() -> int:
    """PID de l'application active via AppKit, si PyObjC est installe."""
    try:
        from AppKit import NSWorkspace
    except Exception:
        return 0
    try:
        application = NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(application.processIdentifier()) if application is not None else 0
    except Exception:
        return 0


def _lsappinfo_front() -> str:
    return _run([LSAPPINFO, "front"])


def _lsappinfo_pid(asn: str) -> int:
    output = _run([LSAPPINFO, "info", "-only", "pid", asn])
    for line in output.splitlines():
        line = line.strip()
        if line.startswith('"pid"='):
            with contextlib.suppress(ValueError):
                return int(line.split("=", 1)[1].strip().strip('"'))
    return 0


def _lsappinfo_name(asn: str) -> str:
    output = _run([LSAPPINFO, "info", "-only", "name", asn])
    for line in output.splitlines():
        line = line.strip()
        if line.startswith('"name"=') or line.startswith('"LSDisplayName"='):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def get_foreground_window_id() -> int:
    """PID de l'application active (0 si le systeme ne sait pas le dire).

    Sert a verifier, apres l'insertion, que le texte est alle dans la meme
    application qu'au moment de l'arreter : plus fiable que le titre, qui peut
    changer tout seul (page web, document modifie...).
    """
    pid = _frontmost_pid_appkit()
    if pid:
        return pid
    asn = _lsappinfo_front()
    return _lsappinfo_pid(asn) if asn else 0


def get_foreground_window_title() -> str:
    """Nom de l'application active (le titre exact reste prive sous macOS)."""
    try:
        from AppKit import NSWorkspace
    except Exception:
        NSWorkspace = None  # type: ignore[assignment]
    if NSWorkspace is not None:
        try:
            application = NSWorkspace.sharedWorkspace().frontmostApplication()
            name = application.localizedName() if application is not None else ""
            if name:
                return str(name)
        except Exception:
            log.debug("Nom de l'application active indisponible", exc_info=True)
    asn = _lsappinfo_front()
    return _lsappinfo_name(asn) if asn else ""


__all__ = [
    "InjectorError",
    "clipboard_available",
    "get_clipboard_text",
    "get_foreground_window_id",
    "get_foreground_window_title",
    "inject",
    "paste_text",
    "press_enter",
    "set_clipboard_text",
    "type_text",
]
