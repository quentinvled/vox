"""Tests de la detection des entrees audio (micro et son du systeme)."""

from __future__ import annotations

import sys
import types

import pytest

from vox import sources


@pytest.fixture(autouse=True)
def _reset_probe():
    """L'instance PyAudio partagee ne doit pas fuiter entre les tests."""
    sources.reset()
    yield
    sources.reset()


# ----------------------------------------------------------------------
# Micro
# ----------------------------------------------------------------------
def test_mic_status_uses_default_device(monkeypatch) -> None:
    monkeypatch.setattr(
        sources.sd,
        "query_devices",
        lambda device=None, kind=None: {
            "name": "Micro (USB)",
            "max_input_channels": 2,
            "hostapi": 0,
            "default_samplerate": 48000,
        },
    )
    monkeypatch.setattr(sources.sd, "query_hostapis", lambda index: {"name": "WASAPI"})

    status = sources.mic_status()
    assert status.available
    assert status.name == "Micro (USB)"
    assert "WASAPI" in status.detail
    assert "2 canaux" in status.detail
    assert status.summary.startswith("Micro : Micro (USB)")


def test_mic_status_falls_back_when_settings_device_is_gone(monkeypatch) -> None:
    def _query(device=None, kind=None):
        if device is None:
            return {"name": "Micro par défaut", "max_input_channels": 1, "hostapi": 0}
        raise ValueError("device not found")

    monkeypatch.setattr(sources.sd, "query_devices", _query)
    monkeypatch.setattr(sources.sd, "query_hostapis", lambda index: {"name": "MME"})

    status = sources.mic_status(device=42)
    assert status.available
    assert status.name == "Micro par défaut"
    assert "périphérique des réglages introuvable" in status.detail


def test_mic_status_unavailable_without_device(monkeypatch) -> None:
    def _query(*_args, **_kwargs):
        raise RuntimeError("pas de micro")

    monkeypatch.setattr(sources.sd, "query_devices", _query)
    status = sources.mic_status()
    assert not status.available
    assert "introuvable" in status.detail


# ----------------------------------------------------------------------
# Son du systeme
# ----------------------------------------------------------------------
def _fake_pyaudiowpatch(name: str = "Haut-parleurs (Realtek)"):
    class _FakeAudio:
        def get_default_wasapi_loopback(self):
            return {"name": name, "defaultSampleRate": 48000, "maxInputChannels": 2}

        def terminate(self) -> None:
            pass

    module = types.ModuleType("pyaudiowpatch")
    module.PyAudio = _FakeAudio
    return module


def test_system_status_is_windows_only(monkeypatch) -> None:
    monkeypatch.setattr(sources.sys, "platform", "linux")
    status = sources.system_status()
    assert not status.available
    assert "Windows" in status.detail


