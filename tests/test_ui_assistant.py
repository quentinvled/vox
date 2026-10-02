"""Tests hors ecran des acces « assistant » : bibliotheque, import, entrees audio."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication

from vox import audiofiles, library, models
from vox.sources import KIND_MIC, KIND_SYSTEM, SourceStatus
from vox.ui.overlay import Overlay
from vox.ui.settings_window import SettingsWindow
from vox.ui.tray import Tray
from vox.ui.widgets import make_app_icon, make_dot_icon


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VOX_DATA_DIR", str(tmp_path / "donnees"))


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _mic(available: bool = True, name: str = "Micro USB") -> SourceStatus:
    return SourceStatus(KIND_MIC, "Micro", name=name, available=available, detail="WASAPI")


def _system(available: bool = True, name: str = "Haut-parleurs", active=()) -> SourceStatus:
    return SourceStatus(
        KIND_SYSTEM,
        "Son du système",
        name=name,
        available=available,
        detail="WASAPI loopback",
        active=active,
    )


# ----------------------------------------------------------------------
# Pilule et zone de notification
# ----------------------------------------------------------------------
def test_overlay_opens_library(qapp) -> None:
    overlay = Overlay()
    try:
        seen: list[bool] = []
        overlay.library_requested.connect(lambda: seen.append(True))
        overlay.library_button.click()
        assert seen == [True]
        assert "Bibliothèque" in overlay.library_button.toolTip()
    finally:
        overlay.deleteLater()


def test_tray_import_action(qapp) -> None:
    tray = Tray(make_app_icon(), "Ctrl+Maj")
    try:
        seen: list[bool] = []
        tray.import_requested.connect(lambda: seen.append(True))
        tray.import_action.trigger()
        assert seen == [True]
    finally:
        tray.deleteLater()


def test_tray_audio_status_updates_menu_and_tooltip(qapp) -> None:
    tray = Tray(make_app_icon(), "Ctrl+Maj")
    try:
        tray.set_audio_status([_mic(), _system(False)])
        assert "Micro USB" in tray.mic_action.text()
        assert "indisponible" in tray.system_action.text()
        assert "Micro USB" in tray.toolTip()
        assert not tray.mic_action.icon().isNull()
    finally:
        tray.deleteLater()


def test_tray_tooltip_lists_active_apps(qapp) -> None:
    tray = Tray(make_app_icon(), "Ctrl+Maj")
    try:
        tray.set_audio_status([_mic(), _system(active=("WhatsApp",))])
        assert "WhatsApp" in tray.toolTip()
        assert "WhatsApp" in tray.system_action.text()
    finally:
        tray.deleteLater()


def test_status_icons_are_drawn(qapp) -> None:
    assert not make_app_icon(dot="ok").isNull()
    assert not make_app_icon(dot="warn").isNull()
    assert not make_dot_icon("error").isNull()


def test_tray_status_lines_are_clickable(qapp) -> None:
    """Les lignes d'état doivent rester lisibles (non grisées) et cliquables."""
    tray = Tray(make_app_icon(), "Ctrl+Maj")
    try:
        tests: list[bool] = []
        tray.audio_test_requested.connect(lambda: tests.append(True))
        assert tray.mic_action.isEnabled()
        assert tray.system_action.isEnabled()
        tray.mic_action.trigger()
        tray.system_action.trigger()
        assert tests == [True, True]
    finally:
        tray.deleteLater()


def test_tray_call_action_states(qapp) -> None:
    tray = Tray(make_app_icon(), "Ctrl+Maj")
    try:
        seen: list[bool] = []
        tray.call_toggle_requested.connect(lambda: seen.append(True))
        tray.call_action.trigger()
        assert seen == [True]

        tray.set_call_state(True, 65)
        assert "Arrêter l'enregistrement" in tray.call_action.text()
        assert "01:05" in tray.call_action.text()
        assert not tray.call_action.icon().isNull()

        tray.set_call_state(False)
        assert "Enregistrer un appel" in tray.call_action.text()
    finally:
        tray.deleteLater()


