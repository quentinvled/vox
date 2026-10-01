"""Tests de la configuration : valeurs par defaut et migration de la reformulation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vox import config
from vox.paths import config_file


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VOX_DATA_DIR", str(tmp_path / "donnees"))


def test_reword_is_enabled_by_default() -> None:
    assert config.Settings().reword_enabled is True
    assert config.load().reword_enabled is True
    assert config.load().reword_tone == "clean"
    assert config.Settings().clean_imports is True


def test_existing_installation_switches_once() -> None:
    # Fichier ecrit par une version anterieure : reformulation desactivee et
    # aucun marqueur de migration.
    config_file().parent.mkdir(parents=True, exist_ok=True)
    config_file().write_text(
        json.dumps({"api_key": "cle", "reword_enabled": False}), encoding="utf-8"
    )

    settings = config.load()
    assert settings.reword_enabled is True
    assert settings.extras["reword_default_on"] is True
    # La bascule est ecrite : recharger ne change plus rien.
    assert config.load().reword_enabled is True

    # Si l'utilisateur la desactive ensuite, son choix est respecte.
    raw = json.loads(config_file().read_text(encoding="utf-8"))
    raw["reword_enabled"] = False
    config_file().write_text(json.dumps(raw), encoding="utf-8")
    assert config.load().reword_enabled is False


def test_explicit_choice_after_migration_is_kept() -> None:
    # Une fois la bascule faite, sauvegarder un choix explicite le preserve.
    config.save(
        config.Settings(reword_enabled=False, extras={"reword_default_on": True})
    )
    assert config.load().reword_enabled is False
