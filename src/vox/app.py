"""Assemblage de l'application : tray, pilule, raccourci global, pipeline."""

from __future__ import annotations

import contextlib
import logging
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import __version__, injector, models, sounds
from . import config as config_module
from .config import HOTKEY_CHOICES, Settings
from .hotkey import EVENT_CANCEL, EVENT_START, EVENT_STOP, HotkeyManager
from .models import Catalogue
from .paths import data_dir, project_root
from .pipeline import Pipeline
from .reword import TONES
from .ui.overlay import Overlay
from .ui.settings_window import SettingsWindow
from .ui.stats_window import StatsWindow
from .ui.theme import app_qss
from .ui.tray import Tray
from .ui.widgets import make_app_icon

log = logging.getLogger("vox")

SERVER_NAME = "vox-single-instance"
AUTOSTART_NAME = "Vox"


class _CatalogueLoader(QThread):
    """Charge le catalogue de modeles sans bloquer l'interface."""

    loaded = Signal(object)

    def __init__(self, provider: str, api_key: str, force: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._api_key = api_key
        self._force = force

    def run(self) -> None:
        self.loaded.emit(models.load(self._provider, self._api_key, force_refresh=self._force))


class VoxApp(QObject):
    """Controleur principal."""

    def __init__(self, qapp: QApplication) -> None:
        super().__init__()
        self.qapp = qapp
        self.settings: Settings = config_module.load()
        self.catalogue = models.fallback(self.settings.provider)
        self._settings_window: SettingsWindow | None = None
        self._stats_window: StatsWindow | None = None
        self._pending_enter = False
        self._recording_started_at = 0.0
        self._loader: _CatalogueLoader | None = None

        self.pipeline = Pipeline(self.settings)
        self.hotkey = HotkeyManager(
            self.settings.hotkey,
            self.settings.hotkey_mode,
            self.settings.double_tap_enter,
        )
        self.overlay = Overlay()
        self.tray = Tray(
            make_app_icon(),
            self.hotkey_label,
            self.settings.autostart,
            self.settings.show_in_taskbar,
        )

        self._wire()
        self._apply_theme()
        self._refresh_model_views()
        self._install_hotkey()

    # ------------------------------------------------------------------
    # Mise en place
    # ------------------------------------------------------------------
    @property
    def hotkey_label(self) -> str:
        return HOTKEY_CHOICES.get(self.settings.hotkey, self.settings.hotkey)

    def _wire(self) -> None:
        self.tray.toggle_requested.connect(self.pipeline.toggle)
        self.tray.show_requested.connect(lambda: self.overlay.pin(4))
        self.tray.reinsert_requested.connect(self.pipeline.reinsert_last)
        self.tray.copy_requested.connect(self.pipeline.copy_last)
        self.tray.settings_requested.connect(self.open_settings)
        self.tray.stats_requested.connect(self.open_stats)
        self.tray.quit_requested.connect(self.quit)
        self.tray.model_selected.connect(self.set_model)
        self.tray.reword_toggled.connect(self.set_reword_enabled)
        self.tray.tone_selected.connect(self.set_tone)
        self.tray.icon_help_requested.connect(self.show_icon_help)
        self.tray.autostart_toggled.connect(self.set_autostart_enabled)
        self.tray.taskbar_toggled.connect(self.set_taskbar_mode)

        self.overlay.model_selected.connect(self.set_model)
        self.overlay.reword_toggled.connect(self.set_reword_enabled)
        self.overlay.reinsert_requested.connect(self.pipeline.reinsert_last)
        self.overlay.copy_requested.connect(self.pipeline.copy_last)
        self.overlay.settings_requested.connect(self.open_settings)
        self.overlay.moved.connect(self._on_overlay_moved)

        self.pipeline.recording_changed.connect(self._on_recording_changed)
        self.pipeline.status_changed.connect(self._on_status_changed)
        self.pipeline.notice.connect(self._on_notice)
        self.pipeline.finalized.connect(self._on_finalized)
        self.pipeline.transcript_ready.connect(self._on_transcript)

        self._hotkey_timer = QTimer(self)
        self._hotkey_timer.setInterval(35)
        self._hotkey_timer.timeout.connect(self._drain_hotkey)

        self._level_timer = QTimer(self)
        self._level_timer.setInterval(33)
        self._level_timer.timeout.connect(self._tick_level)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(200)
        self._clock_timer.timeout.connect(self._tick_clock)

    def start(self) -> None:
        self.overlay.restore_position(self.settings.overlay_position)
        self.overlay.set_state("idle", detail=f"{self.hotkey_label} pour dicter")
        if self.settings.show_in_taskbar:
            # Doit preceder tout hide_pill(), qui est ignore dans ce mode.
            self.overlay.set_taskbar_visible(True)
            self.overlay.pin()
        else:
            self.overlay.hide_pill()
        sounds.dump_wavs()
        self.tray.show()
        self.tray.set_status("Prêt")
        self._hotkey_timer.start()
        self._load_catalogue()

        if not self.settings.effective_key:
            self.overlay.show_pill(9)
            self.overlay.set_state(
                "error",
                "Clé OpenRouter manquante : ouvre les réglages (icône engrenage).",
            )
            QTimer.singleShot(1200, self.open_settings)
        elif self.settings.notify_on_start:
            QTimer.singleShot(900, self._greet)

    def _greet(self) -> None:
        """Confirme visuellement que Vox tourne, même si l'icône est masquée."""
        first_run = not self.settings.extras.get("greeted")
        if first_run:
            self.settings.extras["greeted"] = True
            config_module.save(self.settings)
            detail = (
                "L'icône se trouve dans la zone de notification, derrière le "
                "chevron ^ à gauche de l'horloge. Clic droit dessus pour le menu."
            )
        else:
            detail = f"{self.hotkey_label} pour dicter."
        self.tray.showMessage(
            "Vox est actif",
            detail,
            QSystemTrayIcon.Information,
            9000 if first_run else 4000,
        )

    def show_icon_help(self) -> None:
        """Ouvre la page Windows qui régit les icônes de la zone de notification."""
        self.tray.showMessage(
            "Où se trouve l'icône ?",
            "Windows range les nouvelles icônes derrière le chevron ^, près de "
            "l'horloge. Pour l'y épingler : Paramètres → Personnalisation → Barre "
            "des tâches → Autres icônes du système → active Vox.",
            QSystemTrayIcon.Information,
            15000,
        )
        QTimer.singleShot(400, lambda: QDesktopServices.openUrl(QUrl("ms-settings:taskbar")))

    def set_autostart_enabled(self, enabled: bool) -> None:
        self.settings.autostart = enabled
        config_module.save(self.settings)
        set_autostart(enabled)
        self.tray.set_autostart(enabled)
        self._on_notice(
            "info",
            "Vox se lancera au démarrage de Windows." if enabled else "Démarrage auto désactivé.",
        )

    def set_taskbar_mode(self, enabled: bool) -> None:
        """Garde la pilule dans la barre des tâches, comme une application classique."""
        self.settings.show_in_taskbar = enabled
        config_module.save(self.settings)
        self.tray.set_show_in_taskbar(enabled)
        self.overlay.set_taskbar_visible(enabled)
        if enabled:
            self.overlay.pin()
            self._on_notice("info", "Pilule gardée dans la barre des tâches.")
        else:
            self.overlay.set_state("idle", detail=f"{self.hotkey_label} pour dicter")
            self._on_notice("info", "Pilule revenue dans la zone de notification.")

    # ------------------------------------------------------------------
    # Catalogue de modeles
    # ------------------------------------------------------------------
    def _load_catalogue(self, force: bool = False) -> None:
        self._loader = _CatalogueLoader(
            self.settings.provider, self.settings.effective_key, force, self
        )
        self._loader.loaded.connect(self._on_catalogue)
        self._loader.start()

    def _on_catalogue(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue
        if catalogue.error:
            log.info("Catalogue en repli : %s", catalogue.error)
        self._refresh_model_views()

    def _refresh_model_views(self) -> None:
        self.tray.set_models(self.catalogue.stt, self.settings.stt_model)
        self.overlay.set_models(self.catalogue.stt, self.settings.stt_model)
        self.tray.set_reword_enabled(self.settings.reword_enabled)
        self.tray.set_tone(self.settings.reword_tone)
        self.overlay.set_reword_enabled(self.settings.reword_enabled)
        tone = TONES.get(self.settings.reword_tone, TONES["clean"])
        self.overlay.set_tone(self.settings.reword_tone, tone["label"])

    # ------------------------------------------------------------------
    # Raccourci global
    # ------------------------------------------------------------------
    def _install_hotkey(self) -> None:
        self.hotkey.configure(
            combo=self.settings.hotkey,
            mode=self.settings.hotkey_mode,
            double_tap_enter=self.settings.double_tap_enter,
        )
        self.hotkey.start()
        if not self.hotkey.error:
            log.info(
                "Raccourci global actif : %s (%s)",
                self.settings.hotkey,
                self.settings.hotkey_mode,
            )
            self.tray.set_status("Prêt")
            return

        log.warning("Raccourci global indisponible : %s", self.hotkey.error)
        self.tray.set_status("Raccourci indisponible")
        QTimer.singleShot(
            1500,
            lambda: self._on_notice("error", self.hotkey.error or "Raccourci indisponible."),
        )

    def _drain_hotkey(self) -> None:
        for event in self.hotkey.drain():
            if event == EVENT_START:
                self._pending_enter = False
                self.pipeline.start_recording()
            elif event == EVENT_STOP:
                # L'Entree automatique n'a de sens qu'en mode bascule, ou le
                # second appui marque la fin. En push-to-talk, chaque relachement
                # est un arret : envoyer Entree serait un comportement surprise.
                if (
                    self.settings.hotkey_mode == "toggle"
                    and self.settings.double_tap_enter
                    and self.pipeline.recording
                ):
                    self._pending_enter = True
                self.pipeline.stop_recording()
            elif event == EVENT_CANCEL:
                self.pipeline.cancel_recording()

    # ------------------------------------------------------------------
    # Etats
    # ------------------------------------------------------------------
    def _on_recording_changed(self, recording: bool) -> None:
        if recording:
            self._recording_started_at = time.monotonic()
            self.overlay.pin()
            self.overlay.set_state("recording", detail=f"{self.hotkey_label} pour arreter")
            self.tray.set_status("Enregistrement…")
            self._level_timer.start()
            self._clock_timer.start()
        else:
            self._level_timer.stop()
            self._clock_timer.stop()

    def _on_status_changed(self, state: str) -> None:
        if state == "cancelled":
            # Annulation discrete : pas de bulle, pas de son d'erreur.
            self.overlay.set_state("idle", detail=f"{self.hotkey_label} pour dicter")
            self.tray.set_status("Prêt")
            self.overlay.hide_pill()
        elif state == "idle":
            self.overlay.set_state("idle", detail=f"{self.hotkey_label} pour dicter")
            self.tray.set_status("Pret")
            self.overlay.pin(2)
        elif state == "transcribing":
            self.overlay.pin()
            self.overlay.set_state("transcribing", detail="Patientez…")
            self.tray.set_status("Transcription…")
        elif state == "rewording":
            self.overlay.pin()
            self.overlay.set_state("rewording", detail="Patientez…")
            self.tray.set_status("Reformulation…")
        elif state == "done":
            self.tray.set_status("Prêt")
            if self.settings.show_overlay_on_result:
                self.overlay.pin(4)
            else:
                self.overlay.hide_pill()
        elif state == "error":
            self.overlay.pin(7)
            self.tray.set_status("Erreur")

    def _on_notice(self, level: str, message: str) -> None:
        if level == "error":
            self.overlay.set_state("error", message)
            self.overlay.pin(7)
            if self.tray.isSystemTrayAvailable():
                self.tray.showMessage("Vox", message, QSystemTrayIcon.Warning, 6000)
        else:
            if not self.settings.show_overlay_on_result:
                return
            self.overlay.set_state("done", message)
            self.overlay.pin(3)

    def _on_transcript(self, text: str, _meta: dict) -> None:
        self.overlay.set_state("transcribing", detail=text[:70] + ("…" if len(text) > 70 else ""))

    def _on_finalized(self, text: str, _meta: dict) -> None:
        self.overlay.set_state("done", message=text)
        if self._pending_enter:
            self._pending_enter = False
            QTimer.singleShot(120, injector.press_enter)

    def _tick_level(self) -> None:
        self.overlay.push_level(self.pipeline.recorder.level)

    def _tick_clock(self) -> None:
        elapsed = int(time.monotonic() - self._recording_started_at)
        prefix = "Enregistrement " if self.pipeline.recorder.hit_max else ""
        self.overlay.set_state(
            "recording",
            detail=f"{prefix}{elapsed // 60:01d}:{elapsed % 60:02d}  ·  {self.hotkey_label} pour arreter",
        )
        if self.pipeline.recorder.hit_max:
            self.pipeline.stop_recording()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def set_model(self, model_id: str) -> None:
        self.settings.stt_model = model_id
        config_module.save(self.settings)
        self.pipeline.apply_settings(self.settings)
        self._refresh_model_views()
        self.overlay.set_state("idle", detail=f"Modele : {model_id}")

    def set_reword_enabled(self, enabled: bool) -> None:
        self.settings.reword_enabled = enabled
        config_module.save(self.settings)
        self.tray.set_reword_enabled(enabled)
        self.overlay.set_reword_enabled(enabled)

    def set_tone(self, tone: str) -> None:
        self.settings.reword_tone = tone
        config_module.save(self.settings)
        self._refresh_model_views()

    def _on_overlay_moved(self, x: int, y: int) -> None:
        self.settings.overlay_position = [x, y]
        config_module.save(self.settings)

    def open_settings(self) -> None:
        if self._settings_window is not None:
            self._settings_window.raise_()
            self._settings_window.activateWindow()
            return
        window = SettingsWindow(self.settings, self.catalogue)
        self._settings_window = window
        if window.exec():
            self._commit_settings(window.values())
        self._settings_window = None

    def open_stats(self) -> None:
        """Ouvre (ou ramene au premier plan) la fenetre de statistiques."""
        if self._stats_window is None:
            self._stats_window = StatsWindow(self.settings)
        self._stats_window.apply_theme(self.settings.theme)
        self._stats_window.refresh()
        self._stats_window.show()
        self._stats_window.raise_()
        self._stats_window.activateWindow()

    def _commit_settings(self, settings: Settings) -> None:
        previous = self.settings
        self.settings = settings

        if previous.provider != settings.provider:
            stt_default, chat_default = config_module.default_models(settings.provider)
            settings.stt_model = stt_default
            settings.chat_model = chat_default
            self.settings = settings

        config_module.save(settings)
        self.pipeline.apply_settings(settings)

        if (previous.theme, previous.hotkey, previous.hotkey_mode) != (
            settings.theme,
            settings.hotkey,
            settings.hotkey_mode,
        ):
            self._apply_theme()
            self.hotkey.stop()
            self._install_hotkey()

        if previous.autostart != settings.autostart:
            set_autostart(settings.autostart)
        self.tray.set_autostart(settings.autostart)
        self.tray.set_show_in_taskbar(settings.show_in_taskbar)
        if previous.show_in_taskbar != settings.show_in_taskbar:
            self.set_taskbar_mode(settings.show_in_taskbar)

        self.overlay.restore_position(settings.overlay_position)
        self._refresh_model_views()
        self.overlay.set_state("idle", detail=f"{self.hotkey_label} pour dicter")
        self._load_catalogue(force=True)
        self._on_notice("info", "Réglages enregistrés.")

    def _apply_theme(self) -> None:
        self.qapp.setStyleSheet(app_qss(self.settings.theme))
        self.overlay.apply_theme(self.settings.theme)
        if self._stats_window is not None:
            self._stats_window.apply_theme(self.settings.theme)

    # ------------------------------------------------------------------
    def quit(self) -> None:
        self._hotkey_timer.stop()
        self._level_timer.stop()
        self._clock_timer.stop()
        self.hotkey.stop()
        self.pipeline.shutdown()
        self.tray.hide()
        self.qapp.quit()


# ----------------------------------------------------------------------
# Instance unique et demarrage automatique
# ----------------------------------------------------------------------
def acquire_single_instance(parent: QObject | None = None) -> QLocalServer | None:
    """Renvoie un serveur si on est la premiere instance, sinon None."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(400):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(400)
        socket.disconnectFromServer()
        return None

    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer(parent)
    if not server.listen(SERVER_NAME):
        return None
    return server


def _launcher_command() -> str:
    """Commande inscrite au demarrage automatique de Windows."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}"'
    project = project_root()
    venv_pythonw = project / ".venv" / "Scripts" / "pythonw.exe"
    launcher = data_dir() / "vox_autostart.cmd"
    interpreter = venv_pythonw if venv_pythonw.exists() else Path(sys.executable)
    launcher.write_text(
        "@echo off\r\n"
        f'cd /d "{project}"\r\n'
        f'start "" "{interpreter}" -m vox\r\n',
        encoding="utf-8",
    )
    return f'"{launcher}"'


def set_autostart(enabled: bool) -> None:
    """Inscrit ou retire Vox du demarrage automatique de Windows."""
    if sys.platform != "win32":
        return
    import winreg

    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, _launcher_command())
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, AUTOSTART_NAME)
    except OSError:
        pass


__all__ = ["VoxApp", "__version__", "acquire_single_instance", "set_autostart"]
