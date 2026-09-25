"""Orchestration : enregistrement -> transcription -> reformulation -> insertion."""

from __future__ import annotations

import json
import queue
import re
import time
from datetime import datetime

from PySide6.QtCore import QObject, QThread, Signal

from . import config as config_module
from . import injector, recordings, reword, sounds
from .api import ApiError, Client
from .config import Settings
from .paths import history_file
from .recorder import Recorder, RecorderError

_PUNCT = set(".,;:!?…")


class _Worker(QThread):
    """Thread unique qui sérialise les appels réseau et l'injection."""

    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._jobs: queue.Queue = queue.Queue()

    def submit(self, job) -> None:
        self._jobs.put(job)

    def run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                job()
            except Exception as exc:
                self.failed.emit(str(exc))

    def shutdown(self) -> None:
        self._jobs.put(None)
        self.wait(4000)


class Pipeline(QObject):
    """Coeur fonctionnel de l'application."""

    recording_changed = Signal(bool)
    busy_changed = Signal(bool)
    status_changed = Signal(str)
    notice = Signal(str, str)  # (niveau: info|error, message)
    transcript_ready = Signal(str, dict)
    finalized = Signal(str, dict)
    retranscribed = Signal(str, str)  # (nom du fichier audio, nouveau texte)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.recorder = Recorder(
            samplerate=settings.sample_rate,
            device=settings.input_device,
            max_seconds=settings.max_record_seconds,
        )
        self.worker = _Worker()
        self.worker.failed.connect(self._on_worker_failure)
        self.worker.start()

        self.last_transcript: str = ""
        self.last_final: str = ""
        self.last_meta: dict = {}
        self._recording = False
        self._busy = False
        self._last_injection_at = 0.0
        self._cached_client = None
        self._cached_signature: tuple | None = None

    # ------------------------------------------------------------------
    # Etat
    # ------------------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def busy(self) -> bool:
        return self._busy

    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.recorder.samplerate = settings.sample_rate
        self.recorder.device = settings.input_device
        self.recorder.max_seconds = settings.max_record_seconds

    # ------------------------------------------------------------------
    # Enregistrement
    # ------------------------------------------------------------------
    def start_recording(self) -> None:
        if self._recording:
            return
        if self._busy:
            self._notify("info", "Transcription en cours…")
            return
        try:
            self.recorder.start()
        except RecorderError as exc:
            self._fail(str(exc))
            return
        self._recording = True
        self.recording_changed.emit(True)
        self.status_changed.emit("recording")
        if self.settings.sounds:
            sounds.play("start")

    def stop_recording(self) -> None:
        if not self._recording:
            return
        wav = self.recorder.stop()
        had_sound = self.recorder.has_speech
        self._recording = False
        self.recording_changed.emit(False)
        if self.settings.sounds:
            sounds.play("stop")

        duration = len(wav) / (self.settings.sample_rate * 2) if wav else 0.0
        # Le WAV part sur le disque avant tout appel reseau : rien n'est perdu
        # si la transcription echoue.
        audio = ""
        if self.settings.save_recordings:
            audio = recordings.save_audio(wav, duration)
        if not wav or duration < self.settings.min_record_seconds:
            if audio:
                recordings.note(audio, "court", seconds=duration)
            self.status_changed.emit("idle")
            self._notify("info", "Rien capté.")
            return
        if not had_sound:
            # Inutile d'appeler l'API : sur du silence, les modeles Whisper
            # inventent du texte (caracteres chinois, « Sous-titres », etc.).
            if audio:
                recordings.note(audio, "vide", seconds=duration)
            self.status_changed.emit("idle")
            self._notify("info", "Aucun son détecté.")
            return

        self._set_busy(True)
        self.status_changed.emit("transcribing")
        self.worker.submit(lambda: self._transcribe(wav, duration, audio))

    def cancel_recording(self) -> None:
        """Abandon silencieux (autre touche pressee pendant la combinaison)."""
        if not self._recording:
            return
        self.recorder.cancel()
        self._recording = False
        self.recording_changed.emit(False)
        self.status_changed.emit("cancelled")

    def toggle(self) -> None:
        if self._recording:
            self.stop_recording()
        else:
            self.start_recording()

    # ------------------------------------------------------------------
    # Coeur : transcription puis insertion
    # ------------------------------------------------------------------
    def _client(self) -> Client:
        """Client partage (la connexion TLS est ainsi reutilisee)."""
        key = config_module.resolve_api_key(self.settings)
        signature = (self.settings.provider, key)
        if self._cached_client is None or self._cached_signature != signature:
            if self._cached_client is not None:
                self._cached_client.close()
            self._cached_client = Client(self.settings.provider, key)
            self._cached_signature = signature
        return self._cached_client

    def _transcribe(self, wav: bytes, duration: float, audio: str = "") -> None:
        def keep(status: str, error: str = "") -> None:
            if audio:
                recordings.note(
                    audio,
                    status,
                    seconds=duration,
                    error=error,
                    model=self.settings.stt_model,
                )

        try:
            client = self._client()
            result = client.transcribe(
                wav,
                self.settings.stt_model,
                language=self.settings.language or None,
                vocabulary=self.settings.vocabulary_prompt,
            )
        except ApiError as exc:
            keep("erreur", str(exc))
            self._fail(str(exc), key_missing=not self.settings.effective_key)
            return
        except Exception as exc:
            keep("erreur", str(exc))
            self._fail(f"Erreur réseau : {exc}")
            return

        text = _post_process(result.text, self.settings)
        if not text or _looks_like_hallucination(text, self.settings.language):
            keep("vide")
            self._set_busy(False)
            self.status_changed.emit("idle")
            self._notify("info", "Rien de reconnaissable.")
            return

        meta = {
            "model": result.model or self.settings.stt_model,
            "seconds": result.seconds or duration,
            "cost": result.cost,
            "latency_ms": result.latency_ms,
            "language": result.language,
            "tone": None,
        }
        self.last_transcript = text
        self.last_meta = dict(meta)
        self.transcript_ready.emit(text, dict(meta))

        final = text
        if self.settings.reword_enabled:
            self.status_changed.emit("rewording")
            final, reword_meta = self._reword(client, text)
            meta.update(reword_meta)

        self.last_final = final
        self.last_meta = dict(meta)

        injection_error = None
        try:
            self._inject(final)
        except injector.InjectorError as exc:
            injection_error = str(exc)

        self._save_history(final, meta)
        if audio:
            recordings.note(
                audio,
                "ok",
                seconds=meta.get("seconds") or duration,
                text=final,
                transcript=text,
                model=meta.get("model", ""),
                cost=meta.get("cost") or 0.0,
                latency_ms=meta.get("latency_ms") or 0,
                language=meta.get("language"),
            )
        self._set_busy(False)

        if injection_error:
            self._fail(f"Texte prêt mais insertion impossible : {injection_error}")
            self.finalized.emit(final, dict(meta))
            return

        self.status_changed.emit("done")
        self.finalized.emit(final, dict(meta))
        if self.settings.sounds:
            sounds.play("done")

    def _reword(self, client: Client, text: str):
        try:
            completion = client.chat(
                reword.messages(
                    text,
                    self.settings.reword_tone,
                    self.settings.reword_custom_prompt,
                    self.settings.vocabulary_prompt,
                ),
                self.settings.chat_model,
            )
        except Exception as exc:
            self._notify("error", f"Reformulation échouée, texte brut conservé ({exc}).")
            return text, {"tone": None}
        cleaned = _strip_wrapping(completion.text)
        if not cleaned:
            return text, {"tone": None}
        return cleaned, {
            "tone": self.settings.reword_tone,
            "chat_model": completion.model or self.settings.chat_model,
            "reword_cost": completion.cost,
            "reword_latency_ms": completion.latency_ms,
        }

    def _inject(self, text: str) -> None:
        payload = text
        now = time.monotonic()
        if self.settings.add_space and self._last_injection_at and now - self._last_injection_at < 30:
            payload = " " + payload
        injector.inject(payload, self.settings.inject_method, self.settings.paste_keys)
        self._last_injection_at = time.monotonic()

    def reword_text(self, text: str, tone: str) -> None:
        """Reformule un texte deja transcrit (bouton « Reformuler »)."""
        if not text.strip():
            return
        self._set_busy(True)
        self.status_changed.emit("rewording")

        def job() -> None:
            try:
                client = self._client()
                completion = client.chat(
                    reword.messages(
                        text, tone, self.settings.reword_custom_prompt, self.settings.vocabulary_prompt
                    ),
                    self.settings.chat_model,
                )
                final = _strip_wrapping(completion.text) or text
                self.last_final = final
                try:
                    self._inject(final)
                except injector.InjectorError as exc:
                    self._fail(f"Insertion impossible : {exc}")
                    return
                self.status_changed.emit("done")
                self.finalized.emit(final, {"tone": tone, "model": completion.model})
            except Exception as exc:
                self._fail(str(exc))
            finally:
                self._set_busy(False)

        self.worker.submit(job)

    def reinsert_last(self) -> None:
        if not self.last_final:
            self._notify("info", "Aucun texte à réinsérer.")
            return
        try:
            injector.inject(self.last_final, self.settings.inject_method, self.settings.paste_keys)
            self._notify("info", "Texte réinséré.")
        except injector.InjectorError as exc:
            self._fail(str(exc))

    def copy_last(self) -> None:
        if not self.last_final:
            self._notify("info", "Aucun texte à copier.")
            return
        try:
            injector.set_clipboard_text(self.last_final)
            self._notify("info", "Copie dans le presse-papier.")
        except injector.InjectorError as exc:
            self._fail(str(exc))

    # ------------------------------------------------------------------
    # Historique des enregistrements
    # ------------------------------------------------------------------
    def copy_text(self, text: str) -> bool:
        """Copie un texte quelconque (depuis l'historique)."""
        if not text.strip():
            return False
        try:
            injector.set_clipboard_text(text)
            return True
        except injector.InjectorError as exc:
            self._fail(str(exc))
            return False

    def insert_text(self, text: str) -> None:
        """Reinsere dans la fenetre active un texte venu de l'historique."""
        if not text.strip():
            return
        self.last_final = text
        self.reinsert_last()

    def retranscribe(self, audio: str) -> None:
        """Relance la transcription d'un enregistrement deja sur disque."""
        path = recordings.audio_path(audio)
        if not path.exists():
            self._notify("error", "Fichier audio introuvable.")
            return
        if self._busy:
            self._notify("info", "Une transcription est déjà en cours.")
            return
        self._set_busy(True)
        self.status_changed.emit("transcribing")

        def job() -> None:
            try:
                wav = path.read_bytes()
                client = self._client()
                result = client.transcribe(
                    wav,
                    self.settings.stt_model,
                    language=self.settings.language or None,
                    vocabulary=self.settings.vocabulary_prompt,
                )
            except ApiError as exc:
                self._fail(str(exc))
                return
            except Exception as exc:
                self._fail(f"Erreur réseau : {exc}")
                return

            text = _post_process(result.text, self.settings)
            if not text or _looks_like_hallucination(text, self.settings.language):
                self._set_busy(False)
                self.status_changed.emit("done")
                self._notify("info", "Rien de reconnaissable dans cet enregistrement.")
                return

            recordings.update_text(
                audio,
                text,
                model=result.model or self.settings.stt_model,
                latency_ms=result.latency_ms or 0,
            )
            self.last_transcript = text
            self.last_final = text
            self._set_busy(False)
            self.status_changed.emit("done")
            self.retranscribed.emit(audio, text)
            self._notify("info", "Retranscription prête.")

        self.worker.submit(job)

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    def _set_busy(self, value: bool) -> None:
        if self._busy != value:
            self._busy = value
            self.busy_changed.emit(value)

    def _notify(self, level: str, message: str) -> None:
        self.notice.emit(level, message)

    def _fail(self, message: str, key_missing: bool = False) -> None:
        self._set_busy(False)
        self.status_changed.emit("error")
        if key_missing:
            message = "Clé OpenRouter manquante. Ouvre les réglages pour la saisir."
        if self.settings.sounds:
            sounds.play("error")
        self._notify("error", message)

    def _on_worker_failure(self, message: str) -> None:
        self._fail(message)

    def _save_history(self, text: str, meta: dict) -> None:
        if not self.settings.history_enabled:
            return
        record = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "text": text,
            "transcript": self.last_transcript,
            **meta,
        }
        try:
            with history_file().open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def shutdown(self) -> None:
        if self._recording:
            self.recorder.cancel()
        self.worker.shutdown()
        if self._cached_client is not None:
            self._cached_client.close()
            self._cached_client = None


