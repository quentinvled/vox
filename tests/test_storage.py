"""Rangement des appels et des imports dans un dossier dedie, et migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vox import library, paths, storage


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("VOX_MEDIA_DIR", raising=False)
    monkeypatch.setenv("VOX_DATA_DIR", str(tmp_path / "donnees"))


def test_appels_et_imports_sont_dans_un_dossier_dedie() -> None:
    media = paths.media_dir()
    assert media == paths.data_dir() / "appels-imports"
    assert paths.calls_dir() == media / "Appels"
    assert paths.library_dir() == media / "Imports"
    assert paths.library_index_file() == media / "Imports" / "bibliotheque.jsonl"
    # Les dictées et les réglages ne bougent pas.
    assert paths.recordings_dir() == paths.data_dir() / "enregistrements"
    assert paths.config_file() == paths.data_dir() / "settings.json"


def test_emplacement_peut_etre_force(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOX_MEDIA_DIR", str(tmp_path / "ailleurs"))
    assert paths.calls_dir() == tmp_path / "ailleurs" / "Appels"
    assert paths.library_dir() == tmp_path / "ailleurs" / "Imports"


def test_migration_deplace_appels_imports_et_index() -> None:
    legacy_calls = paths.legacy_calls_dir()
    legacy_calls.mkdir(parents=True)
    (legacy_calls / "2026-10-01_10-00-00_micro.wav").write_bytes(b"audio")
    legacy_transcripts = paths.legacy_library_dir() / "transcripts"
    legacy_transcripts.mkdir(parents=True)
    (legacy_transcripts / "2026-10-01_09-00-00.json").write_text("{}", encoding="utf-8")
    paths.legacy_library_index_file().write_text(
        json.dumps({"id": "2026-10-01_09-00-00", "title": "Réunion"}) + "\n",
        encoding="utf-8",
    )

    moved = storage.migrate()

    assert (paths.calls_dir() / "2026-10-01_10-00-00_micro.wav").read_bytes() == b"audio"
    assert (paths.library_dir() / "transcripts" / "2026-10-01_09-00-00.json").exists()
    assert not (legacy_calls / "2026-10-01_10-00-00_micro.wav").exists()
    assert not legacy_transcripts.parent.exists() or not any(legacy_transcripts.iterdir())
    assert library.get("2026-10-01_09-00-00").title == "Réunion"
    assert len(moved) == 3
    # L'ancien index est conservé en copie, sans être relu ensuite.
    assert (paths.data_dir() / "bibliotheque.jsonl.migre").exists()


def test_migration_idempotente_et_sans_ecrasement() -> None:
    legacy_calls = paths.legacy_calls_dir()
    legacy_calls.mkdir(parents=True)
    (legacy_calls / "appel.wav").write_bytes(b"ancien")
    (paths.calls_dir() / "appel.wav").write_bytes(b"nouveau")

    storage.migrate()
    assert (paths.calls_dir() / "appel.wav").read_bytes() == b"nouveau"
    assert (legacy_calls / "appel.wav").read_bytes() == b"ancien"

    assert storage.migrate() == []


def test_fusion_index_garde_les_entrees_recentes_et_evite_les_doublons() -> None:
    paths.library_index_file().write_text(
        json.dumps({"id": "a", "title": "version récente"}) + "\n",
        encoding="utf-8",
    )
    paths.legacy_library_index_file().write_text(
        "\n".join(
            [
                json.dumps({"id": "a", "title": "version ancienne"}),
                json.dumps({"id": "b", "title": "autre"}),
                "ligne illisible",
            ]
        ),
        encoding="utf-8",
    )

    storage.migrate()

    entries = library.load()
    assert {entry.id for entry in entries} == {"a", "b"}
    assert next(entry for entry in entries if entry.id == "a").title == "version récente"


def test_appels_et_imports_utilisent_le_nouveau_dossier() -> None:
    from vox import calls

    folder = paths.calls_dir()
    assert calls.calls_dir == paths.calls_dir
    assert folder.name == "Appels"
    assert paths.library_dir().name == "Imports"
