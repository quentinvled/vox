"""Chemins de l'application (donnees locales, multi-plateforme)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Vox"


def _base_dir() -> Path:
    override = os.environ.get("VOX_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    # Linux et apparentes : repertoires XDG.
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_NAME


def data_dir() -> Path:
    path = _base_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    return data_dir() / "settings.json"


def history_file() -> Path:
    return data_dir() / "history.jsonl"


def models_cache_file() -> Path:
    return data_dir() / "models-cache.json"


def recordings_dir() -> Path:
    """WAV de toutes les dictees, conserves localement."""
    path = data_dir() / "enregistrements"
    path.mkdir(parents=True, exist_ok=True)
    return path


def recordings_index_file() -> Path:
    return data_dir() / "enregistrements.jsonl"


def downloads_dir() -> Path:
    """Dossier ou deposer les mises a jour telechargees.

    On prefere le dossier « Telechargements » de l'utilisateur (la ou il
    cherchera naturellement) ; a defaut, un sous-dossier des donnees.
    """
    home = Path.home()
    for candidate in (home / "Downloads", home / "Téléchargements"):
        if candidate.is_dir():
            return candidate
    fallback = data_dir() / "mises-a-jour"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def log_file() -> Path:
    return data_dir() / "vox.log"


def project_root() -> Path:
    """Racine du projet, ou dossier de l'executable si l'app est figee."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]