def test_wasapi_loopback_exposes_index_and_is_cached(monkeypatch) -> None:
    created: list[object] = []

    class _FakeAudio:
        def __init__(self):
            created.append(self)

        def get_default_wasapi_loopback(self):
            return {
                "index": 7,
                "name": "Haut-parleurs (Realtek)",
                "defaultSampleRate": 44100,
                "maxInputChannels": 2,
            }

        def terminate(self):
            pass

    module = types.ModuleType("pyaudiowpatch")
    module.PyAudio = _FakeAudio
    monkeypatch.setattr(sources.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", module)

    first = sources.wasapi_loopback()
    second = sources.wasapi_loopback()
    assert first == second
    assert first["index"] == 7
    assert first["rate"] == 44100
    assert first["channels"] == 2
    assert len(created) == 1  # l'instance est reutilisee, pas recreee

    sources.reset()
    third = sources.wasapi_loopback()
    assert third == first
    assert len(created) == 2  # apres reset, une nouvelle instance


def test_system_status_detects_loopback(monkeypatch) -> None:
    monkeypatch.setattr(sources.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", _fake_pyaudiowpatch())
    monkeypatch.setitem(sys.modules, "pycaw", None)  # aucune app active listee

    status = sources.system_status()
    assert status.available
    assert status.name == "Haut-parleurs (Realtek)"
    assert "WASAPI" in status.detail
    assert status.active == ()


def test_system_status_without_loopback_device(monkeypatch) -> None:
    class _Broken:
        def get_default_wasapi_loopback(self):
            raise LookupError("no loopback device")

        def terminate(self) -> None:
            pass

    module = types.ModuleType("pyaudiowpatch")
    module.PyAudio = _Broken
    monkeypatch.setattr(sources.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", module)

    status = sources.system_status()
    assert not status.available
    assert "aucune sortie audio" in status.detail


# ----------------------------------------------------------------------
# Applications qui jouent du son
# ----------------------------------------------------------------------
class _Process:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name


class _Session:
    def __init__(self, state: int, name: str | None) -> None:
        self.State = state
        self.Process = _Process(name) if name else None


def _install_fake_pycaw(monkeypatch, sessions) -> None:
    class _AudioUtilities:
        @staticmethod
        def GetAllSessions():
            return sessions

    utils = types.ModuleType("pycaw.utils")
    utils.AudioUtilities = _AudioUtilities
    package = types.ModuleType("pycaw")
    package.utils = utils
    monkeypatch.setitem(sys.modules, "pycaw", package)
    monkeypatch.setitem(sys.modules, "pycaw.utils", utils)


def test_active_audio_apps_filters_and_dedupes(monkeypatch) -> None:
    monkeypatch.setattr(sources.sys, "platform", "win32")
    _install_fake_pycaw(
        monkeypatch,
        [
            _Session(1, "WhatsApp.exe"),
            _Session(0, "Spotify.exe"),  # inactive
            _Session(1, "WhatsApp.exe"),  # doublon
            _Session(1, "Vox.exe"),  # soi-même
            _Session(1, None),  # sons système
            _Session(1, "Teams.exe"),
        ],
    )
    assert sources.active_audio_apps() == ("WhatsApp", "Teams")


def test_active_audio_apps_ignores_broken_sessions(monkeypatch) -> None:
    class _Exploding:
        @property
        def State(self):
            raise RuntimeError("COM cassé")

    _install_fake_pycaw(monkeypatch, [_Exploding(), _Session(1, "Zoom.exe")])
    monkeypatch.setattr(sources.sys, "platform", "win32")
    assert sources.active_audio_apps() == ("Zoom",)


def test_active_audio_apps_is_empty_outside_windows(monkeypatch) -> None:
    monkeypatch.setattr(sources.sys, "platform", "linux")
    assert sources.active_audio_apps() == ()


# ----------------------------------------------------------------------
# Vue d'ensemble
# ----------------------------------------------------------------------
def test_detect_returns_mic_then_system(monkeypatch) -> None:
    monkeypatch.setattr(sources, "mic_status", lambda device=None: _mic(True))
    monkeypatch.setattr(sources, "system_status", lambda: _system(False))
    statuses = sources.detect()
    assert [status.kind for status in statuses] == ["mic", "system"]
    assert sources.status_dot(statuses) == "warn"


def _mic(available: bool):
    return sources.SourceStatus("mic", "Micro", name="M", available=available, detail="d")


def _system(available: bool):
    return sources.SourceStatus(
        "system", "Son du système", name="S", available=available, detail="d"
    )


def test_status_dot_scenarios() -> None:
    assert sources.status_dot([_mic(True), _system(True)]) == "ok"
    assert sources.status_dot([_mic(True), _system(False)]) == "warn"
    assert sources.status_dot([_mic(False), _system(True)]) == "error"
    assert sources.status_dot([_mic(False), _system(False)]) == "error"


def test_summary_mentions_active_sound() -> None:
    status = sources.SourceStatus(
        "system", "Son du système", name="Sortie", available=True,
        detail="WASAPI", active=("WhatsApp",),
    )
    assert "WhatsApp" in status.summary
    assert "Son du système : Sortie" in status.summary
