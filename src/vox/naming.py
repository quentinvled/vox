"""Attribution des prenoms aux locuteurs d'un transcript (passe LLM).

La diarisation dit « Locuteur 1/2/3 » ; ce module demande a un modele de chat
de retrouver les vrais prenoms a partir de la conversation elle-meme : on
s'adresse a quelqu'un par son nom, on parle de lui a la troisieme personne,
etc. Les prenoms candidats sont fournis par l'utilisateur (ou deduits).

En cas de doute le modele ne renomme rien : les libelles restent « Locuteur N ».
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata

from .api import Client
from .transcript import Transcript, stamp

log = logging.getLogger("vox")

# Au-dela, on resume la conversation (echantillon par locuteur).
MAX_PROMPT_CHARS = 30000
MAX_TURN_CHARS = 200
DIGEST_TURNS = 6
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM = (
    "Tu analyses une transcription pour identifier qui parle. Tu ne deduis "
    "jamais un prenom d'une simple supposition : tu t'appuies sur des indices "
    "explicites (on interpelle quelqu'un, on parle de lui, on repond a sa "
    "place). Tu reponds uniquement par le JSON demande."
)

_HEADER = """Voici la transcription d'un echange entre plusieurs personnes, avec
des libelles de locuteurs provisoires (Locuteur 1, 2, 3…).

Contexte : {context}
Prenoms possibles : {names}

Indices a chercher :
- quelqu'un interpelle une personne par son prenom (« t'es la, X ? »,
  « je te laisse dire, X ») : X n'est donc pas celui qui parle ;
- quelqu'un parle d'une personne a la troisieme personne (« comme a dit X ») :
  X n'est pas celui qui parle ;
- une personne qui repond juste apres avoir ete interpellee est la personne
  nommee.

Conversation :
{body}

Reponds uniquement par : {{"locuteurs": {{"<libelle>": "<prenom>"}}}}
Regles :
- n'utilise que les prenoms de la liste ci-dessus ;
- si un locuteur ne peut pas etre identifie avec certitude, mets "";
- ne mets jamais le meme prenom sur deux locuteurs differents."""


def rename_in_order(transcript: Transcript, names: list[str]) -> dict[str, str]:
    """Renomme les locuteurs dans l'ordre d'apparition (mapping explicite).

    Plus sur que l'inference : l'utilisateur sait qui est qui. Les locuteurs
    sans nom fourni gardent leur libelle « Locuteur N ».
    """
    clean = [item.strip() for item in names if item and item.strip()]
    mapping: dict[str, str] = {}
    for speaker, name in zip(transcript.speakers, clean, strict=False):
        transcript.rename(speaker.id, name)
        mapping[speaker.id] = name
    if mapping:
        transcript.extras["prenoms"] = mapping
    return mapping


def infer_speaker_names(
    transcript: Transcript,
    client: Client,
    model: str,
    *,
    names: list[str] | None = None,
    context: str = "",
) -> dict[str, str]:
    """Renomme les locuteurs. Renvoie {libelle: prenom} pour ceux trouves."""
    labels = [speaker.id for speaker in transcript.speakers]
    if len(labels) < 2:
        return {}

    candidates = [item.strip() for item in (names or []) if item and item.strip()]
    prompt = _HEADER.format(
        context=context.strip() or "non precise",
        names=", ".join(candidates) if candidates else "deduis-les de la conversation",
        body=_conversation(transcript),
    )
    completion = client.chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        model,
        temperature=0.0,
    )
    aliases = _aliases(transcript)
    mapping = _parse(completion.text, aliases, candidates)
    if not mapping:
        log.warning("Prenoms non identifies. Reponse du modele : %s", (completion.text or "")[:400])
    for label, name in mapping.items():
        transcript.rename(label, name)
    if mapping:
        transcript.extras["prenoms"] = mapping
    return mapping


def _aliases(transcript: Transcript) -> dict[str, str]:
    """Toutes les facons dont le modele peut designer un locuteur."""
    aliases: dict[str, str] = {}
    for index, speaker in enumerate(transcript.speakers, start=1):
        aliases[_key(speaker.id)] = speaker.id
        aliases[_key(f"Locuteur {index}")] = speaker.id
        if speaker.name:
            aliases[_key(speaker.name)] = speaker.id
    return aliases


# ----------------------------------------------------------------------
def _conversation(transcript: Transcript) -> str:
    turns = [
        f"[{stamp(segment.start, short=True)}] {transcript.label_for(segment.speaker)}: "
        f"{segment.text[:MAX_TURN_CHARS]}"
        for segment in transcript.segments
    ]
    body = "\n".join(turns)
    if len(body) <= MAX_PROMPT_CHARS:
        return body
    return _digest(transcript)


def _digest(transcript: Transcript) -> str:
    blocks: list[str] = []
    for speaker in transcript.speakers:
        segments = [item for item in transcript.segments if item.speaker == speaker.id]
        if not segments:
            continue
        sample = [*segments[:DIGEST_TURNS], *segments[-DIGEST_TURNS:]]
        lines = [f"[{stamp(item.start, short=True)}] {item.text[:MAX_TURN_CHARS]}" for item in sample]
        blocks.append(f"### {speaker.id}\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def _parse(text: str, aliases: dict[str, str], candidates: list[str]) -> dict[str, str]:
    match = _JSON_RE.search(text or "")
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    source = payload.get("locuteurs") if isinstance(payload.get("locuteurs"), dict) else payload
    if not isinstance(source, dict):
        return {}

    by_key = {_key(name): name for name in candidates}
    output: dict[str, str] = {}
    used: set[str] = set()
    for alias, raw in source.items():
        label = aliases.get(_key(str(alias)))
        if label is None:
            continue
        name = str(raw or "").strip()
        if not name:
            continue
        if by_key:
            name = by_key.get(_key(name), "")
            if not name:
                continue
        key = _key(name)
        if key in used:
            continue  # un prenom ne peut pas designer deux personnes
        used.add(key)
        output[label] = name
    return output


def _key(name: str) -> str:
    """Comparaison insensible a la casse et aux accents."""
    text = unicodedata.normalize("NFKD", name.strip().lower())
    return "".join(char for char in text if not unicodedata.combining(char))


__all__ = ["infer_speaker_names", "rename_in_order"]
