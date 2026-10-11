"""Tests du pipeline : la reformulation « Nettoyer » est active par defaut."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication

from vox import api
from vox.config import Settings
from vox.pipeline import Pipeline


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeChat:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def chat(self, messages, model):
        self.messages = messages
        return api.Completion(
            text="Bonjour, je voulais te dire que le devis est prêt.",
            model=model,
            cost=0.0002,
            latency_ms=320,
        )


def test_pipeline_reword_cleans_by_default(qapp) -> None:
    settings = Settings()
    assert settings.reword_enabled is True  # le defaut demande par Quentin
    pipeline = Pipeline(settings)
    try:
        client = _FakeChat()
        cleaned, meta = pipeline._reword(client, "euh bonjour euh je voulais euh te dire")
        assert cleaned == "Bonjour, je voulais te dire que le devis est prêt."
        assert meta["tone"] == "clean"
        assert meta["reword_cost"] == pytest.approx(0.0002)
        assert "hésitations" in client.messages[0]["content"]
        assert client.messages[1]["content"].startswith("euh bonjour")
    finally:
        pipeline.shutdown()


def test_pipeline_toggle_mode(qapp) -> None:
    pipeline = Pipeline(Settings())
    seen: list[str] = []
    pipeline.mode_changed.connect(seen.append)
    try:
        assert pipeline.mode == "dictation"
        assert pipeline.toggle_mode() == "instruction"
        assert pipeline.toggle_mode() == "dictation"
    finally:
        pipeline.shutdown()
    assert seen == ["instruction", "dictation"]


def test_instruction_messages_include_clipboard_context() -> None:
    from vox import reword

    with_context = reword.instruction_messages("Rends ce texte poli", "salut ça va")
    assert "CONSIGNE" in with_context[0]["content"]
    assert "Rends ce texte poli" in with_context[1]["content"]
    assert "salut ça va" in with_context[1]["content"]

    without = reword.instruction_messages("Écris un message d'excuse")
    assert "contexte" not in without[1]["content"].lower()


def test_instruct_sends_clipboard_as_context(qapp, monkeypatch) -> None:
    pipeline = Pipeline(Settings())
    captured: dict = {}

    class _Client:
        def chat(self, messages, model):
            captured["messages"] = messages
            captured["model"] = model
            return api.Completion(
                text="Bonjour, veuillez m'excuser pour ce retard.",
                model=model,
                cost=0.0003,
                latency_ms=410,
            )

    monkeypatch.setattr(
        "vox.pipeline.injector.get_clipboard_text", lambda: "texte collé"
    )
    try:
        final, meta = pipeline._instruct(_Client(), "réponds poliment")
    finally:
        pipeline.shutdown()

    assert final == "Bonjour, veuillez m'excuser pour ce retard."
    assert "texte collé" in captured["messages"][1]["content"]
    assert "réponds poliment" in captured["messages"][1]["content"]
    assert meta["mode"] == "instruction"
    assert meta["context_chars"] == len("texte collé")
    assert meta["instruct_cost"] == pytest.approx(0.0003)


def test_instruct_without_context(qapp, monkeypatch) -> None:
    pipeline = Pipeline(Settings())
    monkeypatch.setattr("vox.pipeline.injector.get_clipboard_text", lambda: "   ")

    class _Client:
        def chat(self, messages, _model):
            assert "contexte" not in messages[1]["content"].lower()
            return api.Completion(text="Message rédigé.", model="m", cost=0.0, latency_ms=1)

    try:
        final, meta = pipeline._instruct(_Client(), "écris un message")
    finally:
        pipeline.shutdown()

    assert final == "Message rédigé."
    assert meta["context_chars"] == 0


def test_instruct_returns_empty_on_failure(qapp, monkeypatch) -> None:
    pipeline = Pipeline(Settings())
    monkeypatch.setattr("vox.pipeline.injector.get_clipboard_text", lambda: "")

    class _Broken:
        def chat(self, *_args, **_kwargs):
            raise RuntimeError("réseau indisponible")

    notices: list[tuple[str, str]] = []
    pipeline.notice.connect(lambda level, message: notices.append((level, message)))
    try:
        final, meta = pipeline._instruct(_Broken(), "fais un truc")
    finally:
        pipeline.shutdown()

    assert final == ""
    assert meta == {"mode": "instruction"}
    assert notices and notices[0][0] == "error"


def test_pipeline_reword_falls_back_to_raw_text(qapp) -> None:
    class _Broken:
        def chat(self, *_args, **_kwargs):
            raise RuntimeError("réseau indisponible")

    pipeline = Pipeline(Settings())
    notices: list[tuple[str, str]] = []
    pipeline.notice.connect(lambda level, message: notices.append((level, message)))
    try:
        raw = "euh bonjour euh"
        cleaned, meta = pipeline._reword(_Broken(), raw)
        assert cleaned == raw
        assert meta == {"tone": None}
        assert notices and notices[0][0] == "info"
        assert "texte brut conservé" in notices[0][1]
    finally:
        pipeline.shutdown()
