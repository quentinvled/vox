"""Chemins de l'application (donnees locales Windows)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Vox"


def _base_dir() -> Path:
    override = os.environ.get("VOX_DATA_DIR")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APP_NAME
    return Path.home() / "AppData" / "Local" / APP_NAME


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


def log_file() -> Path:
    return data_dir() / "vox.log"


def project_root() -> Path:
    """Racine du projet, ou dossier de l'executable si l'app est figee."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]
