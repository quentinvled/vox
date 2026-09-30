"""Nettoyage editorial d'un transcript par un modele de chat.

Corrige les erreurs de reconnaissance, la ponctuation et les noms propres,
supprime les repetitions et les tics de langage, sans jamais resumer ni
inventer. Les horodatages et les locuteurs ne sont jamais modifies : seuls les
textes des segments changent.

Le transcript est decoupe en blocs (pour rester sous les limites de sortie du
modele) ; un bloc en echec est simplement laisse tel quel.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .api import Client
from .transcript import Segment, Transcript

log = logging.getLogger("vox")

# Budget de caracteres d'un bloc envoye au modele (~3 000 tokens d'entree).
BLOCK_CHARS = 8000
# Plafond de sortie : un bloc trop long est recoupe en deux et retente.
MAX_TOKENS = 6000
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM = (
    "Tu corriges des transcriptions automatiques en francais. Tu ne resumes "
    "jamais, tu n'inventes rien, tu ne commentes pas : tu reponds uniquement "
    "par le JSON demande."
)

_HEADER = """Tu corriges la transcription automatique d'un echange oral.

Contexte : {context}
Orthographe a respecter : {glossary}

Regles :
- corrige les erreurs de reconnaissance, la ponctuation et les majuscules ;
- corrige les noms propres d'apres le contexte et l'orthographe ci-dessus ;
- supprime les repetitions et begaiements (« le le », « de de », « je je je »)
  et les « euh » inutiles, sans changer le sens ni le niveau de langue ;
- ne resume pas, n'ajoute aucune information, n'en supprime aucune ;
- garde exactement les memes numeros, dans le meme ordre, un texte par numero ;
- si un passage est incomprehensible, recopie-le tel quel.

Transcription (numero|locuteur|texte) :
{body}

Reponds uniquement par : {{"segments": [{{"i": 0, "text": "..."}}, ...]}}"""


def clean_transcript(
    transcript: Transcript,
    client: Client,
    model: str,
    *,
    glossary: str = "",
    context: str = "",
    progress: Callable[[int, int], None] | None = None,
    workers: int = 3,
) -> dict:
    """Reecrit le texte des segments. Renvoie un compte-rendu (pour l'UI).

    Les blocs partent en parallele ; un bloc dont la reponse est illisible
    (JSON tronque par la limite de sortie du modele) est recoupe en deux et
    retente, jusqu'a garder le texte d'origine si c'est vraiment impossible.
    """
    blocks = _blocks(transcript.segments)
    total = len(blocks)
    replacements: dict[int, str] = {}
    failed = 0
    cost = 0.0
    done = 0
    lock = threading.Lock()

    def handle(block: list[int]) -> tuple[dict[int, str], float]:
        """Traite un bloc (et le coupe en deux si la reponse est inutilisable)."""
        if not block:
            return {}, 0.0
        found, spent = _ask(client, model, block, transcript, glossary, context)
        if found:
            return found, spent
        if len(block) <= 2:
            return {}, spent
        middle = len(block) // 2
        left, left_cost = handle(block[:middle])
        right, right_cost = handle(block[middle:])
        return {**left, **right}, spent + left_cost + right_cost

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(handle, block) for block in blocks]
        for future in futures:
            found, spent = future.result()
            with lock:
                replacements.update(found)
                cost += spent
                if not found:
                    failed += 1
                done += 1
                if progress is not None:
                    progress(done, total)

    changed = apply_replacements(transcript, replacements)
    report = {
        "blocs": total,
        "segments_modifies": changed,
        "blocs_en_echec": failed,
        "modele": model,
        "cout": round(cost, 6),
    }
    transcript.extras["nettoyage"] = report
    return report


# ----------------------------------------------------------------------
def _blocks(segments: list[Segment], budget: int = BLOCK_CHARS) -> list[list[int]]:
    """Indices globaux regroupes en blocs de taille raisonnable."""
    blocks: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, segment in enumerate(segments):
        length = len(segment.text) + 12
        if current and size + length > budget:
            blocks.append(current)
            current, size = [], 0
        current.append(index)
        size += length
    if current:
        blocks.append(current)
    return blocks


def _ask(
    client: Client,
    model: str,
    block: list[int],
    transcript: Transcript,
    glossary: str,
    context: str,
) -> tuple[dict[int, str], float]:
    body = "\n".join(
        f"{index}|{transcript.label_for(transcript.segments[index].speaker)}"
        f"|{transcript.segments[index].text}"
        for index in block
    )
    prompt = _HEADER.format(
        context=context.strip() or "non precise",
        glossary=glossary.strip() or "aucune indication particuliere",
        body=body,
    )
    completion = client.chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        model,
        temperature=0.0,
        max_tokens=MAX_TOKENS,
    )
    return _parse(completion.text, block), float(completion.cost or 0.0)


def _parse(text: str, block: list[int]) -> dict[int, str]:
    match = _JSON_RE.search(text or "")
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    items = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return {}
    allowed = set(block)
    output: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("i"))
        except (TypeError, ValueError):
            continue
        clean = str(item.get("text") or "").strip()
        if index in allowed and clean:
            output[index] = clean
    return output


def apply_replacements(transcript: Transcript, replacements: dict[int, str]) -> int:
    """Applique les textes corriges au transcript. Renvoie le nombre de changements."""
    changed = 0
    for index, clean in replacements.items():
        if 0 <= index < len(transcript.segments) and transcript.segments[index].text.strip() != clean.strip():
            transcript.segments[index].text = clean
            changed += 1
    return changed


__all__ = ["BLOCK_CHARS", "apply_replacements", "clean_transcript"]
