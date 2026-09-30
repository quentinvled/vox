"""Tests du raccord des locuteurs entre tranches (passe LLM)."""

from __future__ import annotations

from vox.api import Completion
from vox.speakers import merge_speakers_with_llm
from vox.transcript import Segment, Speaker, Transcript


class FakeChat:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def chat(self, messages, model, *, temperature=0.2, max_tokens=None) -> Completion:
        self.calls.append({"messages": messages, "model": model})
        return Completion(text=self.reply, model=model)


def _transcript() -> Transcript:
    return Transcript(
        segments=[
            Segment(0.0, 2.0, "t0:c0:s0", "Bonjour, on commence ?"),
            Segment(598.0, 601.0, "t0:c1:s0", "Oui, je vous écoute."),
            Segment(602.0, 605.0, "t0:c1:s1", "Moi aussi je suis là."),
            Segment(1200.0, 1203.0, "t0:c2:s1", "On reprend."),
        ],
        speakers=[Speaker("t0:c0:s0"), Speaker("t0:c1:s0"), Speaker("t0:c1:s1"), Speaker("t0:c2:s1")],
    )


def test_merges_labels_from_mapping() -> None:
    reply = (
        "Voici le resultat :\n```json\n"
        '{"etiquettes": {"t0:c0:s0": "A", "t0:c1:s0": "A", "t0:c1:s1": "B", "t0:c2:s1": "B"}}'
        "\n```"
    )
    transcript = _transcript()
    applied = merge_speakers_with_llm(transcript, FakeChat(reply), "modele-test", expected=2)
    assert applied == {"t0:c1:s0": "t0:c0:s0", "t0:c2:s1": "t0:c1:s1"}
    assert transcript.speaker_labels() == ["Locuteur 1", "Locuteur 2"]
    assert [segment.speaker for segment in transcript.segments] == [
        "t0:c0:s0",
        "t0:c0:s0",
        "t0:c1:s1",
        "t0:c1:s1",
    ]


def test_keeps_labels_on_invalid_reply() -> None:
    transcript = _transcript()
    applied = merge_speakers_with_llm(transcript, FakeChat("je ne sais pas"), "modele-test")
    assert applied == {}
    assert len(transcript.speakers) == 4


def test_ignores_unknown_labels_and_single_member_groups() -> None:
    reply = '{"etiquettes": {"t0:c0:s0": "A", "inconnue": "A", "t0:c1:s0": "B"}}'
    transcript = _transcript()
    applied = merge_speakers_with_llm(transcript, FakeChat(reply), "modele-test")
    assert applied == {}
    assert len(transcript.speakers) == 4


def test_single_speaker_does_not_call_the_model() -> None:
    transcript = Transcript(segments=[Segment(0.0, 1.0, "s0", "Bonjour")], speakers=[Speaker("s0")])
    chat = FakeChat("{}")
    assert merge_speakers_with_llm(transcript, chat, "modele-test") == {}
    assert chat.calls == []
