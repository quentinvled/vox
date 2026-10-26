"""Actualisation des modeles dans des reglages deja ouverts, hors ecran."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from vox import app as app_module
from vox import config, models
from vox.ui import settings_window

TURBO = "openai/whisper-large-v3-turbo"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(models, "load", lambda provider, *args, **kwargs: models.fallback(provider))

    def key_for(provider, settings):
        return getattr(settings, config.KEY_FIELDS[provider])

    monkeypatch.setattr(config, "key_for", key_for)
    monkeypatch.setattr(settings_window, "key_for", key_for)


def fresh(provider="openrouter"):
    return models.Catalogue(
        provider=provider,
        stt=[{"id": TURBO, "name": "OpenAI: Whisper Large V3 Turbo"}, {"id": "nouveau/asr"}],
        chat=[{"id": "nouveau/chat"}],
        fetched_at=1234,
    )


def test_open_settings_requests_live_models_and_preserves_choices(qapp) -> None:
    old = models.Catalogue(stt=[{"id": "ancien/asr"}], chat=[{"id": "ancien/chat"}], from_cache=True)
    window = settings_window.SettingsWindow(
        config.Settings(api_key="cle", stt_model="ancien/asr", chat_model="ancien/chat"), old
    )
    requests = []
    window.models_refresh_requested.connect(lambda provider, key: requests.append((provider, key)))
    try:
        window.show()
        qapp.processEvents()
        assert requests == [("openrouter", "cle")]
        assert not window.refresh_models_button.isEnabled()
        window.set_catalogue(fresh(), "cle")
        assert window.refresh_models_button.isEnabled()
        assert window.stt_combo.findData(TURBO) >= 0
        assert window.import_model_combo.findData(TURBO) >= 0
        assert window.stt_combo.currentData() == "ancien/asr"
        assert window.chat_combo.currentData() == "ancien/chat"
        assert "Liste à jour" in window.models_status.text()

        window.stt_combo.setCurrentIndex(window.stt_combo.findData(TURBO))
        window.refresh_models_button.click()
        assert requests[-1] == ("openrouter", "cle")
        window.set_catalogue(fresh(), "cle")
        assert window.stt_combo.currentData() == TURBO
    finally:
        window.close()
        window.deleteLater()


def test_switch_provider_reloads_matching_models_without_forgetting_draft(qapp) -> None:
    window = settings_window.SettingsWindow(
        config.Settings(api_key="or", groq_api_key="groq", stt_model=TURBO), models.fallback("openrouter")
    )
    requests = []
    window.models_refresh_requested.connect(lambda provider, key: requests.append((provider, key)))
    try:
        window.provider_combo.setCurrentIndex(window.provider_combo.findData("groq"))
        assert requests[-1] == ("groq", "groq")
        assert window.stt_combo.currentData() == "whisper-large-v3-turbo"
        assert window.stt_combo.findData(TURBO) == -1
        window.stt_combo.setCurrentIndex(window.stt_combo.findData("whisper-large-v3"))

        # Une reponse OpenRouter tardive actualise l'import, pas les choix Groq.
        window.set_catalogue(fresh(), "or")
        assert window.stt_combo.currentData() == "whisper-large-v3"
        assert window.import_model_combo.findData("nouveau/asr") >= 0
        assert not window.refresh_models_button.isEnabled()
        window.set_catalogue(models.fallback("groq"), "groq")
        assert window.refresh_models_button.isEnabled()

        window.provider_combo.setCurrentIndex(window.provider_combo.findData("openrouter"))
        assert window.stt_combo.currentData() == TURBO
        window.provider_combo.setCurrentIndex(window.provider_combo.findData("groq"))
        assert window.stt_combo.currentData() == "whisper-large-v3"
        assert window.values().stt_model == "whisper-large-v3"
    finally:
        window.deleteLater()


def test_response_for_old_key_cannot_overwrite_current_request(qapp) -> None:
    window = settings_window.SettingsWindow(
        config.Settings(api_key="ancienne"), models.fallback("openrouter")
    )
    try:
        window.key_edit.setText("nouvelle")
        window.request_models()
        window.set_catalogue(fresh(), "ancienne")
        assert window.stt_combo.findData("nouveau/asr") == -1
        assert not window.refresh_models_button.isEnabled()
        window.set_catalogue(fresh(), "nouvelle")
        assert window.stt_combo.findData("nouveau/asr") >= 0
        assert window.refresh_models_button.isEnabled()
    finally:
        window.deleteLater()


def test_failed_refresh_keeps_models_and_allows_retry(qapp) -> None:
    catalogue = models.fallback("openrouter")
    catalogue.error = "connexion indisponible"
    window = settings_window.SettingsWindow(config.Settings(stt_model=TURBO), catalogue)
    try:
        window.request_models()
        window.set_catalogue(catalogue, "")
        assert window.stt_combo.currentData() == TURBO
        assert "Dernière liste conservée" in window.models_status.text()
        assert "connexion indisponible" in window.models_status.text()
        assert window.refresh_models_button.isEnabled()
    finally:
        window.deleteLater()


def test_catalogue_updates_saved_provider_and_open_settings_only_when_matching() -> None:
    original = models.fallback("openrouter")
    controller = SimpleNamespace(
        settings=config.Settings(api_key="or", groq_api_key="groq"),
        catalogue=original,
        _settings_window=Mock(),
        _refresh_model_views=Mock(),
    )
    wrong_provider = fresh("groq")
    app_module.VoxApp._on_catalogue(controller, wrong_provider, "groq")
    assert controller.catalogue is original
    controller._refresh_model_views.assert_not_called()
    controller._settings_window.set_catalogue.assert_called_once_with(wrong_provider, "groq")

    live = fresh()
    app_module.VoxApp._on_catalogue(controller, live, "ancienne-cle")
    assert controller.catalogue is original
    app_module.VoxApp._on_catalogue(controller, live, "or")
    assert controller.catalogue is live
    controller._refresh_model_views.assert_called_once()


def test_duplicate_refreshes_share_one_worker(monkeypatch) -> None:
    constructor = Mock(side_effect=lambda *args: Mock())
    monkeypatch.setattr(app_module, "_CatalogueLoader", constructor)
    controller = SimpleNamespace(
        settings=config.Settings(), _catalogue_loaders={}, _on_catalogue=Mock(), _on_catalogue_finished=Mock()
    )
    app_module.VoxApp._load_catalogue(controller, provider="openrouter", api_key="cle")
    app_module.VoxApp._load_catalogue(controller, provider="openrouter", api_key="cle")
    assert constructor.call_count == 1
    app_module.VoxApp._load_catalogue(controller, provider="groq", api_key="autre-cle")
    assert constructor.call_count == 2
    assert len(controller._catalogue_loaders) == 2


def test_loader_failure_always_notifies_settings(qapp, monkeypatch) -> None:
    monkeypatch.setattr(models, "load", Mock(side_effect=RuntimeError("cache illisible")))
    loader = app_module._CatalogueLoader("openrouter", "cle", True)
    received = []
    loader.loaded.connect(lambda catalogue, key: received.append((catalogue, key)))
    loader.run()
    assert received[0][0].stt
    assert received[0][0].error == "cache illisible"
    assert received[0][1] == "cle"


def test_saving_provider_keeps_explicit_model_selection(qapp) -> None:
    controller = app_module.VoxApp(qapp)
    try:
        controller._commit_settings(config.Settings(
            provider="groq", stt_model="whisper-large-v3", chat_model="llama-3.1-8b-instant"
        ))
        assert controller.settings.stt_model == "whisper-large-v3"
        assert controller.settings.chat_model == "llama-3.1-8b-instant"
        assert config.load().stt_model == "whisper-large-v3"
    finally:
        controller.quit()


def test_background_catalogue_reaches_already_open_settings(qapp, monkeypatch) -> None:
    monkeypatch.setattr(models, "load", lambda *args, **kwargs: fresh())
    controller = app_module.VoxApp(qapp)
    try:
        controller.open_settings()
        loop = QEventLoop()
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(2000)
        for loader in controller._catalogue_loaders.values():
            loader.loaded.connect(loop.quit)
        loop.exec()
        assert controller._settings_window.stt_combo.findData("nouveau/asr") >= 0
        assert controller._settings_window.refresh_models_button.isEnabled()
        assert controller.catalogue.stt == fresh().stt
    finally:
        if controller._settings_window is not None:
            controller._settings_window.reject()
        controller.quit()
