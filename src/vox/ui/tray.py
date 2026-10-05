"""Icone de la zone de notification et menu contextuel."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import __version__
from ..reword import TONES
from ..sources import KIND_MIC, KIND_SYSTEM, SourceStatus, status_dot
from .widgets import make_app_icon, make_dot_icon


def format_elapsed(seconds: int) -> str:
    """Chrono court : « 05:32 », « 1:12:07 » au-delà d'une heure."""
    minutes, secs = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


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
    import_requested = Signal()
    update_requested = Signal()
    audio_refresh_requested = Signal()
    audio_test_requested = Signal()
    call_toggle_requested = Signal()

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
        self._status_text = ""
        self._audio_statuses: list[SourceStatus] = []
        self._call_recording = False

        self._menu = QMenu()
        self._build()
        self.setContextMenu(self._menu)
        # Le menu ouvert est le bon moment pour rafraichir les entrees audio :
        # indisponible de le faire en continu, et l'utilisateur voit l'etat a
        # l'instant ou il le demande.
        self._menu.aboutToShow.connect(self.audio_refresh_requested.emit)
        self._refresh_tooltip()
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

        settings_action = QAction("Réglages…", self._menu)
        settings_action.triggered.connect(self.settings_requested.emit)
        self._menu.addAction(settings_action)

        dashboard_action = QAction("Tableau de bord…", self._menu)
        dashboard_action.setToolTip("Temps gagné, modèles, dépenses, statistiques d'usage")
        dashboard_action.triggered.connect(self.stats_requested.emit)
        self._menu.addAction(dashboard_action)

        self.import_action = QAction("Importer des fichiers audio…", self._menu)
        self.import_action.setToolTip(
            "Appel, réunion, vocal WhatsApp : transcrire, diariser et ranger "
            "dans la bibliothèque (sélection multiple possible)"
        )
        self.import_action.triggered.connect(self.import_requested.emit)
        self._menu.addAction(self.import_action)

        history_action = QAction("Bibliothèque (dictées, imports)…", self._menu)
        history_action.setToolTip(
            "Réécouter les dictées conservées, copier leur texte, les retranscrire"
        )
        history_action.triggered.connect(self.history_requested.emit)
        self._menu.addAction(history_action)
        self._menu.addSeparator()

        self.toggle_action = QAction(f"Dicter ({self._hotkey_label})", self._menu)
        self.toggle_action.triggered.connect(self.toggle_requested.emit)
        self._menu.addAction(self.toggle_action)

        self.call_action = QAction("Enregistrer un appel (micro + son système)…", self._menu)
        self.call_action.setToolTip(
            "Enregistre l'appel en deux pistes — toi et tes interlocuteurs — puis "
            "le transcrit et le range dans la bibliothèque"
        )
        self.call_action.triggered.connect(self.call_toggle_requested.emit)
        self._menu.addAction(self.call_action)

        # --- entrees audio : etat du micro et du son du systeme ---
        self.audio_menu = self._menu.addMenu("Entrées audio")
        self.mic_action = QAction("Micro : …", self.audio_menu)
        self.mic_action.setToolTip("Clique pour vérifier les entrées audio maintenant")
        self.mic_action.triggered.connect(self.audio_test_requested.emit)
        self.audio_menu.addAction(self.mic_action)
        self.system_action = QAction("Son du système : …", self.audio_menu)
        self.system_action.setToolTip("Clique pour vérifier les entrées audio maintenant")
        self.system_action.triggered.connect(self.audio_test_requested.emit)
        self.audio_menu.addAction(self.system_action)
        self.audio_menu.addSeparator()
        audio_test = QAction("Tester les entrées…", self.audio_menu)
        audio_test.triggered.connect(self.audio_test_requested.emit)
        self.audio_menu.addAction(audio_test)
        audio_refresh = QAction("Actualiser", self.audio_menu)
        audio_refresh.triggered.connect(self.audio_refresh_requested.emit)
        self.audio_menu.addAction(audio_refresh)

        show_action = QAction("Afficher la pilule", self._menu)
        show_action.triggered.connect(self.show_requested.emit)

        display_menu = self._menu.addMenu("Affichage")
        display_menu.addAction(show_action)

        self.taskbar_action = QAction("Garder dans la barre des tâches", display_menu)
        self.taskbar_action.setCheckable(True)
        self.taskbar_action.setChecked(self._show_in_taskbar)
        self.taskbar_action.toggled.connect(self.taskbar_toggled.emit)
        display_menu.addAction(self.taskbar_action)

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
        self._status_text = text or ""
        self.status_action.setText(f"Vox {__version__} — {text}")
        self._refresh_tooltip()

    def set_audio_status(self, statuses: list[SourceStatus]) -> None:
        """Met a jour les indicateurs micro / son du systeme.

        L'icone de la zone de notification change de pastille (verte, orange,
        rouge) : l'etat se voit sans ouvrir le menu ; le detail est dans le
        menu et dans l'infobulle.
        """
        self._audio_statuses = list(statuses or [])
        for status in self._audio_statuses:
            if status.kind == KIND_MIC:
                action = self.mic_action
            elif status.kind == KIND_SYSTEM:
                action = self.system_action
            else:
                continue
            action.setText(status.summary)
            action.setIcon(make_dot_icon("ok" if status.available else "error"))
        if self._audio_statuses and not self._call_recording:
            self.setIcon(make_app_icon(dot=status_dot(self._audio_statuses)))
        if not self._call_recording:
            self.call_action.setIcon(self._call_icon())
        self._refresh_tooltip()

    def set_call_state(self, recording: bool, elapsed: int = 0) -> None:
        """Texte et pastille de l'entree « Enregistrer un appel »."""
        self._call_recording = bool(recording)
        if self._call_recording:
            self.call_action.setText(
                f"Arrêter l'enregistrement ({format_elapsed(elapsed)})"
            )
            self.call_action.setIcon(make_dot_icon("recording"))
            self.setIcon(make_app_icon(dot="recording"))
        else:
            self.call_action.setText("Enregistrer un appel (micro + son système)…")
            self.call_action.setIcon(self._call_icon())
            if self._audio_statuses:
                self.setIcon(make_app_icon(dot=status_dot(self._audio_statuses)))

    def _call_icon(self):
        """Pastille de disponibilité : le son du système est-il capturable ?"""
        system = next(
            (item for item in self._audio_statuses if item.kind == KIND_SYSTEM), None
        )
        if system is None:
            return make_dot_icon("warn")
        return make_dot_icon("ok" if system.available else "warn")

    def _refresh_tooltip(self) -> None:
        lines = [f"Vox {__version__}"]
        if self._status_text:
            lines.append(self._status_text)
        for status in self._audio_statuses:
            if status.available:
                lines.append(f"{status.label} : {status.name}")
                if status.active:
                    lines.append(f"    son en cours : {', '.join(status.active)}")
            else:
                lines.append(f"{status.label} : {status.detail}")
        lines.append(self._hotkey_label)
        self.setToolTip("\n".join(lines))

    def set_update_available(self, version: str) -> None:
        """Fait apparaitre (ou masque si `version` est vide) l'entree de menu."""
        if not version:
            self.update_action.setVisible(False)
            return
        self.update_action.setText(f"Mise à jour {version} disponible…")
        self.update_action.setVisible(True)

    # ------------------------------------------------------------------
    def _on_activated(self, reason) -> None:
        # Clic gauche (ou double-clic) : on ouvre la grande fenetre de reglages,
        # pas la pilule. Clic droit : le menu contextuel (comportement Qt).
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.settings_requested.emit()
        elif reason == QSystemTrayIcon.MiddleClick:
            self.toggle_requested.emit()
