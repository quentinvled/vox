"""Tests des lancements de programmes externes (sans console sous Windows)."""

from __future__ import annotations

import subprocess

from vox import audiofiles, install, proc


def test_no_window_flags(monkeypatch) -> None:
    monkeypatch.setattr(proc.sys, "platform", "win32")
    assert proc.no_window() == {"creationflags": proc.CREATE_NO_WINDOW}
    monkeypatch.setattr(proc.sys, "platform", "linux")
    assert proc.no_window() == {}


def test_ffmpeg_is_launched_without_console(monkeypatch) -> None:
    monkeypatch.setattr(proc.sys, "platform", "win32")
    captured: dict = {}

    def _fake_run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(audiofiles.subprocess, "run", _fake_run)
    monkeypatch.setattr(audiofiles, "ffmpeg_path", lambda: "ffmpeg")

    audiofiles._run(["ffmpeg", "-version"], timeout=5)
    assert captured["creationflags"] == proc.CREATE_NO_WINDOW
    assert captured["capture_output"] is True


def test_ffmpeg_launch_outside_windows_has_no_flag(monkeypatch) -> None:
    monkeypatch.setattr(proc.sys, "platform", "linux")
    captured: dict = {}

    def _fake_run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(audiofiles.subprocess, "run", _fake_run)
    monkeypatch.setattr(audiofiles, "ffmpeg_path", lambda: "ffmpeg")

    audiofiles._run(["ffmpeg", "-version"], timeout=5)
    assert "creationflags" not in captured


def test_shortcuts_use_hidden_powershell(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(proc.sys, "platform", "win32")
    monkeypatch.setenv("TEMP", str(tmp_path))
    captured: dict = {}

    def _fake_run(args, **kwargs):
        captured.update(kwargs)
        captured["args"] = list(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(install.subprocess, "run", _fake_run)
    monkeypatch.setattr(install, "desktop_dir", lambda: tmp_path)
    monkeypatch.setattr(install, "start_menu_dir", lambda: tmp_path / "menu")
    monkeypatch.setattr(install, "_system_exe", lambda *_parts: "powershell.exe")

    exe = tmp_path / "Vox.exe"
    exe.write_bytes(b"MZ")
    install.create_shortcuts(exe)

    assert captured["creationflags"] == proc.CREATE_NO_WINDOW
    assert captured["capture_output"] is True
    assert captured["args"][0] == "powershell.exe"
    assert not (tmp_path / "vox-shortcuts.ps1").exists()  # script nettoye