def test_overlay_call_mode_shows_stop(qapp) -> None:
    overlay = Overlay()
    try:
        assert overlay.stop_button.isHidden()
        overlay.set_call_recording(True)
        assert not overlay.stop_button.isHidden()
        assert overlay.model_chip.isHidden()
        assert overlay.reword_chip.isHidden()

        stops: list[bool] = []
        overlay.stop_recording_requested.connect(lambda: stops.append(True))
        overlay.stop_button.click()
        assert stops == [True]

        overlay.set_call_recording(False)
        assert overlay.stop_button.isHidden()
        assert not overlay.model_chip.isHidden()
    finally:
        overlay.deleteLater()


# ----------------------------------------------------------------------
# Application
# ----------------------------------------------------------------------
def test_choose_import_files_opens_library(qapp, monkeypatch, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QFileDialog

    from vox import app as app_module

    source = tmp_path / "vocal.m4a"
    source.write_bytes(b"faux")

    vox = app_module.VoxApp(qapp)
    try:
        opened: list[bool] = []
        started: list[list[str]] = []
        monkeypatch.setattr(vox, "open_recordings", lambda: opened.append(True))
        monkeypatch.setattr(
            vox, "start_import", lambda paths, replace_id="": started.append(list(paths))
        )

        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileNames",
            staticmethod(lambda *_a, **_k: ([str(source)], "")),
        )
        vox.choose_import_files()
        assert opened == [True]
        assert started == [[str(source)]]

        # Annuler ne lance rien de plus.
        monkeypatch.setattr(
            QFileDialog, "getOpenFileNames", staticmethod(lambda *_a, **_k: ([], ""))
        )
        vox.choose_import_files()
        assert len(started) == 1
    finally:
        vox.quit()


def test_refresh_audio_status_fills_the_tray(qapp, monkeypatch) -> None:
    from vox import app as app_module

    vox = app_module.VoxApp(qapp)
    try:
        monkeypatch.setattr(
            "vox.app.sources.detect",
            lambda device=None: [
                SourceStatus(
                    KIND_MIC, "Micro", name="Blue Yeti", available=True, detail="WASAPI"
                ),
                SourceStatus(
                    KIND_SYSTEM,
                    "Son du système",
                    available=False,
                    detail="Windows pour l'instant",
                ),
            ],
        )
        vox.refresh_audio_status()
        assert "Blue Yeti" in vox.tray.mic_action.text()
        assert "Blue Yeti" in vox.tray.toolTip()
    finally:
        vox.quit()


# ----------------------------------------------------------------------
# Onglet « Importer » des réglages
# ----------------------------------------------------------------------
def _probe_120s(path):
    return audiofiles.AudioInfo(duration=120.0, channels=1, samplerate=16000, codec="aac")


def test_settings_import_tab_collects_files_and_options(qapp, monkeypatch, tmp_path) -> None:
    from vox.config import Settings

    source = tmp_path / "appel.m4a"
    source.write_bytes(b"faux audio")
    monkeypatch.setattr("vox.ui.settings_window.audiofiles.probe", _probe_120s)

    window = SettingsWindow(Settings(), models.fallback("openrouter"))
    try:
        window.show()
        qapp.processEvents()
        assert not window.import_start_button.isEnabled()

        window.add_import_files([str(source)])
        assert window.import_start_button.isEnabled()
        assert "2 min 00 s" in window.import_estimate_label.text()
        assert "≈" in window.import_estimate_label.text()

        requests: list[tuple[list, dict]] = []
        window.import_requested.connect(
            lambda paths, options: requests.append((paths, options))
        )
        window.import_start_button.click()
        assert requests == [
            (
                [str(source)],
                {"model": "", "diarize": True, "speakers": 0, "clean": True},
            )
        ]

        # Progression pilotée par l'application.
        window.set_import_running(True, "Import : appel.m4a")
        assert not window.import_start_button.isEnabled()
        window.set_import_progress(3, 10, "Tranche 3/10 — Locuteur 1")
        assert window.import_progress_bar.value() == 3
        assert window.import_progress_bar.maximum() == 10
        assert "fichier 3/10" in window.import_progress_label.text()
        window.set_import_running(False)

        # Retirer et vider.
        window.import_files_list.item(0).setSelected(True)
        window._remove_selected_import_files()
        assert window.import_files_list.count() == 0
        assert not window.import_start_button.isEnabled()
    finally:
        window.deleteLater()


