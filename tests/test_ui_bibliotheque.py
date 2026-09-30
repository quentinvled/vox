"""Tests hors ecran de la bibliotheque (fenetre Qt + workers d'import)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication

from vox import audiofiles, imports, library, recordings, routing
from vox.app import _ImportWorker, _NamesWorker
from vox.config import Settings
from vox.transcript import Segment, Speaker, Transcript
from vox.ui.history_window import RecordingsWindow


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VOX_DATA_DIR", str(tmp_path / "donnees"))


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _transcript(source: Path) -> Transcript:
    return Transcript(
        segments=[
            Segment(start=0.0, end=2.0, speaker="t0:c0:s0", text="Bonjour à tous."),
            Segment(start=2.5, end=4.0, speaker="t0:c0:s1", text="Salut, on commence."),
        ],
        speakers=[Speaker(id="t0:c0:s0"), Speaker(id="t0:c0:s1")],
        duration=4.0,
        model="microsoft/mai-transcribe-2",
        cost=0.01,
        source=str(source),
        title="Réunion test",
    )


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "reunion.m4a"
    path.write_bytes(b"faux audio")
    return path


# ----------------------------------------------------------------------
# Fenetre
# ----------------------------------------------------------------------
def test_window_lists_dictations_and_imports(qapp, tmp_path: Path) -> None:
    wav = recordings.save_audio(b"RIFF----WAVEfmt ", 3.0)
    recordings.note(wav, "ok", seconds=3.0, text="Une dictée de test.")
    source = _source(tmp_path)
    entry = library.add(_transcript(source), source)

    window = RecordingsWindow(Settings())
    try:
        assert window.list_widget.count() == 2
        window._set_filter("imports")
        visible = [
            window.list_widget.item(i)
            for i in range(window.list_widget.count())
            if not window.list_widget.item(i).isHidden()
        ]
        assert len(visible) == 1
        assert entry.id in str(visible[0].data(0x0100))  # Qt.UserRole
    finally:
        window.deleteLater()


def test_import_detail_shows_segments_and_renames(qapp, tmp_path: Path) -> None:
    source = _source(tmp_path)
    entry = library.add(_transcript(source), source)
    window = RecordingsWindow(Settings())
    try:
        window._select_key("import", entry.id)
        assert window.import_title.text() == "Réunion test"
        assert window.segments_list.count() == 2
        assert window.names_button.isEnabled()
        assert window.export_button.isEnabled()

        stored = library.load_transcript(entry.id)
        assert stored is not None
        stored.rename("t0:c0:s0", "Jonas")
        library.refresh(entry.id, stored)
        window.on_import_updated(entry.id)
        assert "Jonas" in window._transcript.speaker_labels()

        window._merge_speakers("t0:c0:s0", "t0:c0:s1")
        assert window._transcript.speaker_labels() == ["Jonas"]
        assert library.get(entry.id).speakers == ["Jonas"]
    finally:
        window.deleteLater()


def test_import_progress_row_appears(qapp, tmp_path: Path) -> None:
    window = RecordingsWindow(Settings())
    try:
        window.show()
        qapp.processEvents()
        assert not window.progress_frame.isVisible()
        window.set_import_running(True, "Import : test.m4a")
        assert window.progress_frame.isVisible()
        window.set_import_progress(2, 5, "Tranche 2/5")
        assert window.progress_bar.value() == 2
        window.set_import_running(False)
        assert not window.progress_frame.isVisible()
    finally:
        window.deleteLater()


# ----------------------------------------------------------------------
# Workers
# ----------------------------------------------------------------------
def _fake_result(source: Path) -> imports.ImportResult:
    return imports.ImportResult(
        transcript=_transcript(source),
        info=audiofiles.AudioInfo(duration=4.0, channels=1, samplerate=16000, codec="aac"),
        strategy=routing.Strategy(kind="mono", reason="test", tracks=()),
        model="microsoft/mai-transcribe-2",
        chunks=1,
    )


def test_import_worker_stores_entry(monkeypatch, tmp_path: Path) -> None:
    source = _source(tmp_path)
    monkeypatch.setattr("vox.app.process_file", lambda *_a, **_k: _fake_result(source))
    worker = _ImportWorker([str(source)], Settings())
    imported: list[str] = []
    worker.imported.connect(imported.append)
    worker.run()

    assert len(imported) == 1
    entry = library.get(imported[0])
    assert entry is not None
    assert entry.title == "Réunion test"
    assert entry.speakers == ["Locuteur 1", "Locuteur 2"]


def test_import_worker_records_failure(monkeypatch, tmp_path: Path) -> None:
    source = _source(tmp_path)

    def _boom(*_a, **_k):
        raise RuntimeError("réseau indisponible")

    monkeypatch.setattr("vox.app.process_file", _boom)
    worker = _ImportWorker([str(source)], Settings())
    failures: list[tuple[str, str]] = []
    worker.failed.connect(lambda path, message: failures.append((path, message)))
    worker.run()

    assert failures and "réseau" in failures[0][1]
    entry = library.load()[0]
    assert entry.status == "erreur"
    assert "réseau" in entry.error


def test_import_worker_stops_before_start(monkeypatch, tmp_path: Path) -> None:
    source = _source(tmp_path)
    called: list[str] = []
    monkeypatch.setattr(
        "vox.app.process_file", lambda path, *_a, **_k: called.append(str(path))
    )
    worker = _ImportWorker([str(source)], Settings())
    cancels: list[bool] = []
    worker.cancelled.connect(lambda: cancels.append(True))
    worker.stop()
    worker.run()

    assert cancels == [True]
    assert called == []


def test_import_worker_replaces_transcript(monkeypatch, tmp_path: Path) -> None:
    source = _source(tmp_path)
    entry = library.add(_transcript(source), source)
    improved = _transcript(source)
    improved.segments.append(
        Segment(start=5.0, end=6.0, speaker="t0:c0:s0", text="Une phrase de plus.")
    )
    improved.duration = 6.0
    result = _fake_result(source)
    result.transcript = improved
    monkeypatch.setattr("vox.app.process_file", lambda *_a, **_k: result)

    worker = _ImportWorker([str(source)], Settings(), replace_id=entry.id)
    replaced: list[str] = []
    worker.replaced.connect(replaced.append)
    worker.run()

    assert replaced == [entry.id]
    stored = library.load_transcript(entry.id)
    assert stored is not None and len(stored.segments) == 3
    updated = library.get(entry.id)
    assert updated is not None and updated.words == 10


def test_names_worker_applies_mapping(monkeypatch, tmp_path: Path) -> None:
    source = _source(tmp_path)
    entry = library.add(_transcript(source), source)

    class _FakeClient:
        def __init__(self, *_a, **_k) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

    def _fake_infer(transcript, _client, _model, **_kwargs):
        transcript.rename("t0:c0:s0", "Jonas")
        return {"t0:c0:s0": "Jonas"}

    monkeypatch.setattr("vox.app.Client", _FakeClient)
    monkeypatch.setattr("vox.app.infer_speaker_names", _fake_infer)
    monkeypatch.setattr("vox.app.config_module.key_for", lambda *_a, **_k: "cle")

    worker = _NamesWorker(entry.id, Settings())
    done: list[tuple[str, dict]] = []
    worker.done.connect(lambda eid, mapping: done.append((eid, mapping)))
    worker.run()

    assert done and done[0][1] == {"t0:c0:s0": "Jonas"}
    assert library.get(entry.id).speakers == ["Jonas", "Locuteur 2"]
