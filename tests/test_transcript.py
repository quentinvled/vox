"""Tests de la fusion des tranches et des exports de transcript."""

from __future__ import annotations

from vox.transcript import (
    ChunkResult,
    Segment,
    Speaker,
    Transcript,
    assemble,
    segments_from_payload,
    stamp,
)


# ----------------------------------------------------------------------
# Lecture des reponses de l'API
# ----------------------------------------------------------------------
def test_segments_from_payload_reads_speakers() -> None:
    payload = {
        "text": "Bonjour. Salut.",
        "segments": [
            {"start": 0.0, "end": 1.2, "text": "Bonjour.", "speaker": 0},
            {"start": 1.5, "end": 3.0, "text": "Salut.", "speaker": 1},
        ],
    }
    segments = segments_from_payload(payload)
    assert [(item.speaker, item.text) for item in segments] == [("s0", "Bonjour."), ("s1", "Salut.")]


def test_segments_from_words_groups_by_speaker() -> None:
    payload = {
        "words": [
            {"word": "Bonjour", "start": 0.0, "end": 0.4, "speaker": 0},
            {"word": "à", "start": 0.4, "end": 0.5, "speaker": 0},
            {"word": "tous", "start": 0.5, "end": 0.9, "speaker": 0},
            {"word": "Salut", "start": 1.2, "end": 1.6, "speaker": 1},
        ]
    }
    segments = segments_from_payload(payload)
    assert len(segments) == 2
    assert segments[0].text == "Bonjour à tous"
    assert segments[1].speaker == "s1"


def test_segments_from_plain_text() -> None:
    segments = segments_from_payload({"text": "Un texte sans horodatage."})
    assert len(segments) == 1
    assert segments[0].text == "Un texte sans horodatage."
    assert segments_from_payload({}) == []


# ----------------------------------------------------------------------
# Fusion des tranches
# ----------------------------------------------------------------------
def _chunk(
    index: int,
    window_start: float,
    core_start: float,
    core_end: float,
    segments: list[Segment],
    *,
    window_end: float = 0.0,
) -> ChunkResult:
    return ChunkResult(
        index=index,
        track=0,
        window_start=window_start,
        core_start=core_start,
        core_end=core_end,
        window_end=window_end,
        segments=segments,
    )


def test_assemble_offsets_and_dedupes_overlap() -> None:
    first = _chunk(
        0,
        0.0,
        0.0,
        600.0,
        [
            Segment(0.0, 3.0, "s0", "Bonjour à tous"),
            Segment(597.0, 601.0, "s0", "alors voilà"),
        ],
        window_end=602.5,
    )
    second = _chunk(
        1,
        597.5,
        600.0,
        1200.0,
        [
            Segment(0.5, 3.5, "s1", "alors voilà la suite"),
            Segment(5.0, 8.0, "s1", "et donc on continue"),
        ],
        window_end=1202.5,
    )
    transcript = assemble([first, second], duration=1200.0, title="Essai")
    # Le segment à cheval sur la coupe n'apparaît qu'une fois, et les deux
    # étiquettes de locuteur sont raccordées par la zone de recouvrement.
    assert [segment.text for segment in transcript.segments] == [
        "Bonjour à tous",
        "alors voilà",
        "et donc on continue",
    ]
    assert len(transcript.speakers) == 1
    assert transcript.segments[1].start == 597.0


def test_assemble_keeps_separate_speakers_without_overlap_evidence() -> None:
    first = _chunk(0, 0.0, 0.0, 600.0, [Segment(0.0, 3.0, "s0", "Bonjour")], window_end=602.5)
    second = _chunk(
        1, 597.5, 600.0, 1200.0, [Segment(10.0, 12.0, "s1", "Autre voix")], window_end=1202.5
    )
    transcript = assemble([first, second], duration=1200.0)
    # Sans preuve dans le recouvrement, les deux locuteurs restent distincts :
    # c'est le rôle de la fusion manuelle (ou de la passe LLM) ensuite.
    assert len(transcript.speakers) == 2
    assert transcript.segments[0].speaker != transcript.segments[1].speaker


def test_assemble_tracks_are_named_by_piste() -> None:
    left = ChunkResult(
        index=0,
        track=0,
        window_start=0.0,
        core_start=0.0,
        core_end=600.0,
        window_end=600.0,
        track_label="Canal gauche",
        fixed_speaker=True,
        segments=[Segment(0.0, 2.0, "canal-gauche", "Bonjour")],
    )
    right = ChunkResult(
        index=0,
        track=1,
        window_start=0.0,
        core_start=0.0,
        core_end=600.0,
        window_end=600.0,
        track_label="Canal droit",
        fixed_speaker=True,
        segments=[Segment(0.5, 2.5, "canal-droit", "Salut")],
    )
    transcript = assemble([left, right], duration=600.0)
    assert transcript.speaker_labels() == ["Canal gauche", "Canal droit"]


# ----------------------------------------------------------------------
# Edition et exports
# ----------------------------------------------------------------------
def _sample() -> Transcript:
    return Transcript(
        segments=[
            Segment(0.0, 2.0, "s0", "Bonjour."),
            Segment(2.5, 4.0, "s1", "Salut, ça va ?"),
            Segment(5.0, 6.0, "s0", "Très bien."),
        ],
        speakers=[Speaker("s0"), Speaker("s1")],
        title="Appel test",
        duration=6.0,
    )


def test_labels_and_renaming() -> None:
    transcript = _sample()
    assert transcript.label_for("s1") == "Locuteur 2"
    transcript.rename("s1", "Marie")
    assert transcript.label_for("s1") == "Marie"
    transcript.merge_speakers("s0", "s1")
    assert len(transcript.speakers) == 1
    assert {segment.speaker for segment in transcript.segments} == {"s0"}


def test_exports() -> None:
    transcript = _sample()
    assert "Locuteur 1 : Bonjour." in transcript.to_text()
    markdown = transcript.to_markdown()
    assert markdown.startswith("# Appel test")
    assert "[00:02] Locuteur 2" in markdown
    assert "**Locuteur 2**" in transcript.to_markdown(with_time=False)
    srt = transcript.to_srt()
    assert "00:00:00,000 --> 00:00:02,000" in srt
    assert "Locuteur 1 : Bonjour." in srt
    vtt = transcript.to_vtt()
    assert vtt.startswith("WEBVTT")
    assert "<v Locuteur 2>Salut, ça va ?" in vtt


def test_roundtrip_dict() -> None:
    transcript = _sample()
    transcript.rename("s0", "Paul")
    restored = Transcript.from_dict(transcript.to_dict())
    assert restored.to_dict() == transcript.to_dict()


def test_stamp_formats() -> None:
    assert stamp(0.0) == "00:00:00.000"
    assert stamp(3661.5, comma=True) == "01:01:01,500"
    assert stamp(65.0, short=True) == "01:05"
    assert stamp(3665.0, short=True) == "1:01:05"
