"""Icone de la zone de notification et menu contextuel."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import __version__
from ..reword import TONES


class Tray(QSystemTrayIcon):
    toggle_requested = Signal()
    show_requested = Signal()
    reinsert_requested = Signal()
    copy_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()
    model_selected = Signal(str)
    reword_toggled = Signal(bool)
    tone_selected = Signal(str)
    icon_help_requested = Signal()
    autostart_toggled = Signal(bool)
    taskbar_toggled = Signal(bool)
    stats_requested = Signal()
    history_requested = Signal()
    update_requested = Signal()

    def __init__(
        self,
        icon,
        hotkey_label: str,
        autostart: bool = False,
        show_in_taskbar: bool = False,
        parent=None,
    ) -> None:
        super().__init__(icon, parent)
        self._hotkey_label = hotkey_label
        self._models: list[dict] = []
        self._current_model = ""
        self._tone = "clean"
        self._reword_enabled = False
        self._autostart = autostart
        self._show_in_taskbar = show_in_taskbar

        self._menu = QMenu()
        self._build()
        self.setContextMenu(self._menu)
        self.setToolTip(f"Vox {__version__} — {hotkey_label}")
        self.activated.connect(self._on_activated)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        self._menu.clear()

        self.status_action = QAction(f"Vox {__version__}", self._menu)
        self.status_action.setEnabled(False)
        self._menu.addAction(self.status_action)

        self.update_action = QAction("Mise à jour disponible…", self._menu)
        self.update_action.triggered.connect(self.update_requested.emit)
        self.update_action.setVisible(False)
        self._menu.addAction(self.update_action)
        self._menu.addSeparator()

        self.toggle_action = QAction(f"Dicter ({self._hotkey_label})", self._menu)
        self.toggle_action.triggered.connect(self.toggle_requested.emit)
        self._menu.addAction(self.toggle_action)

        show_action = QAction("Afficher la pilule", self._menu)
        show_action.triggered.connect(self.show_requested.emit)

        display_menu = self._menu.addMenu("Affichage")
        display_menu.addAction(show_action)

        self.taskbar_action = QAction("Garder dans la barre des tâches", display_menu)
        self.taskbar_action.setCheckable(True)
        self.taskbar_action.setChecked(self._show_in_taskbar)
        self.taskbar_action.toggled.connect(self.taskbar_toggled.emit)
        display_menu.addAction(self.taskbar_action)

        self._menu.addSeparator()

        stats_action = QAction("Statistiques…", self._menu)
        stats_action.triggered.connect(self.stats_requested.emit)
        self._menu.addAction(stats_action)

        history_action = QAction("Enregistrements…", self._menu)
        history_action.setToolTip(
            "Réécouter les dictées conservées, copier leur texte, les retranscrire"
        )
        history_action.triggered.connect(self.history_requested.emit)
        self._menu.addAction(history_action)

        icon_help = QAction("Où est mon icône ?", display_menu)
        icon_help.triggered.connect(self.icon_help_requested.emit)
        display_menu.addAction(icon_help)

        self._menu.addSeparator()

        self.model_menu = self._menu.addMenu("Modèle de transcription")
        self.model_group = QActionGroup(self)
        self.model_group.setExclusive(True)

        self.reword_menu = self._menu.addMenu("Reformulation")
        self.reword_action = QAction("Activer la reformulation", self.reword_menu)
        self.reword_action.setCheckable(True)
        self.reword_action.toggled.connect(self.reword_toggled.emit)
        self.reword_menu.addAction(self.reword_action)
        self.reword_menu.addSeparator()

        self.tone_group = QActionGroup(self)
        self.tone_group.setExclusive(True)
        self._tone_actions: dict[str, QAction] = {}
        for tone_id, tone in TONES.items():
            if tone_id == "custom":
                continue
            action = QAction(tone["label"], self.tone_group)
            action.setCheckable(True)
            action.setChecked(tone_id == self._tone)
            action.triggered.connect(lambda _=False, tid=tone_id: self.tone_selected.emit(tid))
            self.reword_menu.addAction(action)
            self._tone_actions[tone_id] = action

        self._menu.addSeparator()

        reinsert_action = QAction("Réinsérer le dernier texte", self._menu)
        reinsert_action.triggered.connect(self.reinsert_requested.emit)
        self._menu.addAction(reinsert_action)

        copy_action = QAction("Copier le dernier texte", self._menu)
        copy_action.triggered.connect(self.copy_requested.emit)
        self._menu.addAction(copy_action)

        self._menu.addSeparator()

        self.autostart_action = QAction("Lancer au démarrage de la session", self._menu)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(self._autostart)
        self.autostart_action.toggled.connect(self.autostart_toggled.emit)
        self._menu.addAction(self.autostart_action)

        self._menu.addSeparator()

        settings_action = QAction("Réglages…", self._menu)
        settings_action.triggered.connect(self.settings_requested.emit)
        self._menu.addAction(settings_action)

        quit_action = QAction("Quitter", self._menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(quit_action)

    # ------------------------------------------------------------------
    def set_models(self, models: list[dict], current: str) -> None:
        self._models = models
        self._current_model = current
        self.model_menu.clear()
        for action in list(self.model_group.actions()):
            self.model_group.removeAction(action)
        for model in models:
            model_id = model.get("id") or ""
            action = QAction(model.get("name") or model_id, self.model_group)
            action.setCheckable(True)
            action.setChecked(model_id == current)
            action.triggered.connect(lambda _=False, mid=model_id: self.model_selected.emit(mid))
            self.model_menu.addAction(action)

    def set_current_model(self, model_id: str, models: list[dict] | None = None) -> None:
        self._current_model = model_id
        if models is not None:
            self._models = models
        for action in self.model_group.actions():
            names = {m.get("name") for m in self._models if m.get("id") == model_id}
            action.setChecked(action.text() in names)

    def set_reword_enabled(self, enabled: bool) -> None:
        self._reword_enabled = enabled
        self.reword_action.setChecked(enabled)

    def set_autostart(self, enabled: bool) -> None:
        self._autostart = enabled
        self.autostart_action.setChecked(enabled)

    def set_show_in_taskbar(self, enabled: bool) -> None:
        self._show_in_taskbar = enabled
        self.taskbar_action.setChecked(enabled)

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        action = self._tone_actions.get(tone)
        if action:
            action.setChecked(True)

    def set_status(self, text: str) -> None:
        self.status_action.setText(f"Vox {__version__} — {text}")
        self.setToolTip(f"Vox {__version__}\n{text}\n{self._hotkey_label}")

    def set_update_available(self, version: str) -> None:
        """Fait apparaitre l'entree « Mise a jour disponible »."""
        self.update_action.setText(f"Mise à jour {version} disponible…")
        self.update_action.setVisible(True)

    # ------------------------------------------------------------------
    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_requested.emit()
