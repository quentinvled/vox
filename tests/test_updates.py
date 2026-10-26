"""Telechargement et passage de relais a l'installeur, sans reseau."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from vox import proc, updates


def test_independent_environment_does_not_mutate_parent(monkeypatch) -> None:
    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)
    monkeypatch.setenv("VOX_DATA_DIR", "donnees")
    env = proc.independent_env()
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert env["VOX_DATA_DIR"] == "donnees"
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in os.environ


def test_windows_installer_is_visible_and_independent(tmp_path, monkeypatch) -> None:
    downloaded = tmp_path / "Vox-Setup.exe"
    downloaded.write_bytes(b"installer")
    popen = Mock()
    monkeypatch.setattr(updates.subprocess, "Popen", popen)
    monkeypatch.setattr(updates.sys, "platform", "win32")

    assert updates.install(downloaded) == (True, "")
    args, kwargs = popen.call_args
    assert args[0] == [str(downloaded), "--update"]
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert kwargs["creationflags"] == proc.CREATE_NO_WINDOW


def test_installer_launch_failure_keeps_vox_open(tmp_path, monkeypatch) -> None:
    downloaded = tmp_path / "Vox-Setup.exe"
    downloaded.touch()
    monkeypatch.setattr(updates.sys, "platform", "win32")
    monkeypatch.setattr(updates.subprocess, "Popen", Mock(side_effect=OSError("refuse")))
    ok, reason = updates.install(downloaded)
    assert not ok
    assert "refuse" in reason


def test_download_only_publishes_complete_file(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "Vox-Setup.exe"
    progress = []

    def serve(request):
        assert not destination.exists()
        return httpx.Response(200, content=b"installer")

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        monkeypatch.setattr(updates.httpx, "stream", client.stream)
        assert updates.download(
            "https://example.test/setup", destination,
            on_progress=lambda received, total: progress.append((received, total)),
        ) == destination
    assert destination.read_bytes() == b"installer"
    assert progress == [(9, 9)]
    assert not destination.with_suffix(".exe.part").exists()


def test_failed_download_removes_partial_file(tmp_path, monkeypatch) -> None:
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx.ReadError("interrompu")

    destination = tmp_path / "Vox-Setup.exe"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=BrokenStream()))
    with httpx.Client(transport=transport) as client:
        monkeypatch.setattr(updates.httpx, "stream", client.stream)
        with pytest.raises(httpx.ReadError):
            updates.download("https://example.test/setup", destination)
    assert not destination.exists()
    assert not destination.with_suffix(".exe.part").exists()


def test_cached_update_still_waits_for_dictation(tmp_path, monkeypatch) -> None:
    from vox.app import VoxApp

    destination = tmp_path / "Vox-Setup.exe"
    destination.write_bytes(b"installer")
    monkeypatch.setattr(updates, "cached_file", lambda *args: destination)
    window = Mock()
    app = SimpleNamespace(
        _update_in_progress=False,
        _update_info=updates.UpdateInfo("99.0", "https://example.test/setup"),
        _settings_window=window,
        pipeline=SimpleNamespace(recording=True, busy=False),
        _install_update=Mock(),
    )
    app._on_update_downloaded = lambda path: VoxApp._on_update_downloaded(app, path)
    later = Mock()
    monkeypatch.setattr("vox.app.QTimer.singleShot", later)

    VoxApp.start_update(app)
    app._install_update.assert_not_called()
    window.show_waiting_to_install.assert_called_once()
    assert app._update_in_progress
    assert later.call_args.args[0] == 4000

    # Un deuxieme clic ne lance ni un autre transfert ni une autre installation.
    VoxApp.start_update(app)
    assert later.call_count == 1
    app.pipeline.recording = False
    later.call_args.args[1]()
    app._install_update.assert_called_once_with(destination)


def test_update_check_cannot_erase_download_progress() -> None:
    from vox.app import VoxApp

    app = SimpleNamespace(_update_in_progress=True, _settings_window=Mock())
    VoxApp.check_updates(app, manual=True)
    VoxApp._on_update_checked(app, None, "a jour")
    assert app._settings_window.mock_calls == []
