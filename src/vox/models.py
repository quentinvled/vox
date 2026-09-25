"""Catalogue des modeles : recuperation en direct, avec cache et repli."""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass, field

from .api import (
    GROQ_STT_PRICING,
    Client,
)
from .api import per_hour_from_catalogue as api_per_hour_from_catalogue
from .paths import models_cache_file

CACHE_TTL_SECONDS = 24 * 3600

# Repli si le reseau est indisponible (tarifs indicatifs $/h d'audio).
FALLBACK_STT: dict[str, list[dict]] = {
    "openrouter": [
        {"id": "qwen/qwen3-asr-1.7b", "name": "Qwen3 ASR 1.7B (rapide)", "per_hour": 0.027},
        {"id": "google/gemini-3.5-transcribe", "name": "Gemini 3.5 Transcribe (precis)", "per_hour": 0.007},
        {"id": "openai/whisper-large-v3-turbo", "name": "Whisper Large v3 Turbo", "per_hour": 0.012},
        {"id": "openai/whisper-large-v3", "name": "Whisper Large v3", "per_hour": 0.027},
        {"id": "nvidia/parakeet-tdt-0.6b-v3", "name": "Parakeet TDT 0.6B v3", "per_hour": 0.09},
        {"id": "deepgram/nova-3", "name": "Deepgram Nova 3", "per_hour": 0.258},
        {"id": "openai/gpt-4o-mini-transcribe", "name": "GPT-4o Mini Transcribe", "per_hour": 0.005},
    ],
    "groq": [
        {"id": "whisper-large-v3-turbo", "name": "Whisper Large v3 Turbo", "per_hour": 0.04},
        {"id": "whisper-large-v3", "name": "Whisper Large v3", "per_hour": 0.111},
    ],
    "openai": [
        {"id": "whisper-1", "name": "Whisper 1", "per_hour": 0.36},
        {"id": "gpt-4o-mini-transcribe", "name": "GPT-4o Mini Transcribe", "per_hour": None},
        {"id": "gpt-4o-transcribe", "name": "GPT-4o Transcribe", "per_hour": None},
    ],
}

