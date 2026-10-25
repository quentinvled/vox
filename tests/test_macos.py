"""Support macOS : ce qui est verifiable sans Mac (generation, chemins, defauts).

Les parties qui parlent vraiment a macOS (frappe, autorisations, launchctl) ne
peuvent etre testees que sur place ; on verifie ici tout ce qui est du texte,
des chemins et des regles, pour qu'une regression se voie tout de suite.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from vox import autostart, config, injector_mac, permissions, sources, updates
from vox.hotkey import HotkeyManager


# ----------------------------------------------------------------------
# LaunchAgent (demarrage automatique macOS)
# ----------------------------------------------------------------------
def test_plist_content_contient_les_cles_attendues():
    plist = autostart.plist_content(["/Applications/Vox.app/Contents/MacOS/Vox"])

    assert "<key>Label</key>" in plist
    assert autostart.LAUNCH_AGENT_LABEL in plist
    assert "/Applications/Vox.app/Contents/MacOS/Vox" in plist
    assert "<key>RunAtLoad</key>" in plist
    assert plist.startswith("<?xml version=\"1.0\"")


def test_plist_content_est_un_plist_valide():
    import plistlib

    payload = plistlib.loads(
        autostart.plist_content(["/Applications/Vox.app/Contents/MacOS/Vox"]).encode()
    )

    assert payload["Label"] == autostart.LAUNCH_AGENT_LABEL
    assert payload["RunAtLoad"] is True
    assert payload["ProgramArguments"] == ["/Applications/Vox.app/Contents/MacOS/Vox"]


def test_plist_content_echappe_le_chemin():
    plist = autostart.plist_content(["/Applications/a&b/Vox"])

    assert "a&amp;b" in plist
    assert "a&b" not in plist


def test_launch_agent_path_est_dans_la_bibliotheque(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert autostart.launch_agent_path() == (
        tmp_path / "Library" / "LaunchAgents" / "fr.vled.vox.plist"
    )


# ----------------------------------------------------------------------
# Mise a jour macOS
# ----------------------------------------------------------------------
def test_macos_bundle_remonte_jusqu_au_app():
    exe = Path("/Applications/Vox.app/Contents/MacOS/Vox")

    assert updates.macos_bundle(exe) == Path("/Applications/Vox.app")


def test_macos_bundle_hors_application():
    assert updates.macos_bundle(Path("/usr/local/bin/vox")) is None


def test_macos_update_script_attend_le_processus_puis_relance(tmp_path):
    script = updates.macos_update_script(
        4242, tmp_path / "Vox-1.0.zip", Path("/Applications/Vox.app")
    )

    assert "kill -0 4242" in script
    assert "/usr/bin/ditto -x -k" in script
    assert "/usr/bin/open" in script
    # Sans ce retrait, Gatekeeper refuse de relancer une application non signee.
    assert "com.apple.quarantine" in script


# ----------------------------------------------------------------------
# Manifeste de mise a jour : chaque systeme prend son fichier
# ----------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    payload: dict = {}  # noqa: RUF012 - bouchon de test, remplace a chaque cas

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def get(self, *args, **kwargs):
        return _FakeResponse(_FakeClient.payload)


def _manifest(monkeypatch, payload: dict) -> None:
    _FakeClient.payload = payload
    monkeypatch.setattr(updates.httpx, "Client", _FakeClient)


def test_manifeste_macos_prend_larchive_zip(monkeypatch):
    _manifest(
        monkeypatch,
        {
            "version": "9.0.0",
            "url": "https://exemple.tld/Vox-Setup-9.0.0.exe",
            "urls": {"windows": "https://exemple.tld/Vox-Setup-9.0.0.exe"},
        },
    )
    monkeypatch.setattr(updates, "platform_key", lambda: "macos")

    info, reason = updates.check("https://exemple.tld/version.json", "0.10.1")

    assert info is None
    assert "aucun fichier" in reason


def test_manifeste_macos_choisit_le_zip(monkeypatch):
    _manifest(
        monkeypatch,
        {
            "version": "9.0.0",
            "urls": {"macos": "https://exemple.tld/Vox-9.0.0-macos-arm64.zip"},
        },
    )
    monkeypatch.setattr(updates, "platform_key", lambda: "macos")

    info, reason = updates.check("https://exemple.tld/version.json", "0.10.1")

    assert info is not None and reason == ""
    assert info.url.endswith("macos-arm64.zip")


# ----------------------------------------------------------------------
# Frappe sous macOS : validation des raccourcis (sans pynput)
# ----------------------------------------------------------------------
def test_press_combo_refuse_un_raccourci_vide():
    with pytest.raises(injector_mac.InjectorError):
        injector_mac._press_combo("")


def test_press_combo_refuse_une_combinaison_sans_touche_finale():
    with pytest.raises(injector_mac.InjectorError):
        injector_mac._press_combo("ctrl+shift")


def test_press_combo_refuse_une_touche_inconnue():
    with pytest.raises(injector_mac.InjectorError):
        injector_mac._press_combo("cmd+fleche")


# ----------------------------------------------------------------------
# Reglages par defaut selon la plateforme
# ----------------------------------------------------------------------
@pytest.fixture
def plateforme(request, monkeypatch):
    """Force sys.platform puis recharge les modules qui en dependent."""

    def apply(platform: str):
        monkeypatch.setattr(sys, "platform", platform)
        for module in (config, sources):
            importlib.reload(module)
        return config, sources

    yield apply

    monkeypatch.undo()
    for module in (config, sources):
        importlib.reload(module)


def test_defauts_macos(plateforme):
    config_mod, sources_mod = plateforme("darwin")

    assert config_mod.DEFAULT_PASTE_KEYS == "cmd+v"
    assert config_mod.Settings().paste_keys == "cmd+v"
    assert "⌘" in config_mod.HOTKEY_CHOICES["ctrl+win"]
    assert sources_mod.SUPPORTS_SYSTEM_AUDIO is False


def test_defauts_linux(plateforme):
    config_mod, sources_mod = plateforme("linux")

    assert config_mod.DEFAULT_PASTE_KEYS == "ctrl+v"
    assert "Windows" in config_mod.HOTKEY_CHOICES["ctrl+win"]
    assert sources_mod.SUPPORTS_SYSTEM_AUDIO is False


def test_defauts_windows(plateforme):
    config_mod, sources_mod = plateforme("win32")

    assert config_mod.DEFAULT_PASTE_KEYS == "ctrl+v"
    assert sources_mod.SUPPORTS_SYSTEM_AUDIO is True


# ----------------------------------------------------------------------
# Raccourci : la touche Commande de macOS est la touche « win » interne
# ----------------------------------------------------------------------
def test_parse_combo_accepte_cmd():
    assert HotkeyManager._parse_combo("cmd+shift") == {"win", "shift"}


# ----------------------------------------------------------------------
# Son du systeme : son absence ne doit pas allumer d'alerte hors Windows
# ----------------------------------------------------------------------
def test_status_dot_ignore_le_son_du_systeme_hors_windows(monkeypatch):
    monkeypatch.setattr(sources, "SUPPORTS_SYSTEM_AUDIO", False)
    statuses = [
        sources.SourceStatus(sources.KIND_MIC, "Micro", available=True),
        sources.SourceStatus(sources.KIND_SYSTEM, "Son du système", available=False),
    ]

    assert sources.status_dot(statuses) == "ok"


def test_status_dot_signale_le_micro_coupe():
    statuses = [sources.SourceStatus(sources.KIND_MIC, "Micro", available=False)]

    assert sources.status_dot(statuses) == "error"


# ----------------------------------------------------------------------
# Autorisations : hors macOS, rien n'est invente
# ----------------------------------------------------------------------
def test_permissions_vides_hors_macos(monkeypatch):
    monkeypatch.setattr(permissions, "IS_MAC", False)

    assert permissions.statuses() == []
    assert permissions.summary() == ""
    assert permissions.open_settings("accessibility") is False