# ----------------------------------------------------------------------
# Post-traitement deterministe du texte transcrit
# ----------------------------------------------------------------------
def _post_process(text: str, settings: Settings) -> str:
    value = " ".join((text or "").split())
    if not value:
        return ""
    if settings.strip_short_period:
        value = _strip_short_period(value)
    return value


def _strip_short_period(text: str) -> str:
    """Retire le point final d'une phrase tres courte sans autre ponctuation."""
    words = text.split()
    if len(words) > 5:
        return text
    if not text.endswith(".") or text.endswith("..."):
        return text
    body = text[:-1]
    if any(char in _PUNCT for char in body):
        return text
    return body


_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")

# Sorties typiques des modeles Whisper quand ils hallucinent sur du silence
# ou du bruit de fond.
_HALLUCINATION_PATTERNS = (
    r"^sous[- ]?titres?",
    r"^subtitles?",
    r"amara\.org",
    r"^merci d'avoir regard",
    r"^thanks for watching",
    r"^abonnez[- ]vous",
    r"^transcription par",
    r"^\[.*\]$",
    r"^\(.*\)$",
    r"^[♪♫\s.…,_-]+$",
)

# Langues qui doivent s'ecrire en alphabet latin.
_LATIN_LANGUAGES = {
    "fr", "en", "es", "de", "it", "pt", "nl",
    "sv", "da", "no", "fi", "pl", "cs", "tr", "ro", "hu",
}


def _looks_like_hallucination(text: str, language: str) -> bool:
    """Detecte une sortie inventee plutot qu'une vraie transcription."""
    stripped = text.strip().strip("♪♫.,;:!?…\"'«» -")
    if not stripped:
        return True
    for pattern in _HALLUCINATION_PATTERNS:
        if re.match(pattern, stripped, re.IGNORECASE):
            return True
    # Une langue latine qui ne rend aucun caractere latin = hors sujet.
    if language in _LATIN_LANGUAGES:
        return not re.search(r"[A-Za-zÀ-ÿ]", stripped)
    return False


def _strip_wrapping(text: str) -> str:
    """Enleve les artefacts classiques d'un LLM (bloc de code, guillemets)."""
    value = (text or "").strip()
    if not value:
        return ""
    value = _FENCE.sub("", value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'«»":
        inner = value[1:-1].strip()
        if inner:
            value = inner
    return value


__all__ = ["ApiError", "Pipeline"]
