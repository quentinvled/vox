"""Raccourci clavier global base sur un hook bas niveau.

Ctrl+Maj seul n'est pas declarable via RegisterHotKey (il faut une touche
reelle). On passe donc par `keyboard` (WH_KEYBOARD_LL) et on reconstruit la
logique "tap" nous-memes, avec annulation si une autre touche est pressee
pendant que la combinaison est maintenue (pour ne pas confondre avec
Ctrl+Maj+Echap, Ctrl+Maj+T, etc.).

Le module n'utilise volontairement aucune API Qt : les evenements sont
pousses dans une file, consommee par le thread principal via un QTimer.
"""

from __future__ import annotations

import queue
import threading
import time

# Evenements semantiques
EVENT_START = "start"
EVENT_STOP = "stop"
EVENT_CANCEL = "cancel"

_CTRL = {"ctrl", "left ctrl", "right ctrl"}
_SHIFT = {"shift", "left shift", "right shift"}
_ALT = {"alt", "left alt", "right alt", "alt gr"}
_WIN = {"windows", "left windows", "right windows", "win", "left win", "right win"}

_GROUPS = {"ctrl": _CTRL, "shift": _SHIFT, "alt": _ALT, "win": _WIN}

# Touches qui ne doivent pas invalider une combinaison (auto-repetition, etc.)
_IGNORED = {"", "unknown"}


class HotkeyManager:
    """Detecte une combinaison de modificateurs et publie des evenements."""

    def __init__(
        self,
        combo: str = "ctrl+shift",
        mode: str = "toggle",
        double_tap_enter: bool = True,
        min_tap_seconds: float = 0.05,
    ) -> None:
        self.combo = combo.lower().strip()
        self.mode = mode
        self.double_tap_enter = double_tap_enter
        self.min_tap_seconds = min_tap_seconds

        self.events: queue.Queue[str] = queue.Queue()
        self._groups = self._parse_combo(self.combo)
        self._pressed: set[str] = set()
        self._combo_down = False
        self._tainted = False
        self._combo_started_at = 0.0
        self._active = False
        self._handler = None
        self._lock = threading.Lock()
        self._error: str | None = None

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_combo(combo: str) -> set[str]:
        groups = set()
        for part in combo.replace(" ", "").split("+"):
            if part in _GROUPS:
                groups.add(part)
            elif part in {"control", "ctrl"}:
                groups.add("ctrl")
            elif part in {"maj", "shift"}:
                groups.add("shift")
            elif part in {"alt", "menu"}:
                groups.add("alt")
            elif part in {"win", "windows", "super"}:
                groups.add("win")
        if not groups:
            groups = {"ctrl", "shift"}
        return groups

    @property
    def error(self) -> str | None:
        return self._error

    # ------------------------------------------------------------------
    def configure(
        self,
        combo: str | None = None,
        mode: str | None = None,
        double_tap_enter: bool | None = None,
    ) -> None:
        """Met a jour la combinaison surveillee a chaud."""
        with self._lock:
            if combo is not None:
                self.combo = combo.lower().strip()
                self._groups = self._parse_combo(self.combo)
            if mode is not None:
                self.mode = mode
            if double_tap_enter is not None:
                self.double_tap_enter = double_tap_enter
            self._pressed.clear()
            self._combo_down = False
            self._tainted = False

    def start(self) -> None:
        """Installe le hook. Renvoie False (via .error) si indisponible."""
        if self._handler is not None:
            return
        try:
            import keyboard  # import tardif : evite un crash si absent
        except Exception as exc:
            self._error = f"Bibliothèque 'keyboard' indisponible : {exc}"
            return
        try:
            self._handler = keyboard.hook(self._on_event, suppress=False)
        except Exception as exc:
            self._error = f"Impossible d'installer le raccourci global : {exc}"
            self._handler = None

    def stop(self) -> None:
        if self._handler is None:
            return
        try:
            import keyboard

            keyboard.unhook(self._handler)
        except Exception:
            pass
        self._handler = None

    # ------------------------------------------------------------------
    @staticmethod
    def _group_of(name: str) -> str | None:
        lowered = (name or "").lower()
        for group, names in _GROUPS.items():
            if lowered in names:
                return group
        return None

    def _on_event(self, event) -> None:
        name = (event.name or "").lower()
        if name in _IGNORED:
            return
        is_down = event.event_type == "down"
        group = self._group_of(name)

        with self._lock:
            if group:
                if is_down:
                    self._pressed.add(group)
                else:
                    self._pressed.discard(group)
            else:
                # Une touche "normale" a ete pressee pendant la combinaison :
                # on invalide le tap pour ne pas confondre avec un raccourci.
                if is_down and self._pressed:
                    self._tainted = True

            combo_complete = self._groups.issubset(self._pressed)

            if combo_complete and not self._combo_down:
                self._combo_down = True
                self._combo_started_at = time.monotonic()
                self._tainted = False
                if self.mode == "push_to_talk":
                    self._emit(EVENT_START)

            elif not combo_complete and self._combo_down:
                self._combo_down = False
                held = time.monotonic() - self._combo_started_at
                tainted = self._tainted
                self._tainted = False

                if self.mode == "push_to_talk":
                    if tainted or held < self.min_tap_seconds:
                        self._emit(EVENT_CANCEL)
                    else:
                        self._emit(EVENT_STOP)
                else:
                    if tainted or held < self.min_tap_seconds:
                        return
                    self._emit(EVENT_STOP if self._active else EVENT_START)
                    self._active = not self._active

    def _emit(self, kind: str) -> None:
        self.events.put(kind)

    def drain(self) -> list[str]:
        """Recupere les evenements en attente (appele depuis le thread Qt)."""
        out: list[str] = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out
