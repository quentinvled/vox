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


def documents_dir() -> Path:
    """Dossier « Documents » de l'utilisateur, visible depuis l'explorateur.

    Sous Windows on interroge le vrai dossier connu : il peut avoir ete
    redirige vers OneDrive, et `~\\Documents` ne pointe alors pas au bon
    endroit.
    """
    if sys.platform == "win32":
        known = _windows_documents_dir()
        if known is not None:
            return known
    candidate = Path.home() / "Documents"
    return candidate if candidate.is_dir() else Path.home()


def _windows_documents_dir() -> Path | None:
    try:
        import ctypes
        from ctypes import wintypes

        class _Guid(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        folder = _Guid(
            0xFDD39AD0,
            0x238F,
            0x46AF,
            (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
        )
        pointer = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder), 0, None, ctypes.byref(pointer)
        )
        if result != 0 or not pointer.value:
            return None
        value = Path(pointer.value)
        ctypes.windll.ole32.CoTaskMemFree(pointer)
        return value
    except (AttributeError, OSError, ValueError):
        return None


def media_dir() -> Path:
    """Dossier dedie aux appels et aux imports, hors des donnees techniques.

    Sur Windows il s'agit de `Documents\\Vox`, un dossier visible et facile a
    retrouver (sauvegarde, synchronisation) ; les dictées, les reglages et les
    journaux restent dans le dossier de donnees de l'application.
    """
    override = os.environ.get("VOX_MEDIA_DIR")
    if override:
        path = Path(override)
    elif os.environ.get("VOX_DATA_DIR"):
        # Tests et installations portables : tout reste sous VOX_DATA_DIR.
        path = data_dir() / "appels-imports"
    else:
        path = documents_dir() / APP_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Support en lecture seule ou synchronisation indisponible : on ne
        # perd pas la possibilite d'enregistrer.
        path = data_dir() / "appels-imports"
        path.mkdir(parents=True, exist_ok=True)
    return path


def calls_dir() -> Path:
    """Pistes audio des appels enregistres par Vox (micro + systeme)."""
    path = media_dir() / "Appels"
    path.mkdir(parents=True, exist_ok=True)
    return path


def library_dir() -> Path:
    """Imports : index, transcripts et exports generes par l'application."""
    path = media_dir() / "Imports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def library_index_file() -> Path:
    return library_dir() / "bibliotheque.jsonl"


def legacy_calls_dir() -> Path:
    """Ancien emplacement des pistes d'appel (avant le dossier dedie)."""
    return data_dir() / "appels"


def legacy_library_dir() -> Path:
    """Ancien emplacement des transcripts d'import."""
    return data_dir() / "bibliotheque"


def legacy_library_index_file() -> Path:
    return data_dir() / "bibliotheque.jsonl"


def library_transcripts_dir() -> Path:
    path = library_dir() / "transcripts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def updates_dir() -> Path:
    """Dossier prive ou Vox telecharge ses mises a jour.

    On ne depose jamais un installateur (plusieurs dizaines de Mo) dans le
    dossier « Telechargements » de l'utilisateur : il s'y accumulerait. Ce
    dossier est vide apres chaque installation (voir `updates.install`).
    """
    path = data_dir() / "mises-a-jour"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file() -> Path:
    return data_dir() / "vox.log"


def project_root() -> Path:
    """Racine du projet, ou dossier de l'executable si l'app est figee."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]