FALLBACK_CHAT: dict[str, list[dict]] = {
    "openrouter": [
        {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
        {"id": "openai/gpt-4o-mini", "name": "GPT-4o mini"},
        {"id": "anthropic/claude-3.5-haiku", "name": "Claude 3.5 Haiku"},
    ],
    "groq": [
        {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B"},
        {"id": "llama-3.1-8b-instant", "name": "Llama 3.1 8B"},
    ],
    "openai": [
        {"id": "gpt-4o-mini", "name": "GPT-4o mini"},
        {"id": "gpt-4o", "name": "GPT-4o"},
    ],
}

# Modeles recommandes, mis en avant en tete de liste.
PREFERRED_STT = [
    "qwen/qwen3-asr-1.7b",
    "google/gemini-3.5-transcribe",
    "openai/whisper-large-v3-turbo",
    "whisper-large-v3-turbo",
]
PREFERRED_CHAT = ["google/gemini-2.5-flash", "llama-3.3-70b-versatile", "openai/gpt-4o-mini"]


@dataclass
class Catalogue:
    stt: list[dict] = field(default_factory=list)
    chat: list[dict] = field(default_factory=list)
    provider: str = "openrouter"
    fetched_at: float = 0.0
    from_cache: bool = False
    error: str | None = None


def _read_cache() -> dict | None:
    path = models_cache_file()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(provider: str, stt: list[dict], chat: list[dict]) -> None:
    payload = _read_cache() or {}
    payload[provider] = {"fetched_at": time.time(), "stt": stt, "chat": chat}
    with contextlib.suppress(OSError):
        models_cache_file().write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def per_hour_from_catalogue(prompt: float | None) -> float | None:
    """Convertit `pricing.prompt` du catalogue en dollars par heure.

    L'unite du catalogue n'est pas homogene : les fournisseurs facturant a la
    seconde (Groq, DeepInfra) exposent un prix par seconde, alors qu'Azure
    expose directement un prix par heure. Mesure faite sur
    `microsoft/mai-transcribe-2` : 0,1 correspond bien a 0,10 $/h.
    """
    return api_per_hour_from_catalogue(prompt)


def _sort_preferred(items: list[dict], preferred: list[str]) -> list[dict]:
    def key(item: dict):
        model_id = item.get("id") or ""
        try:
            rank = preferred.index(model_id)
        except ValueError:
            rank = len(preferred)
        return (rank, model_id)

    return sorted(items, key=key)


def fallback(provider: str) -> Catalogue:
    return Catalogue(
        stt=FALLBACK_STT.get(provider, FALLBACK_STT["openrouter"]),
        chat=FALLBACK_CHAT.get(provider, FALLBACK_CHAT["openrouter"]),
        provider=provider,
        from_cache=True,
    )


def load(provider: str = "openrouter", api_key: str = "", force_refresh: bool = False) -> Catalogue:
    """Renvoie le catalogue du fournisseur (cache < 24 h, sinon reseau)."""
    cached = _read_cache() or {}
    entry = cached.get(provider)

    if (
        not force_refresh
        and entry
        and time.time() - float(entry.get("fetched_at") or 0) < CACHE_TTL_SECONDS
        and entry.get("stt")
    ):
        return Catalogue(
            stt=entry["stt"],
            chat=entry.get("chat") or FALLBACK_CHAT.get(provider, []),
            provider=provider,
            fetched_at=float(entry.get("fetched_at") or 0),
            from_cache=True,
        )

    if not api_key:
        if entry and entry.get("stt"):
            return Catalogue(
                stt=entry["stt"],
                chat=entry.get("chat") or FALLBACK_CHAT.get(provider, []),
                provider=provider,
                from_cache=True,
            )
        result = fallback(provider)
        result.error = f"Cle {provider} absente : liste de modeles locale utilisee."
        return result

    try:
        client = Client(provider, api_key)
        stt = _sort_preferred(client.list_stt_models(), PREFERRED_STT)
        chat = _sort_preferred(client.list_chat_models(), PREFERRED_CHAT)
        if not stt:
            raise RuntimeError("aucun modele de transcription renvoye")
    except Exception as exc:
        if entry and entry.get("stt"):
            return Catalogue(
                stt=entry["stt"],
                chat=entry.get("chat") or FALLBACK_CHAT.get(provider, []),
                provider=provider,
                from_cache=True,
                error=str(exc),
            )
        result = fallback(provider)
        result.error = str(exc)
        return result

    _write_cache(provider, stt, chat)
    _merge_measured_rates(stt)
    return Catalogue(stt=stt, chat=chat, provider=provider, fetched_at=time.time())


def _merge_measured_rates(items: list[dict]) -> None:
    """Ajoute a chaque modele le tarif reellement observe dans l'historique."""
    from .stats import measured_rates

    try:
        rates = measured_rates()
    except Exception:
        return
    for item in items:
        found = rates.get(item.get("id") or "")
        if found:
            item["measured_per_hour"] = round(found.per_hour, 4)
            item["measured_seconds"] = round(found.seconds, 1)


def label(item: dict, measured: float | None = None) -> str:
    """Libelle des listes deroulantes.

    Le tarif mesure dans ton historique prime sur l'estimation du catalogue,
    elle-meme marquee d'un « ~ » pour signaler qu'elle est approximative.
    """
    name = item.get("name") or item.get("id") or "?"
    rate = measured or item.get("measured_per_hour")
    if rate:
        return f"{name}  ·  {rate:.3f} $/h"
    per_hour = item.get("per_hour")
    if per_hour:
        return f"{name}  ·  ~{per_hour:.3f} $/h"
    return name


__all__ = [
    "GROQ_STT_PRICING",
    "Catalogue",
    "fallback",
    "label",
    "load",
    "per_hour_from_catalogue",
]
