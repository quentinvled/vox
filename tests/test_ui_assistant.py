"""Tests hors ecran des acces « assistant » : bibliotheque, import, entrees audio."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication

from vox.sources import KIND_MIC, KIND_SYSTEM, SourceStatus
from vox.ui.overlay import Overlay
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
