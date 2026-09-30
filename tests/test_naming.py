"""Tests de l'attribution des prenoms (client de chat simule)."""

from __future__ import annotations

import json

from vox.api import Completion
from vox.naming import infer_speaker_names
from vox.transcript import Segment, Speaker, Transcript


class FakeChat:
    def __init__(self, payload: dict | str) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def chat(self, messages, model, *, temperature=0.2, max_tokens=None) -> Completion:
        self.prompts.append(messages[-1]["content"])
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return Completion(text=text, model=model, cost=0.02)


def _transcript() -> Transcript:
    return Transcript(
        segments=[
            Segment(0.0, 2.0, "s0", "Attends, t'es là, Jonas ?"),
            Segment(2.0, 4.0, "s1", "Oui, je suis là."),
            Segment(4.0, 6.0, "s2", "Comme a dit Jonas, on peut faire autrement."),
        ],
        speakers=[Speaker("s0"), Speaker("s1"), Speaker("s2")],
    )


def test_infers_names_from_conversation() -> None:
    chat = FakeChat({"locuteurs": {"s0": "Mikael", "s1": "Jonas", "s2": "Quentin"}})
    transcript = _transcript()
    mapping = infer_speaker_names(
        transcript, chat, "modele-test", names=["Jonas", "Quentin", "Mikael"]
    )
    assert mapping == {"s0": "Mikael", "s1": "Jonas", "s2": "Quentin"}
    assert transcript.speaker_labels() == ["Mikael", "Jonas", "Quentin"]
    assert "Jonas" in chat.prompts[0]


def test_ignores_names_outside_the_list() -> None:
    chat = FakeChat({"locuteurs": {"s0": "Robert", "s1": "Jonas"}})
    transcript = _transcript()
    mapping = infer_speaker_names(transcript, chat, "modele-test", names=["Jonas", "Quentin"])
    assert mapping == {"s1": "Jonas"}


def test_never_gives_one_name_to_two_speakers() -> None:
    chat = FakeChat({"locuteurs": {"s0": "Jonas", "s1": "Jonas", "s2": ""}})
    transcript = _transcript()
    mapping = infer_speaker_names(transcript, chat, "modele-test", names=["Jonas"])
    assert mapping == {"s0": "Jonas"}


def test_candidates_match_without_accents_or_case() -> None:
    chat = FakeChat({"locuteurs": {"s0": "MIKAEL"}})
    transcript = _transcript()
    mapping = infer_speaker_names(transcript, chat, "modele-test", names=["Mikaël", "Jonas"])
    assert mapping == {"s0": "Mikaël"}


def test_invalid_reply_changes_nothing() -> None:
    transcript = _transcript()
    mapping = infer_speaker_names(transcript, FakeChat("aucune idée"), "modele-test")
    assert mapping == {}
    assert transcript.speaker_labels() == ["Locuteur 1", "Locuteur 2", "Locuteur 3"]
