"""Remplacement sur : les verrous Windows ne doivent pas casser Vox.exe."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

from vox import install


def test_replace_file_waits_for_windows_lock(tmp_path, monkeypatch) -> None:
    source = tmp_path / "nouveau.exe"
    target = tmp_path / "Vox.exe"
    source.write_bytes(b"nouvelle version")
    target.write_bytes(b"ancienne version")
    replace = install.os.replace
    attempts = []

    def locked(temporary, destination):
        attempts.append(temporary)
        assert target.read_bytes() == b"ancienne version"
        if len(attempts) < 3:
            raise PermissionError("encore ouvert")
        replace(temporary, destination)

    monkeypatch.setattr(install.os, "replace", locked)
    monkeypatch.setattr(install.time, "sleep", Mock())
    install.replace_file(source, target)
    assert target.read_bytes() == b"nouvelle version"
    assert len(attempts) == 3
    assert not attempts[0].exists()


def test_replace_file_timeout_preserves_old_version(tmp_path, monkeypatch) -> None:
    source = tmp_path / "nouveau.exe"
    target = tmp_path / "Vox.exe"
    source.write_bytes(b"nouveau")
    target.write_bytes(b"ancien")
    monkeypatch.setattr(install.os, "replace", Mock(side_effect=PermissionError("verrouille")))
    with pytest.raises(PermissionError):
        install.replace_file(source, target, timeout=0)
    assert target.read_bytes() == b"ancien"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Vox.exe", "nouveau.exe"]


def test_copy_failure_preserves_old_version(tmp_path, monkeypatch) -> None:
    source = tmp_path / "nouveau.exe"
    target = tmp_path / "Vox.exe"
    source.write_bytes(b"nouveau")
    target.write_bytes(b"ancien")

    def fail_copy(source, temporary):
        temporary.write_bytes(b"incomplet")
        raise OSError("disque plein")

    monkeypatch.setattr(install.shutil, "copy2", fail_copy)
    with pytest.raises(OSError, match="disque plein"):
        install.replace_file(source, target)
    assert target.read_bytes() == b"ancien"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Vox.exe", "nouveau.exe"]


@pytest.fixture(scope="module")
def installer_module():
    pytest.importorskip("tkinter")
    path = Path(__file__).resolve().parents[1] / "installer" / "vox_setup.py"
    spec = importlib.util.spec_from_file_location("vox_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installer_relaunches_independent_vox(installer_module, tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in installer_module.PAYLOAD_FILES:
        (source / name).write_bytes(b"nouvelle version")
    destination = tmp_path / "installation"
    destination.mkdir()
    monkeypatch.setattr(installer_module, "payload_dir", lambda: source)
    monkeypatch.setattr(install, "install_dir", lambda: destination)
    monkeypatch.setattr(install, "stop_running", Mock(return_value=2))
    monkeypatch.setattr(install, "create_shortcuts", Mock(return_value=[]))
    monkeypatch.setattr(install, "register_uninstall", Mock())
    popen = Mock()
    monkeypatch.setattr(installer_module.subprocess, "Popen", popen)

    assert installer_module.run_steps(Mock()) == destination / "Vox.exe"
    assert (destination / "Vox.exe").read_bytes() == b"nouvelle version"
    assert popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_update_mode_starts_visible_installer(installer_module, monkeypatch) -> None:
    monkeypatch.setattr(installer_module, "configure_logging", Mock())
    monkeypatch.setattr(installer_module.sys, "platform", "win32")
    monkeypatch.setattr(installer_module.sys, "argv", ["setup.exe", "--update"])
    app = Mock()
    app.run.return_value = 0
    constructor = Mock(return_value=app)
    monkeypatch.setattr(installer_module, "SetupApp", constructor)
    assert installer_module.main() == 0
    constructor.assert_called_once_with(updating=True)


def test_update_window_starts_without_another_click(installer_module, monkeypatch) -> None:
    root = Mock()
    monkeypatch.setattr(installer_module.tk, "Tk", Mock(return_value=root))
    monkeypatch.setattr(installer_module, "asset", lambda name: None)
    monkeypatch.setattr(installer_module.SetupApp, "_build", Mock())
    monkeypatch.setattr(installer_module.SetupApp, "_autosize", Mock())
    app = installer_module.SetupApp(updating=True)
    root.after.assert_called_once_with(100, app.start)


def test_update_error_remains_visible_for_retry(installer_module, monkeypatch) -> None:
    app = installer_module.SetupApp.__new__(installer_module.SetupApp)
    app._updating = True
    app.root = Mock()
    app.status = Mock()
    app.detail = Mock()
    app.install_button = Mock()
    app.quit_button = Mock()
    monkeypatch.setattr(installer_module, "data_dir", lambda: Path("donnees"))
    app._handle(("error", "Vox.exe encore verrouille"))
    assert app._finished
    assert "encore verrouille" in app.detail.configure.call_args.kwargs["text"]
    assert app.install_button.configure.call_args_list[0].kwargs["text"] == "Réessayer"
    app.root.after.assert_not_called()
