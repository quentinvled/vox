"""Clients API : transcription (STT) et chat, multi-fournisseurs.

Deux styles d'API coexistent, et le client s'adapte :

* **OpenRouter** : `POST /audio/transcriptions` en JSON avec l'audio en base64.
  Le `prompt` de vocabulaire n'existe pas au niveau racine : il faut le passer
  via `provider.options.<slug>.prompt`, sinon il est ignore (notamment en
  multipart).
* **Groq / OpenAI-compatible** : `POST /audio/transcriptions` en
  `multipart/form-data` avec un champ `prompt` classique. C'est le chemin le
  plus rapide (Groq execute Whisper sur LPU : ~1 s pour 10 s d'audio).
"""

from __future__ import annotations

import base64
import contextlib
import time
from dataclasses import dataclass, field
from typing import Self

import httpx

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
GROQ_BASE = "https://api.groq.com/openai/v1"
OPENAI_BASE = "https://api.openai.com/v1"

# Fournisseurs OpenRouter susceptibles de servir un modele Whisper et qui
# acceptent un prompt. On declare l'option pour chacun : OpenRouter ne transmet
# que celle du fournisseur reellement retenu.
PROMPT_AWARE_PROVIDERS = (
    "groq",
    "deepinfra",
    "together",
    "openai",
    "fireworks",
    "sambanova",
    "mistral",
    "azure",
)

# Tarifs Groq indicatifs ($/h d'audio), utilises pour l'affichage.
GROQ_STT_PRICING = {
    "whisper-large-v3": 0.111,
    "whisper-large-v3-turbo": 0.04,
}


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    base_url: str
    style: str  # "openrouter" | "openai"
    console_url: str


PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider(
        "openrouter",
        "OpenRouter",
        OPENROUTER_BASE,
        "openrouter",
        "https://openrouter.ai/settings/keys",
    ),
    "groq": Provider(
        "groq",
        "Groq (direct, le plus rapide)",
        GROQ_BASE,
        "openai",
        "https://console.groq.com/keys",
    ),
    "openai": Provider(
        "openai",
        "OpenAI (direct)",
        OPENAI_BASE,
        "openai",
        "https://platform.openai.com/api-keys",
    ),
}


