"""Raccord des locuteurs entre les tranches (passe LLM).

Chaque tranche revient avec ses propres etiquettes (« locuteur 0 », « 1 »…)
qui ne veulent rien dire d'une tranche a l'autre. La fusion s'appuie d'abord
sur les zones de recouvrement (`transcript.assemble`) ; ce module fait le
deuxieme passage : un modele rapide lit la conversation et regroupe les
etiquettes qui designent la meme personne.

En cas d'echec, on ne bloque jamais : les etiquettes restent telles quelles.
"""

from __future__ import annotations

import json
import re

from .api import Client
from .transcript import Transcript, stamp

# Au-dela, le prompt complet est trop long : on passe a un resume par locuteur.
MAX_PROMPT_CHARS = 24000
# Longueur maximale d'une replique dans le prompt.
MAX_TURN_CHARS = 200
# Nombre de repliques gardees par locuteur dans le resume reduit.
DIGEST_TURNS = 4

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM = (
    "Tu aides a reconstituer les locuteurs d'une conversation retranscrite par "
    "une machine. Reponds uniquement par le JSON demande, sans commentaire."
)


def merge_speakers_with_llm(
    transcript: Transcript,
    client: Client,
    model: str,
    *,
    expected: int | None = None,
) -> dict[str, str]:
    """Fusionne les etiquettes locales. Renvoie {etiquette_fusionnee: garde}."""
    labels = [speaker.id for speaker in transcript.speakers]
    if len(labels) < 2:
        return {}

    prompt = _build_prompt(transcript, expected)
    completion = client.chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        model,
        temperature=0.0,
    )
    mapping = _parse_mapping(completion.text)
    if not mapping:
        return {}
    return _apply(transcript, mapping, labels)


# ----------------------------------------------------------------------
def _build_prompt(transcript: Transcript, expected: int | None) -> str:
    turns = [
        f"[{stamp(segment.start, short=True)}] {segment.speaker}: {segment.text[:MAX_TURN_CHARS]}"
        for segment in transcript.segments
    ]
    body = "\n".join(turns)
    if len(body) > MAX_PROMPT_CHARS:
        body = _per_speaker_digest(transcript)
    count = (
        f"La conversation reunit exactement {expected} personnes."
        if expected
        else "Le nombre de personnes est inconnu."
    )
    return (
        "Voici une conversation decoupee en tranches par un traitement "
        "automatique. Les etiquettes de locuteurs (t0:c1:s0, t0:c2:s1…) sont "
        "propres a chaque tranche : la meme personne peut porter plusieurs "
        "etiquettes differentes.\n\n"
        f"{count}\n\n"
        "Retrouve les personnes reelles et regroupe les etiquettes.\n\n"
        f"{body}\n\n"
        "Reponds uniquement par un objet JSON de la forme :\n"
        '{"etiquettes": {"<etiquette>": "<lettre du groupe>"}}\n'
        "Regles :\n"
        "- une etiquette par personne et par tranche ;\n"
        "- ne fusionne que si c'est vraiment la meme personne (continuite de la "
        "discussion, reponses, sujets, tics de langage) ;\n"
        "- n'invente aucune personne et n'oublie aucune etiquette ;\n"
        "- une personne peut n'apparaitre que dans une seule tranche."
    )


def _per_speaker_digest(transcript: Transcript) -> str:
    blocks: list[str] = []
    for speaker in transcript.speakers:
        segments = [item for item in transcript.segments if item.speaker == speaker.id]
        if not segments:
            continue
        sample = segments[:DIGEST_TURNS]
        if len(segments) > DIGEST_TURNS * 2:
            sample = [*sample, *segments[-DIGEST_TURNS:]]
        words = sum(len(item.text.split()) for item in segments)
        lines = [
            f"[{stamp(item.start, short=True)}] {item.text[:MAX_TURN_CHARS]}" for item in sample
        ]
        blocks.append(f"### {speaker.id} ({words} mots)\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def _parse_mapping(text: str) -> dict[str, str]:
    match = _JSON_RE.search(text or "")
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    mapping = payload.get("etiquettes") if isinstance(payload.get("etiquettes"), dict) else payload
    return {
        str(key): str(value)
        for key, value in mapping.items()
        if isinstance(key, str) and isinstance(value, (str, int))
    }


def _apply(transcript: Transcript, mapping: dict[str, str], labels: list[str]) -> dict[str, str]:
    """Applique le regroupement propose, en gardant l'etiquette la plus ancienne."""
    position = {label: index for index, label in enumerate(labels)}
    groups: dict[str, list[str]] = {}
    for label, group in mapping.items():
        if label in position:
            groups.setdefault(str(group), []).append(label)

    applied: dict[str, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda item: position[item])
        keep = ordered[0]
        for drop in ordered[1:]:
            transcript.merge_speakers(keep, drop)
            applied[drop] = keep
    return applied


__all__ = ["merge_speakers_with_llm"]
