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
