"""Assemblage de l'application : tray, pilule, raccourci global, pipeline."""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QSystemTrayIcon

from . import __version__, calls, injector, library, models, recordings, sounds, sources, updates
from . import config as config_module
from .api import Client
from .autostart import set_autostart
from .config import HOTKEY_CHOICES, Settings
from .hotkey import EVENT_CANCEL, EVENT_START, EVENT_STOP, HotkeyManager
from .imports import (
    ImportCancelled,
    Progress,
    clean_transcript_with_settings,
    process_file,
)
from .models import Catalogue
from .naming import infer_speaker_names
from .paths import calls_dir
from .pipeline import Pipeline
from .recorder import Recorder, RecorderError, SystemRecorder
from .reword import TONES
from .stats import format_duration
from .transcript import Transcript
from .ui.history_window import RecordingsWindow
from .ui.overlay import Overlay
from .ui.settings_window import SettingsWindow
from .ui.stats_window import StatsWindow
from .ui.theme import app_qss, build_palette
from .ui.tray import Tray, format_elapsed
from .ui.widgets import make_app_icon

log = logging.getLogger("vox")

SERVER_NAME = "vox-single-instance"

# Duree d'affichage d'un message d'erreur quand la pilule se cache d'elle-meme.
NOTICE_SECONDS = 6

# Enregistrement d'appel : deux pistes (micro + son du systeme), 4 h maximum ;
# en dessous de ce seuil, c'est un appui accidentel : on ne garde rien.
CALL_TITLE = "Enregistrement de l'appel"
MAX_CALL_SECONDS = 4 * 3600
MIN_CALL_SECONDS = 1.5


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


