"""Machine à états du raccourci global (sans hook clavier réel).

On appelle `_handle` directement, comme le ferait un hook : c'est ce qui rend
la bascule « Espace » testable sans clavier ni Windows.
"""

from __future__ import annotations

from vox.hotkey import (
    EVENT_CANCEL,
    EVENT_MODE,
    EVENT_START,
    EVENT_STOP,
    HotkeyManager,
)


def _manager(mode: str = "push_to_talk") -> HotkeyManager:
    # min_tap_seconds=0 : pas de temporisation dans les tests.
    return HotkeyManager(combo="ctrl+shift", mode=mode, min_tap_seconds=0.0)


def test_space_toggles_mode_without_cancelling_push_to_talk() -> None:
    hotkey = _manager()
    hotkey.set_armed(True)
    hotkey._handle("ctrl", True)
    hotkey._handle("shift", True)
    assert hotkey.drain() == [EVENT_START]

    hotkey._handle("space", True)
    assert hotkey.drain() == [EVENT_MODE]
    # Une auto-répétition de l'Espace ne bascule pas deux fois.
    hotkey._handle("space", True)
    assert hotkey.drain() == []
    hotkey._handle("space", False)

    # Relâcher la combinaison arrête l'écoute : l'Espace n'a pas invalidé le tap.
    hotkey._handle("shift", False)
    hotkey._handle("ctrl", False)
    assert hotkey.drain() == [EVENT_STOP]


def test_space_is_a_plain_key_when_not_armed() -> None:
    hotkey = _manager()
    hotkey.set_armed(False)
    hotkey._handle("ctrl", True)
    hotkey._handle("shift", True)
    assert hotkey.drain() == [EVENT_START]

    # Sans armement, l'Espace est une frappe comme une autre : elle invalide le
    # tap en cours (comportement historique, pour ne pas confondre avec Ctrl+Maj+X).
    hotkey._handle("space", True)
    assert hotkey.drain() == []
    hotkey._handle("shift", False)
    hotkey._handle("ctrl", False)
    assert hotkey.drain() == [EVENT_CANCEL]


def test_space_toggles_in_toggle_mode() -> None:
    hotkey = _manager(mode="toggle")
    hotkey.set_armed(True)
    # En bascule, l'écoute démarre au relâchement de la combinaison.
    hotkey._handle("ctrl", True)
    hotkey._handle("shift", True)
    hotkey._handle("shift", False)
    hotkey._handle("ctrl", False)
    assert hotkey.drain() == [EVENT_START]

    hotkey._handle("space", True)
    hotkey._handle("space", False)
    assert hotkey.drain() == [EVENT_MODE]
    hotkey._handle("space", True)
    hotkey._handle("space", False)
    assert hotkey.drain() == [EVENT_MODE]


def test_set_armed_resets_the_space_state() -> None:
    hotkey = _manager()
    hotkey.set_armed(True)
    hotkey._handle("space", True)
    assert hotkey.drain() == [EVENT_MODE]
    # Désarmement puis réarmement : un nouvel appui rebascule.
    hotkey.set_armed(False)
    hotkey.set_armed(True)
    hotkey._handle("space", True)
    assert hotkey.drain() == [EVENT_MODE]
