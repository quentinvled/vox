"""Raccourci clavier global base sur un hook bas niveau.

Ctrl+Maj seul n'est pas declarable via RegisterHotKey (il faut une touche
reelle). Sous Windows on passe donc par `keyboard` (WH_KEYBOARD_LL) ; sous
Linux, `pynput` ecoute les evenements X11 sans privilege particulier. Dans les
deux cas on reconstruit la logique « tap » nous-memes, avec annulation si une
autre touche est pressee pendant que la combinaison est maintenue (pour ne pas
confondre avec Ctrl+Maj+Echap, Ctrl+Maj+T, etc.).

Le module n'utilise volontairement aucune API Qt : les evenements sont
pousses dans une file, consommee par le thread principal via un QTimer.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import sys
import threading
import time

log = logging.getLogger("vox")

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

# Noms renvoyes par pynput -> noms attendus par la machine a etats ci-dessous.
_PYNPUT_NAMES = {
    "ctrl": "ctrl",
    "ctrl_l": "left ctrl",
    "ctrl_r": "right ctrl",
    "shift": "shift",
    "shift_l": "left shift",
    "shift_r": "right shift",
    "alt": "alt",
    "alt_l": "left alt",
    "alt_r": "right alt",
    "alt_gr": "alt gr",
    "cmd": "win",
    "cmd_l": "left win",
    "cmd_r": "right win",
}


def _pynput_name(key) -> str:
    """Normalise une touche pynput en nom lisible (« ctrl », « alt gr », « a »)."""
    name = getattr(key, "name", None)
    if name:
        return _PYNPUT_NAMES.get(name, name)
    char = getattr(key, "char", None)
    if char:
        return str(char).lower()
    return ""


class _WindowsPoller:
    """Surveille l'etat des modificateurs sans hook clavier.

    `GetAsyncKeyState` ne demande ni privilege, ni hook : un antivirus ne peut
    pas le bloquer. La scrutation tourne en permanence, en parallele du hook :
    l'union des deux sources est plus fiable que l'une ou l'autre (sur certaines
    machines le hook voit Ctrl mais pas la touche Windows, par exemple). Les
    evenements en double sont sans effet, les transitions etant dedoublonnees
    par la machine a etats.
    """

    INTERVAL = 0.012
    # VK_LSHIFT/RSHIFT, LCONTROL/RCONTROL, LMENU/RMENU, LWIN/RWIN.
    _MODIFIERS: tuple[tuple[int, str], ...] = (
        (0xA0, "left shift"),
        (0xA1, "right shift"),
        (0xA2, "left ctrl"),
        (0xA3, "right ctrl"),
        (0xA4, "left alt"),
        (0xA5, "right alt"),
        (0x5B, "left win"),
        (0x5C, "right win"),
    )
    # Souris + modificateurs generiques : ignores lors de la detection de taint.
    _IGNORED_VK = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x10, 0x11, 0x12}

    def __init__(self, manager: "HotkeyManager") -> None:
        self._manager = manager
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._modifier_vks = {vk for vk, _ in self._MODIFIERS}
        self._previous = {vk: False for vk in self._modifier_vks}
        self._warned = False

    def start(self) -> None:
        import ctypes

        self._user32 = ctypes.windll.user32
        self._thread = threading.Thread(
            target=self._run, name="vox-hotkey-poll", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _down(self, vk: int) -> bool:
        return bool(self._user32.GetAsyncKeyState(vk) & 0x8000)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            time.sleep(self.INTERVAL)

    def tick(self) -> None:
        """Une passe de scrutation (separee pour etre testable).

        Tourne en permanence, meme quand le hook fonctionne : sur certaines
        machines le hook voit Ctrl mais pas la touche Windows. Les transitions
        en double sont sans effet.
        """
        if self._manager.error:
            return
        for vk, name in self._MODIFIERS:
            down = self._down(vk)
            changed = down != self._previous[vk]
            self._previous[vk] = down
            if not changed:
                continue
            if not self._warned and self._manager.hook_events == 0:
                self._warned = True
                log.info("Hook clavier silencieux : la scrutation prend le relais.")
            self._manager._handle(name, down)
        if self._manager.combo_held and self._any_other_key_down():
            self._manager._mark_tainted()

    def _any_other_key_down(self) -> bool:
        for vk in range(0x08, 0xFF):
            if vk in self._modifier_vks or vk in self._IGNORED_VK:
                continue
            if self._down(vk):
                return True
        return False


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
        self._poller = None
        self._lock = threading.Lock()
        self._error: str | None = None
        # Nombre d'evenements recus par le hook clavier (0 = hook muet).
        self.hook_events = 0
        # Nombre d'evenements clavier reellement traites : sert de temoin pour
        # verifier que le raccourci fonctionne (0 = bloque par l'environnement).
        self.seen = 0
        # Noms de touches deja vus : journalises une fois, pour diagnostiquer
        # une touche qui n'arrive jamais (touche Windows verrouillee, etc.).
        self._names_seen: set[str] = set()

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

    @property
    def combo_held(self) -> bool:
        """Vrai pendant que la combinaison surveillee est maintenue."""
        return self._combo_down

    def _mark_tainted(self) -> None:
        """Invalide le tap en cours (une autre touche a ete pressee)."""
        with self._lock:
            if self._pressed:
                self._tainted = True

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
        if self._handler is not None or self._poller is not None:
            return
        if sys.platform == "win32":
            self._start_keyboard()
            # Toujours doubler le hook par une scrutation d'etat : si le hook
            # est bloque (antivirus, pilote clavier), le raccourci continue de
            # fonctionner. La scrutation se met en veille des que le hook emet
            # le moindre evenement, donc aucune double detection en pratique.
            self._start_polling()
        else:
            self._start_pynput()

    def _start_polling(self) -> None:
        try:
            poller = _WindowsPoller(self)
            poller.start()
        except Exception as exc:  # pragma: no cover - depend du systeme
            log.warning("Scrutation clavier indisponible : %s", exc)
            return
        self._poller = poller
        if self._error:
            # Le hook a echoue mais la scrutation prend le relais : le raccourci
            # est operationnel, on ne signale donc pas d'erreur a l'utilisateur.
            log.warning("Raccourci : repli sur la scrutation (%s)", self._error)
            self._error = None

    def _start_keyboard(self) -> None:
        try:
            import keyboard  # import tardif : evite un crash si absent
        except Exception as exc:
            self._error = f"Bibliothèque 'keyboard' indisponible : {exc}"
            return
        # Un antivirus peut refuser le hook au premier essai : on retente.
        last: Exception | None = None
        for _attempt in range(3):
            try:
                self._handler = keyboard.hook(self._on_keyboard_event, suppress=False)
                self._error = None
                log.info("Hook clavier Windows installe.")
                return
            except Exception as exc:  # pragma: no cover - depend du systeme
                last = exc
                time.sleep(0.15)
        self._error = f"Impossible d'installer le raccourci global : {last}"
        self._handler = None

    def _start_pynput(self) -> None:
        try:
            from pynput import keyboard
        except Exception as exc:
            self._error = f"Bibliothèque 'pynput' indisponible : {exc}"
            return
        last: Exception | None = None
        for _attempt in range(3):
            try:
                listener = keyboard.Listener(
                    on_press=self._on_pynput_press,
                    on_release=self._on_pynput_release,
                    suppress=False,
                )
                listener.start()
                self._handler = listener
                self._error = None
                log.info("Ecoute clavier X11 (pynput) installee.")
                return
            except Exception as exc:  # pragma: no cover - depend du systeme
                last = exc
                time.sleep(0.15)
        self._error = f"Impossible d'installer le raccourci global : {last}"
        self._handler = None

    def stop(self) -> None:
        if self._handler is not None:
            if sys.platform == "win32":
                try:
                    import keyboard

                    keyboard.unhook(self._handler)
                except Exception:
                    pass
            else:
                with contextlib.suppress(Exception):
                    self._handler.stop()
            self._handler = None
        if self._poller is not None:
            self._poller.stop()
            self._poller = None

    # ------------------------------------------------------------------
    @staticmethod
    def _group_of(name: str) -> str | None:
        lowered = (name or "").lower()
        for group, names in _GROUPS.items():
            if lowered in names:
                return group
        return None

    def _on_keyboard_event(self, event) -> None:
        self.hook_events += 1
        self._handle(event.name or "", event.event_type == "down")

    def _on_pynput_press(self, key) -> None:
        self.hook_events += 1
        self._handle(_pynput_name(key), True)

    def _on_pynput_release(self, key) -> None:
        self.hook_events += 1
        self._handle(_pynput_name(key), False)

    def _handle(self, name: str, is_down: bool) -> None:
        lowered = (name or "").lower()
        if lowered in _IGNORED:
            return
        self.seen += 1
        if lowered not in self._names_seen:
            self._names_seen.add(lowered)
            if len(self._names_seen) <= 40:
                log.info("Touche clavier detectee : %s", lowered)
        group = self._group_of(lowered)

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
