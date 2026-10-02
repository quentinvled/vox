"""Tests du recollage des deux pistes d'un appel (micro + systeme)."""

from __future__ import annotations

import pytest

from vox import calls
from vox.transcript import Segment, Speaker, Transcript


def _mic() -> Transcript:
    return Transcript(
        segments=[
            Segment(0.0, 1.0, "t0:c0:s0", "Bonjour à tous."),
            Segment(10.0, 12.0, "t0:c0:s0", "Je te confirme."),
        ],
        speakers=[Speaker(id="t0:c0:s0")],
        duration=12.0,
        model="microsoft/mai-transcribe-2",
        cost=0.01,
        language="fr",
    )


def _system() -> Transcript:
    return Transcript(
        segments=[
            Segment(0.5, 3.0, "t0:c0:s0", "Salut, on commence."),
            Segment(3.5, 5.0, "t0:c0:s1", "Oui, très bien."),
        ],
        speakers=[Speaker(id="t0:c0:s0"), Speaker(id="t0:c0:s1")],
        duration=5.0,
        model="microsoft/mai-transcribe-2",
        cost=0.02,
        warnings=["petit souci"],
    )


def test_merge_tracks_offsets_and_labels() -> None:
    transcript = calls.merge_tracks(_mic(), _system(), offset=2.0, title="Appel test")

    assert transcript.title == "Appel test"
    assert transcript.speaker_labels() == [
        "Moi",
        "Interlocuteur 1",
        "Interlocuteur 2",
    ]
    # Ordre chronologique, segments systeme decales de 2 s.
    starts = [round(segment.start, 2) for segment in transcript.segments]
    assert starts == [0.0, 2.5, 5.5, 10.0]
    system_first = next(item for item in transcript.segments if "Salut" in item.text)
    assert system_first.start == 2.5
    assert system_first.speaker != calls.MOI
    assert transcript.cost == pytest.approx(0.03)
    assert transcript.duration == pytest.approx(12.0)
    assert transcript.warnings == ["petit souci"]
    assert transcript.extras["diarisation_systeme"] == 2


def test_merge_tracks_without_system_keeps_mic_only() -> None:
    transcript = calls.merge_tracks(_mic(), None, title="Note")
    assert transcript.title == "Note"
    assert transcript.speaker_labels() == ["Locuteur 1"]
    assert len(transcript.segments) == 2


def test_merge_tracks_keeps_known_system_speaker_names() -> None:
    system = _system()
    system.rename("t0:c0:s0", "Jonas")
    transcript = calls.merge_tracks(_mic(), system, offset=0.0)
    labels = transcript.speaker_labels()
    assert "Jonas" in labels
    assert "Interlocuteur 2" in labels


def test_merge_tracks_empty_returns_empty_transcript() -> None:
    transcript = calls.merge_tracks(None, None, title="Vide")
    assert transcript.title == "Vide"
    assert transcript.segments == []
    assert transcript.speakers == []