def test_settings_import_tab_diarization_hints(qapp, monkeypatch, tmp_path) -> None:
    from vox.config import Settings

    window = SettingsWindow(Settings(import_speakers=3), models.fallback("openrouter"))
    try:
        assert window.import_diarize_check.isChecked()
        assert window.import_speakers_spin.isEnabled()
        assert window.values().import_speakers == 3

        window.import_diarize_check.setChecked(False)
        assert not window.import_speakers_spin.isEnabled()
        assert "un seul locuteur" in window.import_options_hint.text()
        assert window.values().import_diarize is False

        window.import_diarize_check.setChecked(True)
        index = window.import_model_combo.findData("google/gemini-3.5-transcribe")
        assert index >= 0
        window.import_model_combo.setCurrentIndex(index)
        assert "ne sépare pas les locuteurs" in window.import_options_hint.text()

        # Le modèle choisi est bien celui qui repart dans les réglages.
        assert window.values().diarization_model == "google/gemini-3.5-transcribe"
    finally:
        window.deleteLater()


def _fake_import_result(source: Path):
    from vox import imports, routing
    from vox.transcript import Segment, Speaker, Transcript

    transcript = Transcript(
        segments=[
            Segment(0.0, 2.0, "t0:c0:s0", "Bonjour à tous."),
            Segment(2.5, 4.0, "t0:c0:s1", "Salut, on commence."),
        ],
        speakers=[Speaker(id="t0:c0:s0"), Speaker(id="t0:c0:s1")],
        duration=4.0,
        model="microsoft/mai-transcribe-2",
        cost=0.01,
        source=str(source),
        title="Appel test",
    )
    return imports.ImportResult(
        transcript=transcript,
        info=audiofiles.AudioInfo(duration=4.0, channels=1, samplerate=16000, codec="aac"),
        strategy=routing.Strategy(kind="mono", reason="test", tracks=()),
        model="microsoft/mai-transcribe-2",
        chunks=1,
    )


def test_settings_import_flow_end_to_end(qapp, monkeypatch, tmp_path) -> None:
    """Réglages → onglet Importer → fichier → import → bibliothèque."""
    import time

    from vox import app as app_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "cle-de-test")
    source = tmp_path / "appel.m4a"
    source.write_bytes(b"faux audio")
    result = _fake_import_result(source)
    monkeypatch.setattr("vox.app.process_file", lambda *_a, **_k: result)
    monkeypatch.setattr(
        "vox.app.clean_transcript_with_settings",
        lambda *_a, **_k: {"blocs": 0, "segments_modifies": 0, "cout": 0.0},
    )
    monkeypatch.setattr("vox.ui.settings_window.audiofiles.probe", _probe_120s)

    vox = app_module.VoxApp(qapp)
    try:
        vox.open_settings()
        window = vox._settings_window
        assert window is not None

        window.add_import_files([str(source)])
        window.import_start_button.click()

        deadline = time.time() + 10
        while vox._import_worker is not None and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        qapp.processEvents()
        assert vox._import_worker is None

        entries = library.load()
        assert len(entries) == 1
        assert entries[0].title == "Appel test"
        assert window.import_results_list.count() == 1
        assert "Import terminé" in window.import_status_label.text()
        # Les fichiers traités quittent la liste d'attente.
        assert window.import_files_list.count() == 0
    finally:
        if vox._settings_window is not None:
            vox._settings_window.reject()
        vox.quit()


