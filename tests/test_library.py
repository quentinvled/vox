"""Tests de la bibliotheque des imports (index, transcripts, corrections)."""

from __future__ import annotations

from pathlib import Path

import pytest

from vox import library
from vox.transcript import Segment, Speaker, Transcript


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VOX_DATA_DIR", str(tmp_path / "donnees"))


def _transcript(source: Path) -> Transcript:
    return Transcript(
        segments=[
            Segment(start=0.0, end=2.0, speaker="t0:c0:s0", text="Bonjour à tous."),
            Segment(start=2.5, end=5.0, speaker="t0:c0:s1", text="Salut, on commence."),
            Segment(start=6.0, end=9.0, speaker="t0:c0:s0", text="Parfait, deux minutes."),
        ],
        speakers=[
            Speaker(id="t0:c0:s0"),
            Speaker(id="t0:c0:s1"),
        ],
        duration=9.0,
        model="microsoft/mai-transcribe-2",
        cost=0.012,
        source=str(source),
        title="Réunion",
        extras={"tranches": 1, "strategie": "Fichier mono"},
    )


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "reunion.m4a"
    path.write_bytes(b"faux audio")
    return path


def test_add_stores_index_and_transcript(tmp_path: Path) -> None:
    source = _source(tmp_path)
    entry = library.add(_transcript(source), source, elapsed=12.5)

    assert entry.id
    assert entry.title == "Réunion"
    assert entry.source == str(source)
    assert entry.seconds == pytest.approx(9.0)
    assert entry.words == 9
    assert entry.speakers == ["Locuteur 1", "Locuteur 2"]
    assert entry.speakers_label() == "2 locuteurs"
    assert entry.cost == pytest.approx(0.012)
    assert entry.extras["duree_traitement"] == 12.5

    loaded = library.load()
    assert [item.id for item in loaded] == [entry.id]
    transcript = library.load_transcript(entry.id)
    assert transcript is not None
    assert transcript.title == "Réunion"
    assert len(transcript.segments) == 3


def test_add_keeps_source_untouched_on_delete(tmp_path: Path) -> None:
    source = _source(tmp_path)
    entry = library.add(_transcript(source), source)
    assert library.transcript_path(entry.id).exists()

    assert library.delete(entry.id) is True
    assert library.load() == []
    assert not library.transcript_path(entry.id).exists()
    assert source.exists(), "supprimer une entree ne doit jamais toucher a l'audio"


def test_two_imports_in_the_same_second_get_distinct_ids(tmp_path: Path) -> None:
    source = _source(tmp_path)
    first = library.add(_transcript(source), source)
    second = library.add(_transcript(source), source)
    assert first.id != second.id
    assert len(library.load()) == 2


def test_failed_import_is_listed_without_transcript(tmp_path: Path) -> None:
    source = _source(tmp_path)
    entry = library.add(None, source, status="erreur", error="réseau indisponible")
    assert entry.status == "erreur"
    assert entry.error == "réseau indisponible"
    assert library.load_transcript(entry.id) is None

    loaded = library.load()
    assert loaded[0].source == str(source)
    assert loaded[0].exists is True


def test_refresh_after_rename_updates_speakers(tmp_path: Path) -> None:
    source = _source(tmp_path)
    entry = library.add(_transcript(source), source)
    transcript = library.load_transcript(entry.id)
    assert transcript is not None
    transcript.rename("t0:c0:s0", "Jonas")
    library.refresh(entry.id, transcript)

    reloaded = library.get(entry.id)
    assert reloaded is not None
    assert reloaded.speakers == ["Jonas", "Locuteur 2"]


def test_merge_speakers_in_stored_transcript(tmp_path: Path) -> None:
    source = _source(tmp_path)
    entry = library.add(_transcript(source), source)
    transcript = library.load_transcript(entry.id)
    assert transcript is not None
    transcript.merge_speakers("t0:c0:s0", "t0:c0:s1")
    library.refresh(entry.id, transcript)

    reloaded = library.get(entry.id)
    assert reloaded is not None
    assert reloaded.speakers == ["Locuteur 1"]
    stored = library.load_transcript(entry.id)
    assert stored is not None
    assert {segment.speaker for segment in stored.segments} == {"t0:c0:s0"}


def test_replace_transcript_accumulates_cost(tmp_path: Path) -> None:
    source = _source(tmp_path)
    entry = library.add(_transcript(source), source)
    transcript = library.load_transcript(entry.id)
    assert transcript is not None
    transcript.cost = 0.02
    updated = library.replace_transcript(entry.id, transcript, model="deepgram/nova-3", cost=0.02)

    assert updated is not None
    assert updated.model == "deepgram/nova-3"
    assert updated.cost == pytest.approx(0.032)
    assert updated.extras["retranscriptions"] == 1


def test_stats_counts_imports(tmp_path: Path) -> None:
    source = _source(tmp_path)
    library.add(_transcript(source), source)
    info = library.stats()
    assert info["count"] == 1
    assert info["seconds"] == pytest.approx(9.0)
    assert info["cost"] == pytest.approx(0.012)


def test_entry_path_resolves_source(tmp_path: Path) -> None:
    source = _source(tmp_path)
    entry = library.add(_transcript(source), source)
    assert entry.path == source
    assert entry.exists is True
