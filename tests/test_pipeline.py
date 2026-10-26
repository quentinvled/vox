"""Tests du pipeline : la reformulation « Nettoyer » est active par defaut."""

from __future__ import annotations

import json
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
        assert json.loads(client.messages[1]["content"])["transcription"].startswith("euh bonjour")
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


def test_insertion_confirmed_compares_the_target_window(qapp, monkeypatch) -> None:
    pipeline = Pipeline(Settings())
    try:
        pipeline._target_window = 111
        monkeypatch.setattr("vox.pipeline.injector.get_foreground_window_id", lambda: 111)
        assert pipeline._insertion_confirmed() is True

        # Focus perdu pendant le traitement : le texte n'est pas alle au bon endroit.
        monkeypatch.setattr("vox.pipeline.injector.get_foreground_window_id", lambda: 222)
        assert pipeline._insertion_confirmed() is False

        # Fenetre cible inconnue : on ne bloque pas (X11 sans Xlib, Wayland...).
        pipeline._target_window = 0
        assert pipeline._insertion_confirmed() is True

        # Lecture impossible au moment de la verifier : on suppose bon.
        pipeline._target_window = 111
        monkeypatch.setattr("vox.pipeline.injector.get_foreground_window_id", lambda: 0)
        assert pipeline._insertion_confirmed() is True
    finally:
        pipeline.shutdown()


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


@pytest.mark.parametrize("answer", ["Non.", "Non, je n'ai pas accès à ton Drive."])
def test_reword_keeps_drive_question_instead_of_an_answer(qapp, answer) -> None:
    raw = "Est-ce que oui ou non tu as accès à mon drive ?"

    class _Answering:
        def chat(self, messages, model):
            assert json.loads(messages[1]["content"])["transcription"] == raw
            return api.Completion(text=answer, model=model, cost=0.0002, latency_ms=123)

    pipeline = Pipeline(Settings())
    notices = []
    pipeline.notice.connect(lambda level, message: notices.append((level, message)))
    try:
        final, meta = pipeline._reword(_Answering(), raw)
        assert final == raw
        assert meta["tone"] is None
        assert meta["reword_rejected"] is True
        # L'appel a quand meme ete facture : son cout ne doit pas disparaitre.
        assert meta["reword_cost"] == pytest.approx(0.0002)
        assert notices and "Texte dicté conservé" in notices[0][1]
    finally:
        pipeline.shutdown()


def test_reword_accepts_a_corrected_question(qapp) -> None:
    class _Correcting:
        def chat(self, messages, model):
            return api.Completion(text="As-tu accès à mon Drive, oui ou non ?", model=model)

    pipeline = Pipeline(Settings())
    try:
        final, meta = pipeline._reword(_Correcting(), "est-ce que oui ou non tu as acces à mon drive")
        assert final == "As-tu accès à mon Drive, oui ou non ?"
        assert meta["tone"] == "clean"
        assert "reword_rejected" not in meta
    finally:
        pipeline.shutdown()


def test_dictation_inserts_question_not_model_answer(qapp, monkeypatch) -> None:
    raw = "Est-ce que oui ou non tu as accès à mon drive ?"

    class _Client:
        def transcribe(self, *_args, **_kwargs):
            return api.Transcript(text=raw, model="transcription")

        def chat(self, messages, model):
            return api.Completion(text="Non, je n'ai pas accès à ton Drive.", model=model)

    pipeline = Pipeline(Settings(sounds=False, history_enabled=False))
    inserted = []
    monkeypatch.setattr(pipeline, "_client", lambda: _Client())
    monkeypatch.setattr(pipeline, "_inject", inserted.append)
    try:
        pipeline._transcribe(b"audio", 4.0)
        assert inserted == [raw]
        assert pipeline.last_transcript == raw
        assert pipeline.last_final == raw
        assert pipeline.last_meta["reword_rejected"] is True
    finally:
        pipeline.shutdown()


def test_manual_reword_uses_the_same_question_protection(qapp, monkeypatch) -> None:
    raw = "Est-ce que oui ou non tu as accès à mon drive ?"

    class _Client:
        def chat(self, messages, model):
            return api.Completion(text="Non.", model=model)

    pipeline = Pipeline(Settings())
    inserted = []
    monkeypatch.setattr(pipeline, "_client", lambda: _Client())
    monkeypatch.setattr(pipeline, "_inject", inserted.append)
    monkeypatch.setattr(pipeline.worker, "submit", lambda job: job())
    try:
        pipeline.reword_text(raw, "formal")
        assert inserted == [raw]
        assert pipeline.last_final == raw
        assert not pipeline.busy
    finally:
        pipeline.shutdown()


def test_instruction_mode_can_still_answer_a_question(qapp, monkeypatch) -> None:
    class _Client:
        def chat(self, messages, model):
            assert "Consigne" in messages[1]["content"]
            return api.Completion(text="Non.", model=model)

    pipeline = Pipeline(Settings())
    monkeypatch.setattr("vox.pipeline.injector.get_clipboard_text", lambda: "")
    try:
        final, meta = pipeline._instruct(_Client(), "Réponds non à cette question")
        assert final == "Non."
        assert meta["mode"] == "instruction"
    finally:
        pipeline.shutdown()
