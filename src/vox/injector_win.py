"""Injection de texte sous Windows (SendInput / presse-papier Win32)."""

from __future__ import annotations

import contextlib
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
ULONG = wintypes.ULONG
DWORD = wintypes.DWORD
WORD = wintypes.WORD
LONG = wintypes.LONG
BOOL = wintypes.BOOL
HANDLE = wintypes.HANDLE
HWND = wintypes.HWND
LPVOID = ctypes.c_void_p

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RETURN = 0x0D
VK_INSERT = 0x2D
VK_V = 0x56
VK_C = 0x43


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

# Sur Windows 64 bits (x64 comme ARM64), sizeof(INPUT) == 40.
INPUT_SIZE = ctypes.sizeof(INPUT)


class InjectorError(RuntimeError):
    pass


# ----------------------------------------------------------------------
# Bloc bas niveau
# ----------------------------------------------------------------------
def _key_event(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    item = INPUT(type=INPUT_KEYBOARD)
    item.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return item


def _send(items: list[INPUT]) -> int:
    if not items:
        return 0
    array = (INPUT * len(items))(*items)
    sent = user32.SendInput(len(items), array, INPUT_SIZE)
    if sent != len(items):
        raise InjectorError(
            f"SendInput a envoyé {sent}/{len(items)} événements "
            f"(erreur {ctypes.get_last_error()})."
        )
    return sent


def _combo_items(combo: str) -> list[INPUT]:
    """Traduit 'ctrl+alt+v' en sequence d'evenements clavier."""
    names = [part.strip().lower() for part in combo.split("+") if part.strip()]
    if not names:
        raise InjectorError("Raccourci de collage vide.")

    modifier_map = {
        "ctrl": VK_CONTROL,
        "control": VK_CONTROL,
        "shift": VK_SHIFT,
        "alt": VK_MENU,
        "win": VK_LWIN,
        "super": VK_LWIN,
        "cmd": VK_LWIN,
    }
    key_map = {"insert": VK_INSERT, "ins": VK_INSERT}

    modifiers: list[int] = []
    key_vk: int | None = None
    for name in names:
        if name in modifier_map:
            modifiers.append(modifier_map[name])
            continue
        if name in key_map:
            key_vk = key_map[name]
        elif len(name) == 1:
            key_vk = ctypes.windll.user32.VkKeyScanW(ord(name.upper())) & 0xFF
        else:
            raise InjectorError(f"Touche inconnue dans le raccourci : {name!r}")

    if key_vk is None:
        raise InjectorError("Le raccourci de collage doit contenir une touche finale.")

    items: list[INPUT] = [_key_event(vk=vk) for vk in modifiers]
    items.append(_key_event(vk=key_vk))
    items.append(_key_event(vk=key_vk, flags=KEYEVENTF_KEYUP))
    items.extend(_key_event(vk=vk, flags=KEYEVENTF_KEYUP) for vk in reversed(modifiers))
    return items


# ----------------------------------------------------------------------
# Presse-papier (Win32, utilisable depuis n'importe quel thread)
# ----------------------------------------------------------------------
kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = HANDLE
kernel32.GlobalLock.argtypes = (HANDLE,)
kernel32.GlobalLock.restype = LPVOID
kernel32.GlobalUnlock.argtypes = (HANDLE,)
kernel32.GlobalUnlock.restype = BOOL
kernel32.GlobalFree.argtypes = (HANDLE,)
user32.OpenClipboard.argtypes = (HWND,)
user32.SetClipboardData.argtypes = (wintypes.UINT, HANDLE)
user32.SetClipboardData.restype = HANDLE
user32.GetClipboardData.argtypes = (wintypes.UINT,)
user32.GetClipboardData.restype = HANDLE


def _clipboard_open(retries: int = 10) -> None:
    for attempt in range(retries):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.02 * (attempt + 1))
    raise InjectorError("Le presse-papier est verrouillé par une autre application.")


def get_clipboard_text() -> str:
    try:
        _clipboard_open()
    except InjectorError:
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> None:
    data = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise InjectorError("Allocation mémoire du presse-papier impossible.")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise InjectorError("Verrouillage du presse-papier impossible.")
    try:
        ctypes.memmove(pointer, data, len(data))
    finally:
        kernel32.GlobalUnlock(handle)

    _clipboard_open()
    try:
        if not user32.EmptyClipboard():
            kernel32.GlobalFree(handle)
            raise InjectorError("Impossible de vider le presse-papier.")
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise InjectorError("Impossible d'écrire dans le presse-papier.")
        # Apres SetClipboardData, la propriete du bloc est transferee au systeme.
    finally:
        user32.CloseClipboard()


# ----------------------------------------------------------------------
# API publique
# ----------------------------------------------------------------------
def type_text(text: str, per_chunk: int = 120, delay: float = 0.006) -> None:
    """Tape le texte caractere par caractere, independamment du clavier (AZERTY/QWERTY)."""
    if not text:
        return
    encoded = text.encode("utf-16-le")
    units = [int.from_bytes(encoded[i : i + 2], "little") for i in range(0, len(encoded), 2)]

    for start in range(0, len(units), per_chunk):
        chunk = units[start : start + per_chunk]
        items: list[INPUT] = []
        for unit in chunk:
            items.append(_key_event(scan=unit, flags=KEYEVENTF_UNICODE))
            items.append(_key_event(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        _send(items)
        if len(units) > per_chunk:
            time.sleep(delay)


def paste_text(text: str, paste_keys: str = "ctrl+v", restore: bool = True) -> None:
    """Copie le texte puis simule le raccourci de collage."""
    if not text:
        return
    previous = get_clipboard_text() if restore else ""
    set_clipboard_text(text)
    time.sleep(0.03)
    _send(_combo_items(paste_keys))
    if restore:
        time.sleep(0.35)
        with contextlib.suppress(InjectorError):
            set_clipboard_text(previous)


def press_enter() -> None:
    _send([_key_event(vk=VK_RETURN), _key_event(vk=VK_RETURN, flags=KEYEVENTF_KEYUP)])


def inject(text: str, method: str = "paste", paste_keys: str = "ctrl+v") -> None:
    if not text:
        return
    if method == "type":
        type_text(text)
    else:
        paste_text(text, paste_keys=paste_keys)


def get_foreground_window_title() -> str:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


__all__ = [
    "InjectorError",
    "get_clipboard_text",
    "get_foreground_window_title",
    "inject",
    "paste_text",
    "press_enter",
    "set_clipboard_text",
    "type_text",
]
