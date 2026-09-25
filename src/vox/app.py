"""Assemblage de l'application : tray, pilule, raccourci global, pipeline."""

from __future__ import annotations

import logging
import sys
import time

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QDialog, QSystemTrayIcon

from . import __version__, injector, models, recordings, sounds, updates
from . import config as config_module
from .autostart import set_autostart
from .config import HOTKEY_CHOICES, Settings
from .hotkey import EVENT_CANCEL, EVENT_START, EVENT_STOP, HotkeyManager
from .models import Catalogue
from .pipeline import Pipeline
from .reword import TONES
from .ui.history_window import RecordingsWindow
from .ui.overlay import Overlay
from .ui.settings_window import SettingsWindow
from .ui.stats_window import StatsWindow
from .ui.theme import app_qss
from .ui.tray import Tray
from .ui.widgets import make_app_icon

log = logging.getLogger("vox")

SERVER_NAME = "vox-single-instance"

# Duree d'affichage d'un message d'erreur quand la pilule se cache d'elle-meme.
NOTICE_SECONDS = 6


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


class _UpdateChecker(QThread):
    """Interroge le manifeste de version, hors du thread d'interface."""

    checked = Signal(object, str)

    def __init__(self, url: str, current: str, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._current = current

    def run(self) -> None:
        info, reason = updates.check(self._url, self._current)
        self.checked.emit(info, reason)


class VoxApp(QObject):
    """Controleur principal."""

    def __init__(self, qapp: QApplication) -> None:
        super().__init__()
        self.qapp = qapp
        self.settings: Settings = config_module.load()
        self.catalogue = models.fallback(self.settings.provider)
        self._settings_window: SettingsWindow | None = None
        self._stats_window: StatsWindow | None = None
        self._recordings_window: RecordingsWindow | None = None
        self._pending_enter = False
        self._recording_started_at = 0.0
        self._loader: _CatalogueLoader | None = None
        self._update_checker: _UpdateChecker | None = None
        self._update_info: updates.UpdateInfo | None = None

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
        self.tray.show_requested.connect(self.overlay.show_pill)
        self.tray.reinsert_requested.connect(self.pipeline.reinsert_last)
        self.tray.copy_requested.connect(self.pipeline.copy_last)
        self.tray.settings_requested.connect(self.open_settings)
        self.tray.stats_requested.connect(self.open_stats)
        self.tray.history_requested.connect(self.open_recordings)
        self.tray.update_requested.connect(self.open_update)
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
        self.pipeline.retranscribed.connect(self._on_retranscribed)

        self._hotkey_timer = QTimer(self)
        self._hotkey_timer.setInterval(35)
        self._hotkey_timer.timeout.connect(self._drain_hotkey)

        self._level_timer = QTimer(self)
        self._level_timer.setInterval(33)
        self._level_timer.timeout.connect(self._tick_level)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(200)
        self._clock_timer.timeout.connect(self._tick_clock)

        # Verification des mises a jour : une fois au demarrage, puis 2x par jour.
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(12 * 3600 * 1000)
        self._update_timer.timeout.connect(self.check_updates)

    def start(self) -> None:
        self.overlay.restore_position(self.settings.overlay_position)
        self.overlay.set_hide_delay(self.settings.overlay_hide_delay)
        self.overlay.set_state("idle", detail=f"{self.hotkey_label} pour dicter")
        if self.settings.show_in_taskbar:
            # Doit preceder tout hide_pill(), qui est ignore dans ce mode.
            self.overlay.set_taskbar_visible(True)
            self.overlay.show_pill()
        else:
            self.overlay.hide_pill()
        sounds.dump_wavs()
        # Purge des vieux enregistrements, en tache de fond pour ne pas retarder
        # le demarrage.
        QTimer.singleShot(4000, self._prune_recordings)
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

        QTimer.singleShot(6000, self.check_updates)
        self._update_timer.start()

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
        """Aide a retrouver l'icone dans la zone de notification."""
        if sys.platform == "win32":
            self.tray.showMessage(
                "Où se trouve l'icône ?",
                "Windows range les nouvelles icônes derrière le chevron ^, près de "
                "l'horloge. Pour l'y épingler : Paramètres → Personnalisation → Barre "
                "des tâches → Autres icônes du système → active Vox.",
                QSystemTrayIcon.Information,
                15000,
            )
            QTimer.singleShot(400, lambda: QDesktopServices.openUrl(QUrl("ms-settings:taskbar")))
        else:
            self.tray.showMessage(
                "Où se trouve l'icône ?",
                "L'icône se trouve dans la barre système, près de l'horloge. Sur GNOME, "
                "elle n'apparaît qu'avec l'extension « AppIndicator » : installe-la puis "
                "relance Vox.",
                QSystemTrayIcon.Information,
                15000,
            )

    def set_autostart_enabled(self, enabled: bool) -> None:
        self.settings.autostart = enabled
        config_module.save(self.settings)
        set_autostart(enabled)
        self.tray.set_autostart(enabled)
        if enabled:
            self._on_notice("info", "Vox se lancera au démarrage de la session.")
        else:
            self._on_notice("info", "Démarrage auto désactivé.")

    def set_taskbar_mode(self, enabled: bool) -> None:
        """Garde la pilule dans la barre des tâches, comme une application classique."""
        self.settings.show_in_taskbar = enabled
        config_module.save(self.settings)
        self.tray.set_show_in_taskbar(enabled)
        self.overlay.set_taskbar_visible(enabled)
        if enabled:
            self.overlay.show_pill()
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
        self.overlay.set_state("error", "Raccourci global indisponible")
        self.overlay.show_pill(12)
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
    def _show_progress(self) -> None:
        """Pilule pendant le traitement.

        Par defaut elle disparait des la fin de l'ecoute : l'utilisateur n'a
        rien a fermer. Le reglage inverse l'ancien comportement.
        """
        if self.settings.hide_after_listening:
            self.overlay.hide_pill()
        else:
            self.overlay.show_pill()

    def _on_recording_changed(self, recording: bool) -> None:
        if recording:
            self._recording_started_at = time.monotonic()
            self.overlay.show_pill()
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
            self._show_progress()
        elif state == "transcribing":
            self.overlay.set_state("transcribing", detail="Patientez…")
            self.tray.set_status("Transcription…")
            self._show_progress()
        elif state == "rewording":
            self.overlay.set_state("rewording", detail="Patientez…")
            self.tray.set_status("Reformulation…")
            self._show_progress()
        elif state == "done":
            self.tray.set_status("Prêt")
            # L'etat doit sortir de « transcription », sinon le masquage
            # automatique se croit encore en plein traitement et ne masque
            # jamais la pilule.
            self.overlay.set_state("done", message=self.pipeline.last_final)
            if self.settings.show_overlay_on_result and not self.settings.hide_after_listening:
                self.overlay.show_pill()
            else:
                self.overlay.hide_pill()
        elif state == "error":
            self.overlay.show_pill(NOTICE_SECONDS)
            self.tray.set_status("Erreur")

    def _on_notice(self, level: str, message: str) -> None:
        # Un message informe d'un echec ou d'une absence de texte : il doit se
        # voir, mais pas rester. Il s'efface donc tout seul.
        delay = NOTICE_SECONDS if self.settings.hide_after_listening else None
        if level == "error":
            self.overlay.set_state("error", message)
            self.overlay.show_pill(delay)
            if self.tray.isSystemTrayAvailable():
                self.tray.showMessage("Vox", message, QSystemTrayIcon.Warning, 6000)
        else:
            if not self.settings.show_overlay_on_result:
                return
            self.overlay.set_state("done", message)
            self.overlay.show_pill(delay)

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

    def open_settings(self, parent=None) -> None:
        if self._settings_window is not None:
            self._settings_window.raise_()
            self._settings_window.activateWindow()
            return
        window = SettingsWindow(self.settings, self.catalogue, parent)
        # Fenetre non modale : les reglages et la pilule doivent rester
        # independants, sinon on ne peut plus fermer la pilule (ni interagir
        # avec le menu) tant que les reglages sont ouverts.
        window.setModal(False)
        # Le bouton « Ouvrir les enregistrements » ferme d'abord les reglages.
        state = {"recordings": False}

        def _on_open_recordings() -> None:
            state["recordings"] = True
            window.accept()

        window.open_recordings_requested.connect(_on_open_recordings)

        def _on_finished(result: int) -> None:
            self._settings_window = None
            if result == QDialog.Accepted:
                self._commit_settings(window.values())
            if state["recordings"]:
                state["recordings"] = False
                self.open_recordings()

        window.finished.connect(_on_finished)
        self._settings_window = window
        window.show()
        window.raise_()
        window.activateWindow()

    def check_updates(self) -> None:
        """Interroge le manifeste de version (silencieux en cas d'echec)."""
        if not self.settings.check_updates:
            return
        url = (self.settings.update_manifest_url or "").strip()
        if not url:
            return
        self._update_checker = _UpdateChecker(url, __version__, self)
        self._update_checker.checked.connect(self._on_update_checked)
        self._update_checker.start()

    def _on_update_checked(self, info: updates.UpdateInfo | None, reason: str) -> None:
        if info is None:
            log.info("Mise à jour : %s", reason)
            return
        self._update_info = info
        log.info("Mise à jour disponible : %s", info.version)
        self.tray.set_update_available(info.version)
        notes = (info.notes or "").strip()
        self.tray.showMessage(
            f"Vox {info.version} est disponible",
            (notes + "\n" if notes else "")
            + "Clic droit sur l'icône → Mise à jour disponible…",
            QSystemTrayIcon.Information,
            12000,
        )

    def open_update(self) -> None:
        """Ouvre les reglages sur l'onglet « Mises a jour »."""
        info = self._update_info
        if info is None and not self.settings.update_manifest_url:
            self._on_notice("info", "Aucune adresse de mise à jour configurée.")
            return
        self.open_settings()
        window = self._settings_window
        if window is None:
            return
        if info is not None:
            window.present_update(info)
        else:
            window.show_updates_tab()

    def open_stats(self) -> None:
        """Ouvre (ou ramene au premier plan) la fenetre de statistiques."""
        if self._stats_window is None:
            self._stats_window = StatsWindow(self.settings)
        self._stats_window.apply_theme(self.settings.theme)
        self._stats_window.refresh()
        self._stats_window.show()
        self._stats_window.raise_()
        self._stats_window.activateWindow()

    def open_recordings(self) -> None:
        """Ouvre (ou ramene au premier plan) l'historique des enregistrements."""
        if self._recordings_window is None:
            window = RecordingsWindow(self.settings)
            window.copy_requested.connect(self.pipeline.copy_text)
            window.insert_requested.connect(self.pipeline.insert_text)
            window.retranscribe_requested.connect(self.pipeline.retranscribe)
            window.delete_requested.connect(self._delete_recording)
            # Retour vers les reglages depuis l'historique : le dialogue prend
            # la fenetre d'historique comme parent, pour s'ouvrir par-dessus.
            window.settings_requested.connect(
                lambda: self.open_settings(parent=self._recordings_window)
            )
            self._recordings_window = window
        self._recordings_window.apply_theme(self.settings.theme)
        self._recordings_window.refresh()
        self._recordings_window.show()
        self._recordings_window.raise_()
        self._recordings_window.activateWindow()

    def _delete_recording(self, audio: str) -> None:
        recordings.delete(audio)
        if self._recordings_window is not None:
            self._recordings_window.on_deleted(audio)

    def _on_retranscribed(self, audio: str, text: str) -> None:
        if self._recordings_window is not None:
            self._recordings_window.on_retranscribed(audio, text)

    def _prune_recordings(self) -> None:
        """Supprime les enregistrements plus vieux que la duree de conservation."""
        if self.settings.recording_retention_days <= 0:
            return
        removed = recordings.prune(self.settings.recording_retention_days)
        if removed and self._recordings_window is not None:
            self._recordings_window.refresh()

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
        self.overlay.set_hide_delay(settings.overlay_hide_delay)

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
        if self._recordings_window is not None:
            self._recordings_window.apply_theme(self.settings.theme)

    # ------------------------------------------------------------------
    def quit(self) -> None:
        self._hotkey_timer.stop()
        self._level_timer.stop()
        self._clock_timer.stop()
        self._update_timer.stop()
        self.hotkey.stop()
        self.pipeline.shutdown()
        self.tray.hide()
        self.qapp.quit()


# ----------------------------------------------------------------------
# Instance unique et demarrage automatique
# ----------------------------------------------------------------------
def acquire_single_instance(
    parent: QObject | None = None, message: str = "show"
) -> QLocalServer | None:
    """Renvoie un serveur si on est la premiere instance, sinon None.

    Si Vox tourne deja, `message` lui est transmis : « show » affiche la pilule,
    « recordings » ouvre la fenetre des enregistrements. C'est ce qui permet au
    raccourci « Mes enregistrements » de fonctionner meme quand Vox tourne.
    """
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(400):
        socket.write(message.encode("ascii", "ignore"))
        socket.flush()
        socket.waitForBytesWritten(400)
        socket.disconnectFromServer()
        return None

    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer(parent)
    if not server.listen(SERVER_NAME):
        return None
    return server


def handle_second_instance(server: QLocalServer, app: VoxApp) -> None:
    """Repond a une deuxieme instance : lit sa demande et agit."""
    while server.hasPendingConnections():
        connection = server.nextPendingConnection()
        if connection is None:
            continue
        # La demande est ecrite puis la socket fermee : sans cette attente, on
        # lirait parfois une socket encore vide.
        connection.waitForReadyRead(300)
        request = bytes(connection.readAll()).decode("ascii", "ignore").strip()
        connection.disconnectFromServer()
        connection.deleteLater()
        log.info("Deuxieme instance : demande « %s »", request or "show")
        if request == "recordings":
            app.open_recordings()
        else:
            app.overlay.show_pill()


__all__ = [
    "VoxApp",
    "__version__",
    "acquire_single_instance",
    "handle_second_instance",
    "set_autostart",
]