class _UpdateDownloader(QThread):
    """Telecharge le fichier de mise a jour hors du thread d'interface."""

    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, url: str, destination: Path, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._destination = destination

    def run(self) -> None:
        try:
            path = updates.download(
                self._url, self._destination, on_progress=self.progress.emit
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(path)


def _transcription_inputs(
    mic_wav: Path, mic_ok: bool, system_wav: Path | None, system_ok: bool
) -> tuple[Path, Path | None]:
    """Choisit les pistes a transcrire.

    Quand seul le son du systeme porte quelque chose (conference ecoutee sans
    parler), il devient la piste principale : sinon on enverrait du silence a
    l'API, qui hallucine.
    """
    if not mic_ok and system_ok and system_wav is not None:
        return system_wav, None
    return mic_wav, system_wav if system_ok else None


def _clean_import_transcript(
    transcript: Transcript,
    settings: Settings,
    cancel: threading.Event | None = None,
    progress=None,
) -> bool:
    """Nettoyage editorial automatique. Ne fait jamais echouer l'import.

    Renvoie True si le texte a effectivement ete retravaille (auquel cas
    l'appelant conserve une copie brute a cote).
    """
    if cancel is not None and cancel.is_set():
        transcript.warnings.append("Nettoyage ignoré : traitement annulé.")
        return False
    try:
        report = clean_transcript_with_settings(
            transcript, settings, progress=progress, cancel=cancel
        )
    except Exception as exc:
        transcript.warnings.append(f"Nettoyage ignoré : {exc}")
        return False
    cost = float(report.get("cout", 0.0) or 0.0)
    if cost:
        transcript.cost = round(transcript.cost + cost, 8)
    if report.get("annule"):
        transcript.warnings.append("Nettoyage interrompu : texte brut conservé.")
        return False
    if report.get("blocs_en_echec"):
        transcript.warnings.append(
            f"Nettoyage partiel : {report['blocs_en_echec']} bloc(s) laissé(s) brut(s)."
        )
    return bool(report.get("segments_modifies"))


class _ImportWorker(QThread):
    """Importe des fichiers audio hors du fil d'interface.

    Chaque fichier est transcrit, diarise puis range dans la bibliotheque.
    L'annulation est cooperative : `stop()` arme l'evenement lu par
    `imports.process_file`.
    """

    progress = Signal(int, int, str)
    imported = Signal(str)
    replaced = Signal(str)
    failed = Signal(str, str)
    cancelled = Signal()

    def __init__(
        self,
        paths: list[str],
        settings: Settings,
        replace_id: str = "",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._settings = settings
        self._replace_id = replace_id
        self._file_position = 0
        self.cancel = threading.Event()

    def stop(self) -> None:
        self.cancel.set()

    def run(self) -> None:
        for position, path in enumerate(self._paths, start=1):
            if self.cancel.is_set():
                self.cancelled.emit()
                return
            self._file_position = position
            try:
                result = process_file(
                    path,
                    self._settings,
                    progress=self._on_progress,
                    cancel=self.cancel,
                    expected_speakers=self._settings.import_speakers or None,
                    diarize=self._settings.import_diarize,
                )
            except ImportCancelled:
                self.cancelled.emit()
                return
            except Exception as exc:  # un fichier en echec ne bloque pas les autres
                library.add(None, path, status="erreur", error=str(exc))
                self.failed.emit(path, str(exc))
                continue
            transcript = result.transcript
            raw_copy: Transcript | None = None
            if self._settings.clean_imports:
                raw_copy = copy.deepcopy(transcript)
                if not self._clean(transcript):
                    raw_copy = None  # rien de nettoye : inutile de garder un doublon
            if self._replace_id:
                library.replace_transcript(
                    self._replace_id,
                    transcript,
                    model=result.model,
                    cost=transcript.cost,
                )
                if raw_copy is not None:
                    library.save_raw_copy(self._replace_id, raw_copy)
                self.replaced.emit(self._replace_id)
            else:
                entry = library.add(transcript, path, elapsed=result.elapsed)
                if raw_copy is not None:
                    library.save_raw_copy(entry.id, raw_copy)
                self.imported.emit(entry.id)

    def _clean(self, transcript: Transcript) -> bool:
        """Nettoyage editorial automatique, avec la progression dans l'UI."""
        if self.cancel.is_set():
            transcript.warnings.append("Nettoyage ignoré : import annulé.")
            return False
        self.progress.emit(0, 0, "Nettoyage éditorial…")
        return _clean_import_transcript(
            transcript,
            self._settings,
            self.cancel,
            progress=lambda done, total: self.progress.emit(
                done, total, f"Nettoyage éditorial : {done}/{total} bloc(s)"
            ),
        )

    def _on_progress(self, progress: Progress) -> None:
        message = progress.message
        if len(self._paths) > 1:
            name = Path(self._paths[self._file_position - 1]).name
            message = f"[{self._file_position}/{len(self._paths)}] {name} — {message}"
        self.progress.emit(progress.done, progress.total, message)


class _CallTranscriber(QThread):
    """Transcrit les deux pistes d'un appel et cree l'entree de bibliotheque."""

    progress = Signal(int, int, str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        mic_path: Path,
        system_path: Path | None,
        offset: float,
        title: str,
        settings: Settings,
        at: str = "",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._mic_path = mic_path
        self._system_path = system_path
        self._offset = offset
        self._title = title
        self._settings = settings
        self._at = at
        self.cancel = threading.Event()

    def stop(self) -> None:
        self.cancel.set()

    def run(self) -> None:
        try:
            transcript = calls.process_call(
                self._mic_path,
                self._system_path,
                self._settings,
                offset=self._offset,
                title=self._title,
                progress=self._on_progress,
                cancel=self.cancel,
            )
        except ImportCancelled:
            self._keep_recording("Transcription annulée.")
            self.failed.emit("Transcription annulée.")
            return
        except Exception as exc:
            self._keep_recording(str(exc))
            self.failed.emit(str(exc))
            return

        raw_copy: Transcript | None = None
        if self._settings.clean_imports:
            raw_copy = copy.deepcopy(transcript)
            if not self._clean(transcript):
                raw_copy = None
        entry = library.add(
            transcript,
            self._mic_path,
            title=self._title,
            kind=library.KIND_CALL,
            at=self._at,
        )
        if raw_copy is not None:
            library.save_raw_copy(entry.id, raw_copy)
        self.done.emit(entry.id)

    def _clean(self, transcript: Transcript) -> bool:
        if self.cancel.is_set():
            return False
        self.progress.emit(0, 0, "Nettoyage éditorial…")
        return _clean_import_transcript(
            transcript,
            self._settings,
            self.cancel,
            progress=lambda done, total: self.progress.emit(
                done, total, f"Nettoyage éditorial : {done}/{total} bloc(s)"
            ),
        )

    def _keep_recording(self, reason: str) -> None:
        """Transcription impossible : l'audio reste dans la bibliothèque."""
        mic = Path(self._mic_path)
        if not mic.exists():
            return
        library.add(
            None,
            mic,
            title=self._title,
            kind=library.KIND_CALL,
            status="erreur",
            error=reason,
            at=self._at,
            extras={
                "pistes": {
                    "micro": str(mic),
                    "systeme": str(self._system_path) if self._system_path else "",
                }
            },
        )

    def _on_progress(self, item: Progress) -> None:
        self.progress.emit(item.done, item.total, item.message)


class _NamesWorker(QThread):
    """Demande a un modele de conversation de retrouver les prenoms."""

    done = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, entry_id: str, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._entry_id = entry_id
        self._settings = settings

    def run(self) -> None:
        transcript = library.load_transcript(self._entry_id)
        if transcript is None:
            self.failed.emit(self._entry_id, "Transcript introuvable.")
            return
        key = config_module.key_for("openrouter", self._settings)
        if not key:
            self.failed.emit(self._entry_id, "Aucune clé OpenRouter pour l'analyse.")
            return
        try:
            with Client("openrouter", key, timeout=180.0) as client:
                mapping = infer_speaker_names(
                    transcript, client, self._settings.chat_model
                )
        except Exception as exc:
            self.failed.emit(self._entry_id, str(exc))
            return
        if mapping:
            library.refresh(self._entry_id, transcript)
        self.done.emit(self._entry_id, mapping)


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
        self._downloader: _UpdateDownloader | None = None
        self._import_worker: _ImportWorker | None = None
        self._names_worker: _NamesWorker | None = None
        self._imported_ids: list[str] = []
        self._import_errors: list[str] = []
        self._import_cancelled = False
        self._call_mic: Recorder | None = None
        self._call_system: SystemRecorder | None = None
        self._call_started_at = 0.0
        self._call_offset = 0.0
        self._call_files: dict = {}
        self._call_meta: Path | None = None
        self._call_title = ""
        self._call_worker: _CallTranscriber | None = None
        self._call_warned_hotkey = False
        self._pending_calls: list[calls.RecoveredCall] = []

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
        self.tray.import_requested.connect(self.choose_import_files)
        self.tray.audio_refresh_requested.connect(self.refresh_audio_status)
        self.tray.audio_test_requested.connect(self.test_audio_inputs)
        self.tray.call_toggle_requested.connect(self.toggle_call_recording)
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
        self.overlay.library_requested.connect(self.open_recordings)
        self.overlay.stop_recording_requested.connect(self.stop_call_recording)
        self.overlay.settings_requested.connect(self.open_settings)
        self.overlay.moved.connect(self._on_overlay_moved)

        self.pipeline.recording_changed.connect(self._on_recording_changed)
        self.pipeline.status_changed.connect(self._on_status_changed)
        self.pipeline.notice.connect(self._on_notice)
        self.pipeline.finalized.connect(self._on_finalized)
        self.pipeline.transcript_ready.connect(self._on_transcript)
        self.pipeline.retranscribed.connect(self._on_retranscribed)

        self._hotkey_timer = QTimer(self)
        self._hotkey_timer.setInterval(15)
        self._hotkey_timer.timeout.connect(self._drain_hotkey)

        self._level_timer = QTimer(self)
        self._level_timer.setInterval(33)
        self._level_timer.timeout.connect(self._tick_level)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(200)
        self._clock_timer.timeout.connect(self._tick_clock)

        # Test permanent des entrees audio : la pastille de l'icone et le menu
        # disent a tout moment ce qui est capturable (cout quasi nul, aucun
        # flux ouvert).
        self._audio_timer = QTimer(self)
        self._audio_timer.setInterval(5000)
        self._audio_timer.timeout.connect(self.refresh_audio_status)

        # Chrono et niveaux de l'enregistrement d'appel.
        self._call_timer = QTimer(self)
        self._call_timer.setInterval(200)
        self._call_timer.timeout.connect(self._tick_call)

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
        # Micro « chaud » : on ouvre le peripherique des maintenant pour que le
        # premier appui sur le raccourci demarre instantanement.
        self.pipeline.recorder.warm()
        # Purge des vieux enregistrements, en tache de fond pour ne pas retarder
        # le demarrage.
        QTimer.singleShot(4000, self._prune_recordings)
        # Appels interrompus (plantage, coupure) : finalises puis transcrits.
        QTimer.singleShot(6000, self._recover_calls)
        self.tray.show()
        self.tray.set_status("Prêt")
        # Les entrees audio (micro, son du systeme) sont detectees apres le
        # demarrage : la pastille de l'icone renseigne tout de suite.
        QTimer.singleShot(1500, self.refresh_audio_status)
        # Puis testees en continu, toutes les 5 s.
        self._audio_timer.start()
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
                if self.call_recording:
                    if not self._call_warned_hotkey:
                        self._call_warned_hotkey = True
                        self._on_notice(
                            "info",
                            "Enregistrement d'appel en cours : arrête-le avec ■ "
                            "(pilule) ou le menu de l'icône.",
                        )
                    continue
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
        window.dashboard_requested.connect(self.open_stats)
        window.import_requested.connect(self._on_settings_import_requested)
        window.import_cancel_requested.connect(self.cancel_import)
        window.check_requested.connect(
            lambda url: self.check_updates(url, manual=True)
        )
        window.update_requested.connect(self.start_update)
        if self._update_info is not None:
            window.show_update_available(self._update_info)

        def _on_finished(result: int) -> None:
            self._settings_window = None
            if result == QDialog.Accepted:
                self._commit_settings(window.values())
            if state["recordings"]:
                state["recordings"] = False
                self.open_recordings()

        window.finished.connect(_on_finished)
        self._settings_window = window
        # Un import tourne peut-etre deja : l'onglet « Importer » reprend
        # l'etat en cours (les fichiers deja ranges s'affichent).
        if self._import_worker is not None and self._import_worker.isRunning():
            window.set_import_running(True, "Import en cours…")
            for entry_id in self._imported_ids:
                window.on_import_done(entry_id)
        window.show()
        window.raise_()
        window.activateWindow()

    def check_updates(self, url: str | None = None, manual: bool = False) -> None:
        """Interroge le manifeste de version (silencieux en cas d'echec)."""
        manifest = (url or self.settings.update_manifest_url or "").strip()
        window = self._settings_window
        if not manifest:
            if manual and window is not None:
                window.show_check_error("aucune adresse de mise à jour configurée")
            return
        if manual and window is not None:
            window.show_checking()
        self._update_checker = _UpdateChecker(manifest, __version__, self)
        self._update_checker.checked.connect(self._on_update_checked)
        self._update_checker.start()

    def _on_update_checked(self, info: updates.UpdateInfo | None, reason: str) -> None:
        window = self._settings_window
        if info is None:
            log.info("Mise à jour : %s", reason)
            self._update_info = None
            self.tray.set_update_available("")
            if window is not None:
                if reason.startswith("à jour"):
                    window.show_up_to_date()
                else:
                    window.show_check_error(reason)
            return
        self._update_info = info
        log.info("Mise à jour disponible : %s", info.version)
        self.tray.set_update_available(info.version)
        if window is not None:
            window.show_update_available(info)
        notes = (info.notes or "").strip()
        self.tray.showMessage(
            f"Vox {info.version} est disponible",
            (notes + "\n" if notes else "")
            + "Ouvre Réglages → Mises à jour pour la mettre à jour en un clic.",
            QSystemTrayIcon.Information,
            12000,
        )

    # ------------------------------------------------------------------
    # Mise a jour en un clic
    # ------------------------------------------------------------------
    def start_update(self) -> None:
        """Telecharge puis installe la mise a jour en un seul geste."""
        info = self._update_info
        if info is None or not info.url:
            # Rien de connu pour l'instant : on verifie, l'utilisateur recliquera.
            self.check_updates(manual=True)
            return

        destination = updates.cached_file(info.url, info.version)
        if destination.exists() and destination.stat().st_size > 0:
            log.info("Mise a jour deja telechargee : %s", destination)
            self._install_update(destination)
            return

        updates.cleanup()
        log.info("Telechargement de la mise a jour %s -> %s", info.url, destination)
        if self._settings_window is not None:
            self._settings_window.show_progress(0, 0)
        self._downloader = _UpdateDownloader(info.url, destination, self)
        self._downloader.progress.connect(self._on_update_progress)
        self._downloader.done.connect(self._on_update_downloaded)
        self._downloader.failed.connect(self._on_update_failed)
        self._downloader.start()

    def _on_update_progress(self, received: int, total: int) -> None:
        if self._settings_window is not None:
            self._settings_window.show_progress(received, total)

    def _on_update_failed(self, message: str) -> None:
        log.warning("Telechargement de la mise a jour impossible : %s", message)
        if self._settings_window is not None:
            self._settings_window.show_download_error(message)
        self.tray.showMessage(
            "Vox : échec du téléchargement",
            f"{message}\nRéessaie depuis Réglages → Mises à jour.",
            QSystemTrayIcon.Warning,
            12000,
        )

    def _on_update_downloaded(self, path) -> None:
        log.info("Mise a jour telechargee : %s", path)
        if self.pipeline.recording or self.pipeline.busy:
            # Ne jamais couper une dictee en cours : on repasse plus tard.
            QTimer.singleShot(4000, lambda: self._on_update_downloaded(path))
            return
        self._install_update(Path(path))

    def _install_update(self, path: Path) -> None:
        """Installe la mise a jour telechargee, puis redemarre Vox."""
        if self._settings_window is not None:
            self._settings_window.show_installing()
        log.info("Installation de la mise a jour : %s", path)
        ok, reason = updates.install(path)
        if ok:
            self.quit()
            return
        log.warning("Installation impossible : %s", reason)
        if self._settings_window is not None:
            self._settings_window.show_download_error(reason)
        self.tray.showMessage(
            "Vox : installation impossible",
            reason,
            QSystemTrayIcon.Warning,
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
        window.show_updates_tab()
        if info is not None:
            window.show_update_available(info)

    def open_stats(self) -> None:
        """Ouvre (ou ramene au premier plan) le tableau de bord."""
        if self._stats_window is None:
            self._stats_window = StatsWindow(self.settings)
            self._stats_window.history_requested.connect(self.open_recordings)
        self._stats_window.settings = self.settings
        self._stats_window.apply_theme(self.settings.theme)
        self._stats_window.refresh()
        self._stats_window.show()
        self._stats_window.raise_()
        self._stats_window.activateWindow()

    def open_recordings(self) -> None:
        """Ouvre (ou ramene au premier plan) la bibliotheque."""
        if self._recordings_window is None:
            window = RecordingsWindow(self.settings)
            window.copy_requested.connect(self.pipeline.copy_text)
            window.insert_requested.connect(self.pipeline.insert_text)
            window.retranscribe_requested.connect(self.pipeline.retranscribe)
            window.delete_requested.connect(self._delete_recording)
            window.import_requested.connect(self.start_import)
            window.import_cancel_requested.connect(self.cancel_import)
            window.import_retranscribe_requested.connect(self.retranscribe_import)
            window.import_delete_requested.connect(self._delete_import)
            window.names_requested.connect(self.suggest_names)
            # Retour vers les reglages depuis la bibliotheque : le dialogue prend
            # la fenetre comme parent, pour s'ouvrir par-dessus.
            window.settings_requested.connect(
                lambda: self.open_settings(parent=self._recordings_window)
            )
            self._recordings_window = window
        self._recordings_window.apply_theme(self.settings.theme)
        self._recordings_window.refresh()
        self._recordings_window.show()
        self._recordings_window.raise_()
        self._recordings_window.activateWindow()

    # ------------------------------------------------------------------
    # Entrées audio (assistant : micro, son du système)
    # ------------------------------------------------------------------
    def refresh_audio_status(self) -> None:
        """Détecte micro et son du système, et met à jour les indicateurs."""
        try:
            statuses = sources.detect(self.settings.input_device)
        except Exception as exc:  # ne doit jamais empêcher l'app de tourner
            log.warning("Détection des entrées audio impossible : %s", exc)
            return
        self.tray.set_audio_status(statuses)

    def test_audio_inputs(self) -> None:
        """Vérifie les entrées et raconte le résultat en une notification."""
        try:
            statuses = sources.detect(self.settings.input_device)
        except Exception as exc:
            self._on_notice("error", f"Détection audio impossible : {exc}")
            return
        self.tray.set_audio_status(statuses)
        self.tray.showMessage(
            "Vox — entrées audio",
            "\n".join(status.summary for status in statuses),
            QSystemTrayIcon.Information,
            8000,
        )

    # ------------------------------------------------------------------
    # Enregistrement d'appel (micro + son du systeme)
    # ------------------------------------------------------------------
    @property
    def call_recording(self) -> bool:
        return bool(self._call_mic is not None and self._call_mic.recording)

    def toggle_call_recording(self) -> None:
        if self.call_recording:
            self.stop_call_recording()
        else:
            self.start_call_recording()

    def start_call_recording(self) -> None:
        """Un clic : micro + son du systeme, deux pistes, transcription a l'arret.

        Les pistes sont ecrites au fil de l'eau dans des `.pcm` bruts, avec une
        fiche de suivi : si Vox s'arrete brutalement (plantage, coupure,
        extinction), le prochain demarrage finalise et transcrit l'appel.
        """
        if self.call_recording:
            return
        if self.pipeline.recording:
            self._on_notice("info", "Termine la dictée en cours avant d'enregistrer un appel.")
            return
        if self._call_worker is not None and self._call_worker.isRunning():
            self._on_notice("info", "Un appel est déjà en cours de transcription.")
            return
        if self._import_worker is not None and self._import_worker.isRunning():
            self._on_notice("info", "Un import est en cours : termine-le d'abord.")
            return

        folder = calls_dir()
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        mic_part = folder / f"{stamp}_micro.pcm"
        system_part = folder / f"{stamp}_systeme.pcm"
        mic_wav = folder / f"{stamp}_micro.wav"
        system_wav = folder / f"{stamp}_systeme.wav"
        meta_path = folder / f"{stamp}.json"

        mic = Recorder(
            samplerate=self.settings.sample_rate,
            device=self.settings.input_device,
            max_seconds=MAX_CALL_SECONDS,
            sink=mic_part,
        )
        # Le micro des dictées garde le périphérique « chaud » : on le libère le
        # temps de l'appel (deux flux simultanés sur le même micro ne passent
        # pas sur tous les pilotes).
        self.pipeline.recorder.close()
        try:
            mic.start()
        except RecorderError as exc:
            mic.close()
            self.pipeline.recorder.warm()
            self._on_notice("error", str(exc))
            return
        started_at = time.monotonic()

        system_info = sources.wasapi_loopback()
        system = SystemRecorder(system_info, sink=system_part) if system_info else None
        offset = 0.0
        if system is not None:
            try:
                system.start()
            except RecorderError:
                system.close()
                system = None
            else:
                offset = max(0.0, time.monotonic() - started_at)
        if system is None:
            self._on_notice(
                "info", "Son du système indisponible : seul le micro est enregistré."
            )

        title = f"Appel — {datetime.now().strftime('%d/%m %H:%M')}"
        self._call_mic = mic
        self._call_system = system
        self._call_started_at = started_at
        self._call_offset = offset
        self._call_files = {
            "micro_part": mic_part,
            "systeme_part": system_part if system is not None else None,
            "micro_wav": mic_wav,
            "systeme_wav": system_wav if system is not None else None,
        }
        self._call_meta = meta_path
        self._call_title = title
        self._call_warned_hotkey = False
        calls.write_sidecar(
            meta_path,
            {
                "stamp": stamp,
                "started": datetime.now().isoformat(timespec="seconds"),
                "titre": title,
                "decalage": round(offset, 3),
                "micro_part": mic_part.name,
                "systeme_part": system_part.name if system is not None else "",
            },
        )

        self.overlay.set_call_recording(True)
        self.overlay.set_state("recording", detail="Démarrage…", title=CALL_TITLE)
        self.overlay.show_pill()
        self.tray.set_call_state(True, 0)
        self.tray.set_status("Enregistrement d'appel…")
        self._call_timer.start()
        sources_label = "micro + son du système" if system is not None else "micro seulement"
        self.tray.showMessage(
            "Vox — enregistrement d'appel",
            f"Enregistrement en cours ({sources_label}), écrit au fil de l'eau. "
            "Arrête avec le bouton ■ de la pilule ou « Arrêter l'enregistrement » "
            "dans le menu de l'icône.",
            QSystemTrayIcon.Information,
            9000,
        )

    def stop_call_recording(self) -> None:
        """Arrête l'enregistrement, finalise les pistes, lance la transcription."""
        mic = self._call_mic
        if mic is None:
            return
        system = self._call_system
        duration = time.monotonic() - self._call_started_at
        self._call_timer.stop()

        mic.stop()
        mic_ok = mic.has_speech
        mic.close()
        system_ok = False
        if system is not None:
            system.stop()
            system_ok = system.has_speech
            system.close()

        self._call_mic = None
        self._call_system = None
        self.overlay.set_call_recording(False)
        self.overlay.set_state("idle", detail=f"{self.hotkey_label} pour dicter")
        self.tray.set_call_state(False)
        self.tray.set_status("Prêt")
        # Le micro redevient « chaud » pour les dictées.
        self.pipeline.recorder.warm()

        files = self._call_files
        mic_wav: Path = files.get("micro_wav") or Path()
        system_wav: Path | None = files.get("systeme_wav")
        mic_final = calls.finalize_part(files.get("micro_part") or "", mic_wav)
        system_final = bool(system_wav) and calls.finalize_part(
            files.get("systeme_part") or "", system_wav
        )
        # Les .pcm ne servent plus : le WAV est la copie durable.
        self._remove_call_parts()

        if (
            (not mic_final and not system_final)
            or duration < MIN_CALL_SECONDS
            or not (mic_ok or system_ok)
        ):
            self._on_notice("info", "Rien d'exploitable dans cet enregistrement.")
            self._discard_call_files()
            return

        mic_input, system_input = _transcription_inputs(
            mic_wav,
            mic_ok and mic_final,
            system_wav,
            system_ok and system_final,
        )
        self._on_notice(
            "info",
            f"Enregistrement terminé ({format_duration(duration)}) : transcription en cours…",
        )
        self.start_call_transcription(
            mic_input, system_input, self._call_offset, self._call_title
        )

    def _remove_call_parts(self) -> None:
        for key in ("micro_part", "systeme_part"):
            part = self._call_files.get(key)
            if part is None:
                continue
            with contextlib.suppress(OSError):
                Path(part).unlink()

    def _discard_call_files(self) -> None:
        """Rien d'exploitable : on efface ce qui vient d'être écrit."""
        self._remove_call_parts()
        for key in ("micro_wav", "systeme_wav"):
            path = self._call_files.get(key)
            if path is None:
                continue
            with contextlib.suppress(OSError):
                Path(path).unlink()
        if self._call_meta is not None:
            calls.delete_sidecar(self._call_meta)
            self._call_meta = None

    def _save_call_before_quit(self) -> None:
        """Fenêtre fermée pendant un appel : on sauvegarde, on reprendra au
        prochain démarrage (les WAV sont finalisés, la fiche de suivi reste)."""
        mic, system = self._call_mic, self._call_system
        self._call_mic = None
        self._call_system = None
        self._call_timer.stop()
        if mic is None and system is None:
            return
        if mic is not None:
            mic.stop()
            mic.close()
        if system is not None:
            system.stop()
            system.close()
        for part_key, wav_key in (
            ("micro_part", "micro_wav"),
            ("systeme_part", "systeme_wav"),
        ):
            part = self._call_files.get(part_key)
            wav = self._call_files.get(wav_key)
            if part and wav:
                calls.finalize_part(part, wav)
        log.info("Enregistrement d'appel sauvegardé à la fermeture : %s", self._call_meta)
        self.pipeline.recorder.warm()

    def start_call_transcription(
        self,
        mic_path: Path,
        system_path: Path | None,
        offset: float,
        title: str,
        at: str = "",
    ) -> None:
        worker = _CallTranscriber(
            mic_path, system_path, offset, title, self.settings, at=at, parent=self
        )
        worker.progress.connect(self._on_call_progress)
        worker.done.connect(self._on_call_done)
        worker.failed.connect(self._on_call_failed)
        worker.finished.connect(self._on_call_finished)
        self._call_worker = worker
        self.overlay.set_state("transcribing", detail="Transcription de l'appel…")
        self.overlay.show_pill()
        self.tray.set_status("Transcription de l'appel…")
        worker.start()

    def _on_call_progress(self, done: int, total: int, message: str) -> None:
        if message:
            self.overlay.set_state("transcribing", detail=message)
        if self._recordings_window is not None:
            self._recordings_window.set_import_progress(done, total, message)
        if self._settings_window is not None:
            self._settings_window.set_import_progress(done, total, message)

    def _on_call_done(self, entry_id: str) -> None:
        entry = library.get(entry_id)
        label = entry.label if entry is not None else "L'appel"
        self._on_notice("info", f"{label} est dans la bibliothèque.")
        if self._recordings_window is not None:
            self._recordings_window.on_imported(entry_id)
        self.tray.showMessage(
            "Vox — appel transcrit",
            "Ouvre la bibliothèque pour relire et nommer les interlocuteurs.",
            QSystemTrayIcon.Information,
            6000,
        )

    def _on_call_failed(self, message: str) -> None:
        self._on_notice(
            "error",
            f"Transcription impossible : {message} — l'audio est dans la "
            "bibliothèque (filtre Appels), avec « Retranscrire » pour réessayer.",
        )

    def _on_call_finished(self) -> None:
        self._call_worker = None
        # La fiche de suivi a rempli son rôle : la transcription est terminée
        # (l'audio et l'entrée de bibliothèque existent maintenant).
        if self._call_meta is not None:
            calls.delete_sidecar(self._call_meta)
            self._call_meta = None
        self.tray.set_status("Prêt")
        if self._pending_calls:
            QTimer.singleShot(800, self._start_next_recovered_call)

    # ------------------------------------------------------------------
    # Reprise des appels interrompus (arret brutal, coupure de courant)
    # ------------------------------------------------------------------
    def _recover_calls(self) -> None:
        """Finalise et transcrit les appels que Vox n'a pas pu terminer."""
        try:
            recovered = calls.recover_calls()
        except Exception as exc:
            log.warning("Reprise des appels impossible : %s", exc)
            return
        if not recovered:
            return
        log.info("Appel(s) récupéré(s) : %d", len(recovered))
        self._pending_calls.extend(recovered)
        self._start_next_recovered_call()

    def _start_next_recovered_call(self) -> None:
        if not self._pending_calls:
            return
        if self._call_worker is not None and self._call_worker.isRunning():
            return
        if self.pipeline.recording or self.call_recording:
            QTimer.singleShot(5000, self._start_next_recovered_call)
            return
        item = self._pending_calls.pop(0)
        self._call_meta = item.meta
        self._on_notice("info", f"{item.title} : transcription de l'enregistrement repris…")
        self.start_call_transcription(
            item.mic, item.system, item.offset, item.title, at=item.started
        )

    def _tick_call(self) -> None:
        mic = self._call_mic
        if mic is None:
            return
        # Disque plein ou fichier parti : on arrête proprement pour ne rien perdre.
        if mic.sink_error or (
            self._call_system is not None and self._call_system.sink_error
        ):
            self._on_notice(
                "error",
                "Écriture de l'enregistrement interrompue : arrêt pour tout garder.",
            )
            self.stop_call_recording()
            return
        # Les pistes sont écrites au fil de l'eau : on force le disque à jour
        # pour ne jamais perdre plus de quelques dixièmes de seconde.
        mic.flush()
        if self._call_system is not None:
            self._call_system.flush()
        elapsed = int(time.monotonic() - self._call_started_at)
        level = mic.level
        if self._call_system is not None:
            level = max(level, self._call_system.level)
        self.overlay.push_level(level)
        pistes = "micro + système" if self._call_system is not None else "micro"
        self.overlay.set_state(
            "recording",
            detail=f"{format_elapsed(elapsed)}  ·  {pistes}  ·  ■ pour arrêter",
            title=CALL_TITLE,
        )
        self.tray.set_call_state(True, elapsed)
        if mic.hit_max or (self._call_system is not None and self._call_system.hit_max):
            self.stop_call_recording()

    def choose_import_files(self) -> None:
        """Choisit des fichiers audio à importer, puis ouvre la bibliothèque."""
        paths, _ = QFileDialog.getOpenFileNames(
            None,
            "Importer des fichiers audio",
            str(Path.home()),
            "Audio (*.mp3 *.m4a *.wav *.flac *.ogg *.opus *.wma *.aac *.mp4 *.mkv *.webm)"
            ";;Tous les fichiers (*)",
        )
        if not paths:
            return
        # La bibliothèque s'ouvre avant l'import : on voit où le transcript
        # arrive, et la progression y est affichée.
        self.open_recordings()
        self.start_import(paths)

    # ------------------------------------------------------------------
    # Import de fichiers audio
    # ------------------------------------------------------------------
    def start_import(
        self,
        paths: list[str],
        replace_id: str = "",
        settings_override: Settings | None = None,
    ) -> None:
        """Lance l'import de fichiers audio (ou la retranscription d'une entree).

        `settings_override` laisse l'onglet « Importer » des reglages appliquer
        ses options (modele, diarisation, personnes, nettoyage) au lot, sans
        toucher aux reglages enregistres.
        """
        paths = [str(path) for path in (paths or []) if path]
        if not paths and not replace_id:
            return
        if not self.settings.effective_key:
            self._on_notice("error", "Clé OpenRouter manquante : ouvre les réglages.")
            self.open_settings()
            return
        if self._import_worker is not None and self._import_worker.isRunning():
            self._on_notice("info", "Un import est déjà en cours.")
            return
        if self.pipeline.recording or self.pipeline.busy:
            self._on_notice("info", "Termine la dictée en cours avant d'importer.")
            return

        self._imported_ids = []
        self._import_errors = []
        self._import_cancelled = False
        worker = _ImportWorker(
            paths,
            settings_override or self.settings,
            replace_id=replace_id,
            parent=self,
        )
        worker.progress.connect(self._on_import_progress)
        worker.imported.connect(self._on_imported)
        worker.replaced.connect(self._on_import_replaced)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(self._on_import_finished)
        self._import_worker = worker

        label = "Retranscription…" if replace_id else f"Import : {Path(paths[0]).name}"
        if self._recordings_window is not None:
            self._recordings_window.set_import_running(True, label)
        if self._settings_window is not None:
            self._settings_window.set_import_running(True, label)
        self.overlay.show_pill()
        self.overlay.set_state("transcribing", detail=label)
        self.tray.set_status("Import…")
        worker.start()

    def _on_settings_import_requested(self, paths: list, options: dict) -> None:
        """Import lance depuis les reglages : options appliquees a ce lot."""
        settings = dataclasses.replace(
            self.settings,
            diarization_model=str(options.get("model") or ""),
            import_diarize=bool(options.get("diarize", True)),
            import_speakers=int(options.get("speakers") or 0),
            clean_imports=bool(options.get("clean", True)),
        )
        self.start_import(list(paths), settings_override=settings)

    def cancel_import(self) -> None:
        if self._import_worker is not None and self._import_worker.isRunning():
            self._import_worker.stop()
            if self._recordings_window is not None:
                self._recordings_window.progress_label.setText("Annulation…")

    def retranscribe_import(self, entry_id: str) -> None:
        entry = library.get(entry_id)
        if entry is None:
            return
        if not entry.exists:
            self._on_notice("error", "Fichier d'origine introuvable : impossible de retranscrire.")
            if self._recordings_window is not None:
                self._recordings_window.on_import_updated(entry_id)
            return
        self.start_import([entry.source], replace_id=entry_id)

    def _on_import_progress(self, done: int, total: int, message: str) -> None:
        if self._recordings_window is not None:
            self._recordings_window.set_import_progress(done, total, message)
        if self._settings_window is not None:
            self._settings_window.set_import_progress(done, total, message)
        if message:
            self.overlay.set_state("transcribing", detail=message)

    def _on_imported(self, entry_id: str) -> None:
        self._imported_ids.append(entry_id)
        if self._recordings_window is not None:
            self._recordings_window.on_imported(entry_id)
        if self._settings_window is not None:
            self._settings_window.on_import_done(entry_id)

    def _on_import_replaced(self, entry_id: str) -> None:
        if self._recordings_window is not None:
            self._recordings_window.on_import_updated(entry_id)

    def _on_import_failed(self, path: str, message: str) -> None:
        log.warning("Import en echec (%s) : %s", path, message)
        self._import_errors.append(message)
        if self._recordings_window is not None:
            self._recordings_window.on_import_failed(path)
        if self._settings_window is not None:
            self._settings_window.on_import_failed(path, message)
        self.tray.showMessage(
            "Vox — import impossible",
            f"{Path(path).name} : {message}",
            QSystemTrayIcon.Warning,
            8000,
        )

    def _on_import_cancelled(self) -> None:
        self._import_cancelled = True

    def _on_import_finished(self) -> None:
        self._import_worker = None
        if self._recordings_window is not None:
            self._recordings_window.set_import_running(False)
        self.tray.set_status("Prêt")
        summary = ""
        if self._import_cancelled:
            summary = "Import annulé."
            self._on_notice("info", "Import annulé.")
        elif self._import_errors and not self._imported_ids:
            summary = f"Import impossible : {self._import_errors[0]}"
            self._on_notice("error", summary)
        elif self._import_errors:
            summary = (
                f"Import terminé, {len(self._import_errors)} fichier(s) en échec."
            )
            self._on_notice("info", summary)
        elif self._imported_ids:
            count = len(self._imported_ids)
            summary = f"Import terminé : {count} fichier(s) transcrit(s)."
            self._on_notice("info", summary)
            self.tray.showMessage(
                "Vox — import terminé",
                "Ouvre la bibliothèque pour relire et nommer les locuteurs.",
                QSystemTrayIcon.Information,
                6000,
            )
        if self._settings_window is not None:
            self._settings_window.on_import_finished(summary)

    def _delete_import(self, entry_id: str) -> None:
        library.delete(entry_id)
        if self._recordings_window is not None:
            self._recordings_window.on_import_deleted(entry_id)

    def suggest_names(self, entry_id: str) -> None:
        """Demande au modele de conversation de retrouver qui parle."""
        if self._names_worker is not None and self._names_worker.isRunning():
            self._on_notice("info", "Une analyse est déjà en cours.")
            return
        worker = _NamesWorker(entry_id, self.settings, parent=self)
        worker.done.connect(self._on_names_done)
        worker.failed.connect(self._on_names_failed)
        worker.finished.connect(self._clear_names_worker)
        self._names_worker = worker
        self.overlay.show_pill()
        self.overlay.set_state("rewording", detail="Recherche des prénoms…")
        self.tray.set_status("Analyse…")
        worker.start()

    def _clear_names_worker(self) -> None:
        self._names_worker = None
        self.tray.set_status("Prêt")

    def _on_names_done(self, entry_id: str, mapping) -> None:
        mapping = mapping or {}
        if self._recordings_window is not None:
            self._recordings_window.on_names_applied(entry_id, mapping)
        if mapping:
            names = ", ".join(f"{label} → {name}" for label, name in mapping.items())
            self._on_notice("info", f"Prénoms proposés : {names}")
        else:
            self._on_notice("info", "Aucun prénom identifié avec certitude.")

    def _on_names_failed(self, entry_id: str, message: str) -> None:
        log.warning("Analyse des prenoms impossible : %s", message)
        if self._recordings_window is not None:
            self._recordings_window.on_names_failed(entry_id, message)
        self._on_notice("error", f"Analyse impossible : {message}")

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
        if self._stats_window is not None:
            # Le tableau de bord garde une reference aux reglages : on la met a
            # jour pour qu'un simple « Actualiser » prenne la nouvelle vitesse.
            self._stats_window.settings = settings
            self._stats_window.refresh()
        self.overlay.set_state("idle", detail=f"{self.hotkey_label} pour dicter")
        self._load_catalogue(force=True)
        self.refresh_audio_status()
        self._on_notice("info", "Réglages enregistrés.")

    def _apply_theme(self) -> None:
        # Style Fusion : rendu identique sur toutes les machines, et qui
        # respecte la palette explicite (sinon, sur un Windows en mode sombre,
        # les widgets non couverts par la feuille de style s'affichaient en
        # texte blanc sur nos fonds clairs).
        self.qapp.setStyle("Fusion")
        self.qapp.setPalette(build_palette(self.settings.theme))
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
        self._audio_timer.stop()
        self._call_timer.stop()
        self.hotkey.stop()
        self._save_call_before_quit()
        for worker in (self._import_worker, self._names_worker, self._call_worker):
            if worker is not None and worker.isRunning():
                if isinstance(worker, (_ImportWorker, _CallTranscriber)):
                    worker.stop()
                worker.wait(3000)
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
