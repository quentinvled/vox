"""Dictee depuis le telephone : identifiants, flux audio, liaison et pipeline."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import secrets
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWebSockets")

from PySide6.QtWidgets import QApplication

from vox import phone as phone_module
from vox.config import Settings
from vox.phone import PhoneLink, fingerprint, pairing_page, room_for_secret
from vox.pipeline import Pipeline
from vox.recorder import StreamRecorder

RELAY_ROOMS = Path(__file__).resolve().parent.parent / "relay" / "vox_relay" / "rooms.py"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _pcm(seconds: float, amplitude: float = 0.3) -> bytes:
    t = np.arange(int(16000 * seconds)) / 16000
    return (np.sin(2 * np.pi * 220 * t) * amplitude * 32767).astype(np.int16).tobytes()


# ----------------------------------------------------------------------
# Identifiants : l'ordinateur et le relais doivent calculer la meme chose
# ----------------------------------------------------------------------
def test_identifiers_match_the_relay() -> None:
    spec = importlib.util.spec_from_file_location("relay_rooms", RELAY_ROOMS)
    rooms = importlib.util.module_from_spec(spec)
    sys.modules["relay_rooms"] = rooms  # requis par les dataclasses du module
    spec.loader.exec_module(rooms)
    for _ in range(5):
        secret, token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        assert room_for_secret(secret) == rooms.room_for_secret(secret)
        assert fingerprint(token) == rooms.fingerprint(token)
        assert rooms.valid_room(room_for_secret(secret))


def test_pairing_page_follows_the_relay_address() -> None:
    assert pairing_page("wss://vox.vled.fr/ws") == "https://vox.vled.fr/"
    assert pairing_page("ws://localhost:8080/ws") == "http://localhost:8080/"


# ----------------------------------------------------------------------
# Flux audio recu du telephone
# ----------------------------------------------------------------------
def test_stream_recorder_builds_a_wav() -> None:
    recorder = StreamRecorder(max_seconds=10)
    recorder.feed(_pcm(0.1))  # hors prise : ignore
    recorder.start()
    data = _pcm(1.0)
    # Paquets de taille impaire : aucun echantillon ne doit etre coupe.
    for start in range(0, len(data), 1001):
        recorder.feed(data[start : start + 1001])
    assert recorder.has_speech
    assert recorder.level > 0
    wav = recorder.stop()
    with wave.open(io.BytesIO(wav)) as handle:
        assert handle.getframerate() == 16000
        assert handle.getnchannels() == 1
        assert handle.getnframes() == 16000


def test_stream_recorder_stops_at_the_limit() -> None:
    recorder = StreamRecorder(max_seconds=1)
    recorder.start()
    recorder.feed(_pcm(0.8))
    assert not recorder.hit_max
    recorder.feed(_pcm(0.8))
    assert recorder.hit_max
    recorder.feed(_pcm(0.8))  # au-dela de la limite : plus rien n'est garde
    assert recorder.seconds == pytest.approx(1.6)


def test_silence_is_not_speech() -> None:
    recorder = StreamRecorder()
    recorder.start()
    recorder.feed(bytes(32000))
    assert not recorder.has_speech


# ----------------------------------------------------------------------
# Liaison : messages du relais (sans reseau)
# ----------------------------------------------------------------------
@pytest.fixture
def link(qapp, monkeypatch):
    monkeypatch.setattr(PhoneLink, "_connect", lambda self: None)
    settings = Settings()
    settings.phones = [{"id": "a" * 32, "name": "iPhone"}]
    link = PhoneLink(settings)
    sent: list[dict] = []
    monkeypatch.setattr(link, "_send", sent.append)
    link.sent = sent
    yield link
    link.close()


def _message(link: PhoneLink, **payload) -> None:
    link._on_text(json.dumps(payload))


def test_capture_flow(link) -> None:
    events: list[str] = []
    link.capture_started.connect(lambda mode: events.append(f"start:{mode}"))
    link.mode_requested.connect(lambda mode: events.append(f"mode:{mode}"))
    link.capture_stopped.connect(lambda: events.append("stop"))
    link.recorder.start()

    _message(link, t="start", mode="instruction", **{"from": "a" * 32})
    link._on_binary(_pcm(0.5))
    _message(link, t="mode", mode="dictation", **{"from": "b" * 32})  # autre telephone : ignore
    _message(link, t="mode", mode="dictation", **{"from": "a" * 32})
    _message(link, t="stop", **{"from": "a" * 32})

    assert events == ["start:instruction", "mode:dictation", "stop"]
    assert link.recorder.seconds == pytest.approx(0.5)


def test_phone_leaving_mid_capture_cancels(link) -> None:
    cancelled: list[bool] = []
    link.capture_cancelled.connect(lambda: cancelled.append(True))
    _message(link, t="phone", id="a" * 32, name="iPhone", online=True)
    assert link.is_online("a" * 32)
    _message(link, t="start", mode="dictation", **{"from": "a" * 32})
    _message(link, t="phone", id="a" * 32, name="iPhone", online=False)
    assert cancelled == [True]
    assert not link.is_online("a" * 32)


def test_pairing_registers_the_new_phone(link, monkeypatch) -> None:
    monkeypatch.setattr(phone_module.secrets, "token_urlsafe", lambda _n: "T" * 43)
    url = link.begin_pairing()
    room, token = url.split("#", 1)[1].split(".")
    assert url.startswith("https://vox.vled.fr/#")
    assert room == room_for_secret(link.settings.phone_secret)
    assert fingerprint(token) in link._allowlist()

    named: list[str] = []
    link.paired.connect(named.append)
    _message(link, t="phone", id=fingerprint(token), name="Pixel 8", online=True)
    assert named == ["Pixel 8"]
    assert [p["name"] for p in link.phones] == ["iPhone", "Pixel 8"]

    link.end_pairing()
    assert link._allowlist() == ["a" * 32, fingerprint(token)]


def test_unpair_and_actions(link) -> None:
    actions: list[str] = []
    link.action_requested.connect(actions.append)
    _message(link, t="action", name="enter", **{"from": "a" * 32})
    _message(link, t="action", name="rm -rf", **{"from": "a" * 32})
    assert actions == ["enter"]

    _message(link, t="unpair", **{"from": "a" * 32})
    assert link.phones == []


# ----------------------------------------------------------------------
# Pipeline : une prise venue du telephone suit le chemin habituel
# ----------------------------------------------------------------------
def test_pipeline_accepts_a_phone_source(qapp, monkeypatch) -> None:
    settings = Settings(sounds=False, save_recordings=False)
    pipeline = Pipeline(settings)
    jobs: list = []
    monkeypatch.setattr(pipeline.worker, "submit", jobs.append)
    seen: list[tuple] = []
    monkeypatch.setattr(
        pipeline, "_transcribe", lambda wav, duration, audio="": seen.append((len(wav), duration))
    )
    try:
        source = StreamRecorder()
        assert pipeline.start_recording(source=source)
        assert pipeline.source is source
        assert not pipeline.start_recording()  # deja en cours
        pipeline.set_mode("instruction")
        source.feed(_pcm(1.0))
        pipeline.stop_recording()
        assert len(jobs) == 1
        jobs[0]()
        assert seen[0][1] == pytest.approx(1.0, abs=0.01)
        assert pipeline.mode == "instruction"
    finally:
        pipeline.shutdown()