# ----------------------------------------------------------------------
# Enregistrement d'appel
# ----------------------------------------------------------------------
def test_app_call_recording_flow(qapp, monkeypatch, tmp_path: Path) -> None:
    import time

    from vox import app as app_module

    class _FakeMicro:
        def __init__(self, **_kwargs) -> None:
            self.recording = False
            self.level = 0.4
            self.has_speech = True
            self.hit_max = False

        def start(self) -> None:
            self.recording = True

        def stop(self) -> bytes:
            self.recording = False
            return b"RIFF" + b"\x00" * 64

        def cancel(self) -> None:
            self.recording = False

        def close(self) -> None:
            pass

        def elapsed(self) -> float:
            return 0.0

    monkeypatch.setattr("vox.app.Recorder", lambda **_kwargs: _FakeMicro())
    monkeypatch.setattr("vox.app.SystemRecorder", lambda _info: _FakeMicro())
    monkeypatch.setattr(
        "vox.app.sources.wasapi_loopback",
        lambda: {"index": 1, "name": "Sortie", "rate": 48000, "channels": 2},
    )

    vox = app_module.VoxApp(qapp)
    try:
        started: list[tuple] = []
        monkeypatch.setattr(
            vox,
            "start_call_transcription",
            lambda mic_path, system_path, offset, title: started.append(
                (Path(mic_path), system_path, offset, title)
            ),
        )

        vox.start_call_recording()
        assert vox.call_recording
        assert "Arrêter" in vox.tray.call_action.text()
        assert not vox.overlay.stop_button.isHidden()

        # Simule un appel déjà commencé depuis 30 secondes.
        vox._call_started_at = time.monotonic() - 30
        vox._tick_call()
        assert "00:30" in vox.tray.call_action.text()

        vox.stop_call_recording()
        assert not vox.call_recording
        assert vox.overlay.stop_button.isHidden()
        assert started, "la transcription doit être lancée"
        mic_path, system_path, _offset, title = started[0]
        assert title.startswith("Appel — ")
        assert system_path is not None
        assert mic_path.exists() and system_path.exists()
    finally:
        vox.quit()


def test_call_transcriber_creates_call_entry(monkeypatch, tmp_path: Path) -> None:
    from vox import app as app_module
    from vox.config import Settings
    from vox.transcript import Segment, Speaker, Transcript

    mic = tmp_path / "mic.wav"
    system = tmp_path / "systeme.wav"
    mic.write_bytes(b"x")
    system.write_bytes(b"x")

    def _fake_process(_mic, _system, _settings, **_kwargs):
        return Transcript(
            segments=[
                Segment(0.0, 1.0, "moi", "Salut."),
                Segment(1.5, 2.5, "sys:t0:c0:s0", "Bonjour."),
            ],
            speakers=[
                Speaker(id="moi", name="Moi"),
                Speaker(id="sys:t0:c0:s0", name="Interlocuteur 1"),
            ],
            duration=2.5,
            model="microsoft/mai-transcribe-2",
            cost=0.02,
        )

    monkeypatch.setattr("vox.app.calls.process_call", _fake_process)
    worker = app_module._CallTranscriber(
        mic, system, 0.5, "Appel — test", Settings(clean_imports=False)
    )
    done: list[str] = []
    worker.done.connect(done.append)
    worker.run()

    assert done
    entry = library.get(done[0])
    assert entry is not None
    assert entry.kind == library.KIND_CALL
    assert entry.title == "Appel — test"
    assert entry.cost == pytest.approx(0.02)
    stored = library.load_transcript(entry.id)
    assert stored is not None
    assert stored.speaker_labels() == ["Moi", "Interlocuteur 1"]