class ApiError(RuntimeError):
    """Erreur remontee par un fournisseur."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# Alias historique.
OpenRouterError = ApiError


@dataclass
class Transcript:
    text: str
    model: str = ""
    seconds: float | None = None
    cost: float | None = None
    language: str | None = None
    latency_ms: int = 0
    provider: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Completion:
    text: str
    model: str = ""
    cost: float | None = None
    latency_ms: int = 0


class Client:
    """Client generique pour un fournisseur OpenAI-compatible."""

    def __init__(self, provider: str | Provider, api_key: str, timeout: float = 120.0) -> None:
        if isinstance(provider, str):
            self.provider = PROVIDERS.get(provider, PROVIDERS["openrouter"])
        else:
            self.provider = provider
        self.api_key = (api_key or "").strip()
        self._timeout = timeout
        self._headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.provider.style == "openrouter":
            self._headers["X-Title"] = "Vox"
            self._headers["HTTP-Referer"] = "https://localhost/vox"
        # Client persistant : reutilise la connexion TLS entre les appels
        # (gain de 200 a 600 ms par requete).
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        return self.provider.base_url

    def _check_key(self) -> None:
        if not self.api_key:
            raise ApiError(
                f"Aucune clé {self.provider.label}. Renseigne-la dans les réglages "
                f"({self.provider.console_url})."
            )

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = ""
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                detail = error.get("message") or ""
            elif isinstance(error, str):
                detail = error
            if not detail and isinstance(body, dict) and body.get("message"):
                detail = str(body["message"])
        except Exception:
            detail = response.text[:300]
        if not detail:
            detail = response.text[:300]
        raise ApiError(
            f"HTTP {response.status_code} : {detail or 'erreur inconnue'}", status=response.status_code
        )

    # ------------------------------------------------------------------
    def check_key(self, timeout: float = 20.0) -> dict:
        """Valide la cle. Renvoie un dict d'information (quota, ou nombre de modeles)."""
        self._check_key()
        if self.provider.style == "openrouter":
            response = self._http.get(f"{self.base_url}/key", headers=self._headers)
            self._raise_for_error(response)
            body = response.json()
            return body.get("data") or body

        # Fournisseurs OpenAI-compatibles : pas d'endpoint /key, on valide en
        # listant les modeles accessibles.
        response = self._http.get(f"{self.base_url}/models", headers=self._headers)
        self._raise_for_error(response)
        body = response.json()
        return {"models": len(body.get("data") or [])}

    # ------------------------------------------------------------------
    def transcribe(
        self,
        wav_bytes: bytes,
        model: str,
        *,
        language: str | None = None,
        vocabulary: str | None = None,
        temperature: float = 0.0,
    ) -> Transcript:
        self._check_key()
        started = time.perf_counter()

        if self.provider.style == "openrouter":
            response = self._transcribe_openrouter(
                self._http, wav_bytes, model, language, vocabulary, temperature
            )
        else:
            response = self._transcribe_openai(
                self._http, wav_bytes, model, language, vocabulary, temperature
            )

        latency = int((time.perf_counter() - started) * 1000)
        self._raise_for_error(response)

        body = response.json()
        usage = body.get("usage") or {}
        seconds = usage.get("seconds")
        if seconds is None and "duration" in body:
            seconds = body.get("duration")
        return Transcript(
            text=(body.get("text") or "").strip(),
            model=body.get("model") or model,
            seconds=seconds,
            cost=usage.get("cost") or _duration_cost(self.provider.name, model, seconds),
            language=body.get("language"),
            latency_ms=latency,
            provider=self.provider.name,
            raw=body,
        )

    def _transcribe_openrouter(
        self,
        client: httpx.Client,
        wav_bytes: bytes,
        model: str,
        language: str | None,
        vocabulary: str | None,
        temperature: float,
    ) -> httpx.Response:
        payload: dict = {
            "model": model,
            "input_audio": {
                "data": base64.b64encode(wav_bytes).decode("ascii"),
                "format": "wav",
            },
        }
        if language:
            payload["language"] = language
        if temperature is not None:
            payload["temperature"] = temperature
        if vocabulary and vocabulary.strip():
            payload["provider"] = {
                "options": {name: {"prompt": vocabulary.strip()} for name in PROMPT_AWARE_PROVIDERS}
            }
        return client.post(
            f"{self.base_url}/audio/transcriptions",
            headers={**self._headers, "Content-Type": "application/json"},
            json=payload,
        )

    def _transcribe_openai(
        self,
        client: httpx.Client,
        wav_bytes: bytes,
        model: str,
        language: str | None,
        vocabulary: str | None,
        temperature: float,
    ) -> httpx.Response:
        data: dict[str, str] = {"model": model, "response_format": "json"}
        if language:
            data["language"] = language
        if vocabulary and vocabulary.strip():
            data["prompt"] = vocabulary.strip()
        if temperature is not None:
            data["temperature"] = str(temperature)
        return client.post(
            f"{self.base_url}/audio/transcriptions",
            headers=self._headers,
            data=data,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
        )

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        model: str,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Completion:
        self._check_key()
        payload: dict = {"model": model, "messages": messages, "temperature": temperature}
        if max_tokens:
            payload["max_tokens"] = max_tokens

        started = time.perf_counter()
        response = self._http.post(
            f"{self.base_url}/chat/completions",
            headers={**self._headers, "Content-Type": "application/json"},
            json=payload,
        )
        latency = int((time.perf_counter() - started) * 1000)
        self._raise_for_error(response)

        body = response.json()
        choices = body.get("choices") or []
        text = (choices[0].get("message") or {}).get("content") if choices else ""
        usage = body.get("usage") or {}
        return Completion(
            text=(text or "").strip(),
            model=body.get("model") or model,
            cost=usage.get("cost"),
            latency_ms=latency,
        )

    # ------------------------------------------------------------------
    # Catalogues
    # ------------------------------------------------------------------
    def list_models(self, timeout: float = 30.0) -> list[dict]:
        """Liste brute des modeles du fournisseur (cle requise sauf OpenRouter)."""
        headers = dict(self._headers) if self.api_key else {}
        response = self._http.get(f"{self.base_url}/models", headers=headers)
        self._raise_for_error(response)
        return response.json().get("data") or []

    def list_stt_models(self) -> list[dict]:
        """Modeles de transcription, normalises et tries par prix."""
        if self.provider.style == "openrouter":
            response = self._http.get(
                f"{self.base_url}/models", params={"output_modalities": "transcription"}
            )
            self._raise_for_error(response)
            out = []
            for model in response.json().get("data") or []:
                per_second = _to_float((model.get("pricing") or {}).get("prompt"))
                out.append(
                    {
                        "id": model.get("id"),
                        "name": model.get("name") or model.get("id"),
                        "per_hour": round(per_second * 3600, 4) if per_second else None,
                    }
                )
            out.sort(key=lambda m: (m["per_hour"] is None, m["per_hour"] or 0))
            return out

        out = []
        for model in self.list_models():
            model_id = model.get("id") or ""
            if "whisper" not in model_id and "transcribe" not in model_id:
                continue
            out.append(
                {
                    "id": model_id,
                    "name": model_id,
                    "per_hour": GROQ_STT_PRICING.get(model_id),
                }
            )
        out.sort(key=lambda m: (m["per_hour"] is None, m["per_hour"] or 0))
        return out

    def list_chat_models(self) -> list[dict]:
        out = []
        for model in self.list_models():
            model_id = model.get("id") or ""
            if not model_id or "whisper" in model_id:
                continue
            out.append({"id": model_id, "name": model_id})
        out.sort(key=lambda m: m["id"])
        return out


# ----------------------------------------------------------------------
def _duration_cost(provider: str, model: str, seconds: float | None) -> float | None:
    """Estime le cout quand le fournisseur ne le renvoie pas (Groq)."""
    if not seconds or provider != "groq":
        return None
    per_hour = GROQ_STT_PRICING.get(model)
    if not per_hour:
        return None
    return round(per_hour * seconds / 3600.0, 8)


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
