"""Catalogues en direct, cache de secours et separation transcription/chat."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import httpx
import pytest

from vox import api, models

TURBO = {"id": "openai/whisper-large-v3-turbo", "name": "OpenAI: Whisper Large V3 Turbo"}
CHAT = {"id": "openai/gpt-4o-mini", "name": "GPT-4o mini"}
OLD = {"id": "ancien/modele", "name": "Ancien modèle"}


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOX_DATA_DIR", str(tmp_path))


@pytest.fixture
def client(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    client.list_stt_models.return_value = [dict(TURBO)]
    client.list_chat_models.return_value = [dict(CHAT)]
    constructor = Mock(return_value=client)
    monkeypatch.setattr(models, "Client", constructor)
    return client, constructor


def test_openrouter_catalogue_is_public_without_a_key(client) -> None:
    instance, constructor = client
    catalogue = models.load("openrouter", "", force_refresh=True)
    assert catalogue.stt == [TURBO]
    assert catalogue.chat == [CHAT]
    assert catalogue.error is None
    assert not catalogue.from_cache
    constructor.assert_called_once_with("openrouter", "", timeout=10.0)
    instance.__exit__.assert_called_once()
    assert models._read_cache()["openrouter"]["stt"] == [TURBO]


def test_force_refresh_replaces_a_recent_incomplete_list(client) -> None:
    _, constructor = client
    models._write_cache("openrouter", [OLD], [CHAT])
    assert models.load("openrouter").stt == [OLD]
    constructor.assert_not_called()
    assert models.load("openrouter", force_refresh=True).stt == [TURBO]
    assert models.load("openrouter").stt == [TURBO]


def test_failed_refresh_keeps_previous_catalogue_and_date(client) -> None:
    instance, _ = client
    models._write_cache("openrouter", [OLD], [CHAT])
    before = models._read_cache()["openrouter"]
    instance.list_stt_models.side_effect = OSError("hors ligne")
    catalogue = models.load("openrouter", force_refresh=True)
    assert catalogue.stt == [OLD]
    assert catalogue.chat == [CHAT]
    assert catalogue.from_cache
    assert catalogue.fetched_at == before["fetched_at"]
    assert "hors ligne" in catalogue.error
    assert models._read_cache()["openrouter"] == before


def test_chat_failure_does_not_hide_new_transcription_models(client) -> None:
    instance, _ = client
    models._write_cache("openrouter", [OLD], [CHAT])
    instance.list_chat_models.side_effect = OSError("liste chat indisponible")
    catalogue = models.load("openrouter", force_refresh=True)
    assert catalogue.stt == [TURBO]
    assert catalogue.chat == [CHAT]
    assert not catalogue.from_cache
    assert "reformulation" in catalogue.error
    assert models._read_cache()["openrouter"]["stt"] == [TURBO]


def test_groq_without_key_uses_cache_without_network(client) -> None:
    _, constructor = client
    models._write_cache("groq", [{"id": "whisper-large-v3-turbo"}], [])
    catalogue = models.load("groq", "", force_refresh=True)
    constructor.assert_not_called()
    assert catalogue.provider == "groq"
    assert catalogue.stt[0]["id"] == "whisper-large-v3-turbo"
    assert catalogue.from_cache
    assert "absente" in catalogue.error


@pytest.mark.parametrize("content", ["[]", "null", "incomplet", '{"openrouter": 42}'])
def test_corrupt_cache_does_not_block_refresh(client, content) -> None:
    models.models_cache_file().write_text(content, encoding="utf-8")
    assert models.load("openrouter", force_refresh=True).stt == [TURBO]


def test_provider_caches_do_not_overwrite_each_other(client) -> None:
    models.load("openrouter", force_refresh=True)
    models.load("groq", "cle-de-test", force_refresh=True)
    assert set(models._read_cache()) == {"openrouter", "groq"}


def test_fallback_catalogues_do_not_share_mutable_lists() -> None:
    first = models.fallback("openrouter")
    first.stt[0]["name"] = "autre nom"
    first.stt.clear()
    second = models.fallback("openrouter")
    assert second.stt
    assert second.stt[0]["name"] != "autre nom"


def test_openrouter_filters_out_non_chat_models() -> None:
    payload = {
        "data": [
            {"id": "openai/whisper-large-v3-turbo"},
            {"id": "openai/gpt-4o-transcribe"},
            {"id": "speech/asr", "architecture": {"output_modalities": ["transcription"]}},
            {"id": "speech/tts", "architecture": {"output_modalities": ["audio"]}},
            {"id": "text/model", "architecture": {
                "input_modalities": ["text", "image"], "output_modalities": ["text"],
            }},
        ]
    }
    with api.Client("openrouter", "") as client:
        client._http.close()
        client._http = httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        )
        assert client.list_chat_models() == [{"id": "text/model", "name": "text/model"}]


def test_transcription_request_retrieves_turbo_with_its_official_name() -> None:
    def respond(request):
        assert request.url.params["output_modalities"] == "transcription"
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"data": [{**TURBO, "pricing": {"prompt": "0.00000333"}}]})

    with api.Client("openrouter", "") as client:
        client._http.close()
        client._http = httpx.Client(transport=httpx.MockTransport(respond))
        assert client.list_stt_models()[0]["name"] == "OpenAI: Whisper Large V3 Turbo"
