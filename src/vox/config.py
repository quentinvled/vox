"""Reglages persistants + chargement de la cle API.

La cle peut venir, par ordre de priorite :
  1. de la variable d'environnement OPENROUTER_API_KEY
  2. d'un fichier .env (racine du projet ou dossier de donnees)
  3. du champ `api_key` de settings.json
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .paths import config_file, data_dir, project_root
from .stats import DEFAULT_TYPING_WPM

# --- Modeles par defaut ---------------------------------------------------

# Defaut choisi sur mesures : le plus rapide ET exact sur des clips francais.
# MAI-Transcribe 2 (Microsoft) est multilingue et identifie la langue tout seul.
DEFAULT_STT_MODEL = "microsoft/mai-transcribe-2"
# Alternative « qualite maximale » (meilleure sur les noms propres, 3x plus lente).
QUALITY_STT_MODEL = "google/gemini-3.5-transcribe"
DEFAULT_CHAT_MODEL = "google/gemini-2.5-flash"

# Modeles par defaut quand le fournisseur n'a pas la nomenclature OpenRouter.
DEFAULT_STT_MODEL_GROQ = "whisper-large-v3-turbo"
DEFAULT_CHAT_MODEL_GROQ = "llama-3.3-70b-versatile"

# Manifeste de mise a jour : un asset « version.json » attache a la derniere
# release GitHub. L'URL est stable tant qu'une nouvelle release est publiee
# (voir .github/workflows/release.yml).
DEFAULT_MANIFEST_URL = (
    "https://github.com/quentinvled/vox/releases/latest/download/version.json"
)

# Cle d'environnement / champ de settings associe a chaque fournisseur.
ENV_KEYS: dict[str, str] = {
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
}
KEY_FIELDS: dict[str, str] = {
    "openrouter": "api_key",
    "groq": "groq_api_key",
    "openai": "openai_api_key",
}

# Raccourcis proposés dans l'UI -> (ctrl, shift, alt, win)
HOTKEY_CHOICES: dict[str, str] = {
    "ctrl+shift": "Ctrl + Maj",
    "ctrl+alt": "Ctrl + Alt",
    "ctrl+win": "Ctrl + Windows",
    "alt+shift": "Alt + Maj",
    "ctrl+alt+shift": "Ctrl + Alt + Maj",
    "custom": "Personnalisé (enregistrer une combinaison)",
}

# Modificateurs acceptés dans une combinaison personnalisée.
HOTKEY_MODIFIERS: dict[str, str] = {
    "ctrl": "Ctrl",
    "shift": "Maj",
    "alt": "Alt",
    "win": "Windows",
}


@dataclass
class Settings:
    """Tous les reglages de l'application."""

    # --- API ---
    provider: str = "openrouter"
    api_key: str = ""
    groq_api_key: str = ""
    openai_api_key: str = ""
    stt_model: str = DEFAULT_STT_MODEL
    chat_model: str = DEFAULT_CHAT_MODEL
    language: str = "fr"
    vocabulary_prompt: str = ""

    # --- Declenchement ---
    hotkey: str = "ctrl+shift"
    hotkey_mode: str = "push_to_talk"  # push_to_talk | toggle
    double_tap_enter: bool = False

    # --- Audio ---
    input_device: int | None = None
    sample_rate: int = 16000
    max_record_seconds: int = 120
    min_record_seconds: float = 0.4
    silence_autostop: bool = False
    silence_threshold: float = 0.012
    silence_hold_seconds: float = 2.0

    # --- Sortie ---
    inject_method: str = "paste"  # paste | type
    paste_keys: str = "ctrl+v"
    add_space: bool = True
    strip_short_period: bool = True

    # --- Reformulation ---
    reword_enabled: bool = False
    reword_tone: str = "clean"
    reword_custom_prompt: str = ""

    # --- Interface ---
    theme: str = "dark"
    overlay_position: list[int] | None = None
    # 0 = la pilule ne se masque jamais toute seule.
    overlay_hide_delay: int = 0
    sounds: bool = True
    history_enabled: bool = True
    # Conserve le WAV de chaque dictee dans le dossier de donnees.
    save_recordings: bool = True
    # 0 = on garde les enregistrements indefiniment.
    recording_retention_days: int = 30
    # La pilule n'apparait que pendant l'ecoute puis disparait d'elle-meme.
    hide_after_listening: bool = True
    autostart: bool = False
    notify_on_start: bool = True
    show_in_taskbar: bool = False

    # --- Mises a jour ---
    check_updates: bool = True
    update_manifest_url: str = DEFAULT_MANIFEST_URL
    show_overlay_on_result: bool = True

    # --- Divers ---
    extras: dict = field(default_factory=dict)

    # --- Statistiques ---
    # Vitesse de frappe de reference (mots/minute) pour le « temps gagne ».
    typing_wpm: float = DEFAULT_TYPING_WPM

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        # Le theme clair a ete retire : on reste en sombre, meme si un ancien
        # settings.json contenait « light ».
        self.theme = "dark"

    # ------------------------------------------------------------------
    @property
    def effective_key(self) -> str:
        """Cle du fournisseur selectionne."""
        return resolve_api_key(self)

    @property
    def key_field(self) -> str:
        return KEY_FIELDS.get(self.provider, "api_key")


def _load_dotenv_key(variable: str) -> str | None:
    """Cherche une variable dans un .env, sans dependance externe."""
    candidates = [project_root() / ".env", data_dir() / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        try:
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                if name.strip() == variable:
                    return value.strip().strip('"').strip("'")
        except OSError:
            continue
    return None


def resolve_api_key(settings: Settings | None = None) -> str:
    """Resolution en cascade : env, .env, puis champ de settings.json."""
    provider = settings.provider if settings else "openrouter"
    variable = ENV_KEYS.get(provider, "OPENROUTER_API_KEY")
    field_name = KEY_FIELDS.get(provider, "api_key")

    env = os.environ.get(variable, "").strip()
    if env:
        return env
    dotenv = _load_dotenv_key(variable)
    if dotenv:
        return dotenv
    if settings is not None:
        return str(getattr(settings, field_name, "") or "").strip()
    return ""


def default_models(provider: str) -> tuple[str, str]:
    """Modeles par defaut (STT, chat) d'un fournisseur."""
    if provider == "groq":
        return DEFAULT_STT_MODEL_GROQ, DEFAULT_CHAT_MODEL_GROQ
    if provider == "openai":
        return "whisper-1", "gpt-4o-mini"
    return DEFAULT_STT_MODEL, DEFAULT_CHAT_MODEL


def load() -> Settings:
    path = config_file()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()

    defaults = Settings()
    known = {f.name for f in fields(Settings)}
    optional = {"input_device", "overlay_position"}
    kwargs = {}
    for key, value in raw.items():
        if key not in known:
            continue
        # Un champ non optionnel a None (fichier ecrit par une version
        # anterieure, edition manuelle) doit revenir a sa valeur par defaut.
        if value is None and key not in optional:
            value = getattr(defaults, key)
        kwargs[key] = value
    extras = {k: v for k, v in raw.items() if k not in known}
    settings = Settings(**kwargs)
    if extras:
        settings.extras.update(extras)
    return settings


def save(settings: Settings) -> None:
    payload = asdict(settings)
    if not payload.get("extras"):
        payload.pop("extras", None)
    tmp: Path = config_file().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(config_file())
