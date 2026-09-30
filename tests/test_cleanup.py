"""Tests du nettoyage editorial d'un transcript (client de chat simule)."""

from __future__ import annotations

import json

from vox.api import Completion
from vox.cleanup import _blocks, apply_replacements, clean_transcript
from vox.transcript import Segment, Speaker, Transcript


class FakeChat:
    """Renvoie un texte corrige pour chaque segment numerote du bloc."""

    def __init__(self, *, prefix: str = "corrige", broken: bool = False, cost: float = 0.01) -> None:
        self.prefix = prefix
        self.broken = broken
        self.cost = cost
        self.prompts: list[str] = []

    def chat(self, messages, model, *, temperature=0.2, max_tokens=None) -> Completion:
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if self.broken:
            return Completion(text="desole, je ne peux pas", model=model, cost=self.cost)
        body = prompt.split("Transcription (numero|locuteur|texte) :\n", 1)[1]
        body = body.split("\n\nReponds uniquement", 1)[0]
        items = []
        for line in body.splitlines():
            if "|" not in line:
                continue
            index = line.split("|", 1)[0].strip()
            items.append({"i": int(index), "text": f"{self.prefix} {index}"})
        return Completion(text=json.dumps({"segments": items}), model=model, cost=self.cost)


def _transcript(count: int = 5) -> Transcript:
    return Transcript(
        segments=[Segment(float(i), float(i) + 1.0, "s0", f"texte brut {i}") for i in range(count)],
        speakers=[Speaker("s0", "Khalis")],
        title="Essai",
    )


def test_clean_transcript_rewrites_texts() -> None:
    transcript = _transcript(5)
    chat = FakeChat()
    report = clean_transcript(transcript, chat, "modele-test", glossary="Khalis (et non Calis)")
    assert report["blocs"] == 1
    assert report["segments_modifies"] == 5
    assert report["blocs_en_echec"] == 0
    assert report["cout"] == 0.01
    assert [segment.text for segment in transcript.segments] == [f"corrige {i}" for i in range(5)]
    # Horodatages et locuteurs intacts.
    assert [(segment.start, segment.speaker) for segment in transcript.segments] == [
        (float(i), "s0") for i in range(5)
    ]
    assert "Khalis" in chat.prompts[0]


def test_clean_transcript_keeps_text_on_failure() -> None:
    transcript = _transcript(3)
    report = clean_transcript(transcript, FakeChat(broken=True), "modele-test")
    assert report["blocs_en_echec"] == 1
    assert report["segments_modifies"] == 0
    assert [segment.text for segment in transcript.segments] == [
        "texte brut 0",
        "texte brut 1",
        "texte brut 2",
    ]


def test_blocks_split_on_budget() -> None:
    segments = [Segment(float(i), float(i) + 1.0, "s0", "x" * 1000) for i in range(50)]
    blocks = _blocks(segments, budget=5000)
    assert len(blocks) > 1
    assert [index for block in blocks for index in block] == list(range(50))


def test_apply_replacements_ignores_unknown_indices() -> None:
    transcript = _transcript(2)
    changed = apply_replacements(transcript, {0: "un", 7: "hors bornes"})
    assert changed == 1
    assert transcript.segments[0].text == "un"
    assert transcript.segments[1].text == "texte brut 1"
