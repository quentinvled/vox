"""Fenêtre de réglages."""

from __future__ import annotations

import contextlib
import dataclasses
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, recordings
from ..api import PROVIDERS, Client
from ..config import HOTKEY_CHOICES, HOTKEY_MODIFIERS, Settings
from ..models import Catalogue
from ..models import label as model_label
from ..recorder import list_input_devices
from ..reword import TONES
from .wheel import NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox

LANGUAGES: list[tuple[str, str]] = [
    ("", "Détection automatique"),
    ("fr", "Français"),
    ("en", "Anglais"),
    ("es", "Espagnol"),
    ("de", "Allemand"),
    ("it", "Italien"),
    ("pt", "Portugais"),
    ("nl", "Néerlandais"),
    ("ru", "Russe"),
    ("zh", "Chinois"),
    ("ja", "Japonais"),
    ("ko", "Coréen"),
    ("ar", "Arabe"),
    ("hi", "Hindi"),
    ("tr", "Turc"),
    ("pl", "Polonais"),
    ("sv", "Suédois"),
    ("uk", "Ukrainien"),
]

PASTE_KEYS = ["ctrl+v", "ctrl+shift+v", "ctrl+alt+v", "shift+insert"]

# Correspondance touche Qt -> nom interne, pour la capture d'un raccourci.
_QT_MODIFIERS: tuple[tuple[Qt.KeyboardModifier, str], ...] = (
    (Qt.ControlModifier, "ctrl"),
    (Qt.ShiftModifier, "shift"),
    (Qt.AltModifier, "alt"),
    (Qt.MetaModifier, "win"),
)
_QT_MODIFIER_KEYS: dict[int, str] = {
    int(Qt.Key_Control): "ctrl",
    int(Qt.Key_Shift): "shift",
    int(Qt.Key_Alt): "alt",
    int(Qt.Key_Meta): "win",
}
# Ordre d'affichage conventionnel : Ctrl + Alt + Maj.
_MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")


def combo_from_event(event: QKeyEvent) -> str | None:
    """Traduit un appui clavier en combinaison de modificateurs.

    Renvoie None tant que moins de deux modificateurs sont enfonces : un
    raccourci a un seul modificateur serait ingerable au quotidien.
    """
    pressed = {name for flag, name in _QT_MODIFIERS if event.modifiers() & flag}
    own = _QT_MODIFIER_KEYS.get(int(event.key()))
    if own:
        # Qt n'inclut pas toujours la touche modificateur elle-meme dans
        # modifiers() : on l'ajoute explicitement.
        pressed.add(own)
    if len(pressed) < 2:
        return None
    return "+".join(name for name in _MODIFIER_ORDER if name in pressed)


def describe_combo(combo: str) -> str:
    """« ctrl+alt » -> « Ctrl + Alt »."""
    parts = [HOTKEY_MODIFIERS.get(part, part) for part in combo.split("+") if part]
    return " + ".join(parts) or combo

INJECT_METHODS = [
    ("paste", "Presse-papier (rapide, recommandé)"),
    ("type", "Frappe caractère par caractère"),
]

HOTKEY_MODES = [
    ("toggle", "Basculer (appui 1 = démarrer, appui 2 = arrêter)"),
    ("push_to_talk", "Maintenir pour parler"),
]


class _KeyTester(QThread):
    """Vérifie la clé API hors du thread d'interface."""

    tested = Signal(bool, str)

    def __init__(self, provider: str, api_key: str, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._api_key = api_key

    def run(self) -> None:
        try:
            info = Client(self._provider, self._api_key).check_key()
        except Exception as exc:
            self.tested.emit(False, str(exc))
            return
        if "models" in info:
            self.tested.emit(True, f"{info['models']} modèles accessibles")
            return
        usage = info.get("usage") or 0
        limit = info.get("limit")
        label = info.get("label") or "cle valide"
        detail = f"{label} · usage {usage:.3f} $"
        if limit:
            detail += f" / {limit:.2f} $"
        self.tested.emit(True, detail)


class _UpdateInspector(QThread):
    """Interroge le manifeste de mise a jour hors du thread d'interface."""

    checked = Signal(object, str)

    def __init__(self, url: str, current: str, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._current = current

    def run(self) -> None:
        from ..updates import check

        info, reason = check(self._url, self._current)
        self.checked.emit(info, reason)


class _Downloader(QThread):
    """Telecharge la mise a jour hors du thread d'interface."""

    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, url: str, destination: Path, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._destination = destination

    def run(self) -> None:
        from ..updates import download

        try:
            path = download(self._url, self._destination, on_progress=self.progress.emit)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(path)


class SettingsWindow(QDialog):
    """Boîte de dialogue de configuration."""

    open_recordings_requested = Signal()
    dashboard_requested = Signal()

    def __init__(self, settings: Settings, catalogue: Catalogue, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Réglages — Vox")
        self.setMinimumSize(680, 620)
        self._settings = settings
        self._catalogue = catalogue
        self._tester: _KeyTester | None = None
        self._update_inspector: _UpdateInspector | None = None
        self._update_info = None
        self._downloader: _Downloader | None = None
        self._download_path: Path | None = None
        self._keys: dict[str, str] = {}
        self._active_provider: str = settings.provider
        self._capturing = False
        self._captured = False
        self.custom_hotkey = ""

        self._build()
        self._load(settings)
        self._wire()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Réglages")
        title.setObjectName("sectionTitle")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        self.dashboard_button = QPushButton("Tableau de bord")
        self.dashboard_button.setToolTip(
            "Temps gagné, modèles, dépenses, statistiques d'usage"
        )
        self.dashboard_button.clicked.connect(self.dashboard_requested.emit)
        header.addWidget(self.dashboard_button)
        self.history_button = QPushButton("Historique")
        self.history_button.setToolTip("Réécouter les dictées conservées, copier leur texte")
        self.history_button.clicked.connect(self.open_recordings_requested.emit)
        header.addWidget(self.history_button)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._scrollable(self._build_general()), "Général")
        self.tabs.addTab(self._scrollable(self._build_audio()), "Audio")
        self.tabs.addTab(self._scrollable(self._build_output()), "Sortie")
        self.tabs.addTab(self._scrollable(self._build_reword()), "Reformulation")
        self._updates_tab_index = self.tabs.addTab(
            self._scrollable(self._build_updates()), "Mises à jour"
        )
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox()
        self.save_button = QPushButton("Enregistrer")
        self.save_button.setObjectName("primary")
        cancel_button = QPushButton("Annuler")
        buttons.addButton(self.save_button, QDialogButtonBox.AcceptRole)
        buttons.addButton(cancel_button, QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    @staticmethod
    def _scrollable(page: QWidget) -> QScrollArea:
        """Rend un onglet defilable : le contenu depasse sur les petits ecrans."""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setWidget(page)
        return area

    def _build_general(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 12, 4, 4)

        # --- API ---
        api_box = QGroupBox("Fournisseur et clé API")
        api_form = QFormLayout(api_box)

        self.provider_combo = NoWheelComboBox()
        for name, provider in PROVIDERS.items():
            self.provider_combo.addItem(provider.label, name)
        api_form.addRow("Fournisseur", self.provider_combo)

        self.key_row_label = QLabel("Clé API")
        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-or-v1-…")
        self.key_edit.setMinimumWidth(240)
        key_row.addWidget(self.key_edit, 1)
        self.reveal_button = QPushButton("Afficher")
        self.reveal_button.setCheckable(True)
        self.reveal_button.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self.reveal_button)
        self.test_button = QPushButton("Tester")
        key_row.addWidget(self.test_button)
        api_form.addRow(self.key_row_label, key_row)

        self.key_link = QLabel("")
        self.key_link.setObjectName("hint")
        self.key_link.setOpenExternalLinks(True)
        api_form.addRow("", self.key_link)

        self.key_status = QLabel("")
        self.key_status.setObjectName("hint")
        api_form.addRow("", self.key_status)

        self.stt_combo = NoWheelComboBox()
        self.stt_combo.setMinimumWidth(260)
        api_form.addRow("Modèle de transcription", self.stt_combo)

        self.chat_combo = NoWheelComboBox()
        self.chat_combo.setMinimumWidth(260)
        api_form.addRow("Modèle de reformulation", self.chat_combo)

        self.language_combo = NoWheelComboBox()
        for code, name in LANGUAGES:
            self.language_combo.addItem(name, code)
        api_form.addRow("Langue", self.language_combo)

        self.vocabulary_edit = QPlainTextEdit()
        self.vocabulary_edit.setPlaceholderText(
            "Noms propres, jargon, sigles à reconnaître sans faute.\n"
            "Ex. : VLED, Firestore, Quentin, Snapdragon…"
        )
        self.vocabulary_edit.setFixedHeight(84)
        api_form.addRow("Vocabulaire", self.vocabulary_edit)

        hint = QLabel(
            "Utilisé de deux façons : comme amorce du décodeur pour les modèles Whisper "
            "(biais lexical), et comme glossaire de référence pour la reformulation, qui "
            "corrige alors les noms propres mal transcrits. Un terme par ligne, ou séparés "
            "par des virgules."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        outer.addWidget(api_box)
        outer.addWidget(hint)

        # --- Declenchement ---
        trigger_box = QGroupBox("Déclenchement")
        trigger_form = QFormLayout(trigger_box)

        self.hotkey_combo = NoWheelComboBox()
        for key, name in HOTKEY_CHOICES.items():
            self.hotkey_combo.addItem(name, key)
        self.hotkey_combo.currentIndexChanged.connect(self._sync_hotkey_state)

        hotkey_row = QHBoxLayout()
        hotkey_row.setSpacing(6)
        hotkey_row.addWidget(self.hotkey_combo, 1)
        self.capture_button = QPushButton("Enregistrer…")
        self.capture_button.setToolTip(
            "Clique puis appuie sur ta combinaison (au moins deux modificateurs "
            "parmi Ctrl, Maj, Alt, Windows). Échap pour annuler."
        )
        self.capture_button.clicked.connect(self._start_capture)
        hotkey_row.addWidget(self.capture_button)
        trigger_form.addRow("Raccourci", hotkey_row)

        self.hotkey_hint = QLabel("")
        self.hotkey_hint.setObjectName("hint")
        self.hotkey_hint.setWordWrap(True)
        trigger_form.addRow("", self.hotkey_hint)

        self.hotkey_mode_combo = NoWheelComboBox()
        for key, name in HOTKEY_MODES:
            self.hotkey_mode_combo.addItem(name, key)
        trigger_form.addRow("Comportement", self.hotkey_mode_combo)

        self.enter_check = QCheckBox("Ajouter un « Entrée » après un double appui")
        self.enter_check.setToolTip(
            "Uniquement en mode bascule : un second appui sur le raccourci termine "
            "la dictée et valide la ligne. Sans effet en push-to-talk."
        )
        trigger_form.addRow("", self.enter_check)

        self.autostart_check = QCheckBox("Lancer Vox au démarrage de la session")
        trigger_form.addRow("", self.autostart_check)

        outer.addWidget(trigger_box)

        # --- Statistiques ---
        stats_box = QGroupBox("Statistiques")
        stats_form = QFormLayout(stats_box)
        self.typing_spin = NoWheelSpinBox()
        self.typing_spin.setRange(10, 200)
        self.typing_spin.setSuffix(" mots/min")
        self.typing_spin.setToolTip(
            "Vitesse de frappe de référence, utilisée pour estimer le « temps "
            "gagné » dans le tableau de bord. 40 est la moyenne ; un bon dactylo "
            "tape 60 à 80 mots/minute."
        )
        stats_form.addRow("Vitesse de frappe", self.typing_spin)
        outer.addWidget(stats_box)

        stats_hint = QLabel(
            "Le « temps gagné » compare le temps qu'il t'aurait fallu pour taper "
            "le même texte à cette vitesse au temps réellement passé à dicter. "
            "Ajuste-la à ta frappe pour une estimation réaliste."
        )
        stats_hint.setObjectName("hint")
        stats_hint.setWordWrap(True)
        outer.addWidget(stats_hint)

        outer.addStretch(1)
        return page

    # ------------------------------------------------------------------
    def _build_audio(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 12, 4, 4)

        device_box = QGroupBox("Microphone")
        device_form = QFormLayout(device_box)

        self.device_combo = NoWheelComboBox()
        self.device_combo.setMinimumWidth(260)
        self.device_combo.addItem("Périphérique d'entrée par défaut", None)
        for device in list_input_devices():
            self.device_combo.addItem(
                f"{device['name']}  ·  {device['hostapi']}", device["index"]
            )
        device_form.addRow("Entrée", self.device_combo)

        self.reload_button = QPushButton("Rafraîchir la liste")
        device_form.addRow("", self.reload_button)

        outer.addWidget(device_box)

        limits_box = QGroupBox("Limites")
        limits_form = QFormLayout(limits_box)

        self.max_seconds_spin = NoWheelSpinBox()
        self.max_seconds_spin.setRange(10, 3600)
        self.max_seconds_spin.setSuffix(" s")
        limits_form.addRow("Durée maximale d'un enregistrement", self.max_seconds_spin)

        limit_hint = QLabel(
            "Au-delà de deux minutes, l'API peut dépasser le délai amont de 60 s et "
            "échouer. Pour de longues prises de parole, enregistre en plusieurs fois."
        )
        limit_hint.setObjectName("hint")
        limit_hint.setWordWrap(True)

        self.min_seconds_spin = NoWheelDoubleSpinBox()
        self.min_seconds_spin.setRange(0.0, 5.0)
        self.min_seconds_spin.setSingleStep(0.1)
        self.min_seconds_spin.setDecimals(1)
        self.min_seconds_spin.setSuffix(" s")
        limits_form.addRow("Durée minimale (en dessous : abandon)", self.min_seconds_spin)

        outer.addWidget(limits_box)
        outer.addWidget(limit_hint)

        ui_box = QGroupBox("Interface")
        ui_form = QFormLayout(ui_box)

        self.theme_combo = NoWheelComboBox()
        self.theme_combo.addItem("Sombre", "dark")
        self.theme_combo.addItem("Clair", "light")
        ui_form.addRow("Thème", self.theme_combo)

        self.hide_delay_spin = NoWheelSpinBox()
        self.hide_delay_spin.setRange(0, 120)
        self.hide_delay_spin.setSuffix(" s")
        self.hide_delay_spin.setSpecialValueText("jamais")
        self.hide_delay_spin.setToolTip(
            "Délai avant que la pilule se masque toute seule. 0 = elle reste "
            "affichée jusqu'à ce que tu la fermes avec le ✕.\n\n"
            "Sans effet tant que « Disparaître dès la fin de l'écoute » est cochée : "
            "la pilule ne survit alors pas à l'enregistrement."
        )
        ui_form.addRow("Masquer la pilule après", self.hide_delay_spin)

        self.hide_after_check = QCheckBox("Disparaître dès la fin de l'écoute")
        self.hide_after_check.setToolTip(
            "La pilule ne s'affiche que pendant l'enregistrement, puis s'efface "
            "toute seule. Les erreurs restent visibles quelques secondes."
        )
        self.hide_after_check.toggled.connect(self._sync_hide_delay_state)
        ui_form.addRow("", self.hide_after_check)

        self.sounds_check = QCheckBox("Signaux sonores")
        ui_form.addRow("", self.sounds_check)

        self.overlay_result_check = QCheckBox("Afficher la pilule après chaque insertion")
        ui_form.addRow("", self.overlay_result_check)

        self.history_check = QCheckBox("Conserver l'historique local des dictées")
        ui_form.addRow("", self.history_check)

        self.notify_check = QCheckBox("Afficher une notification au démarrage")
        ui_form.addRow("", self.notify_check)

        self.taskbar_check = QCheckBox("Garder une icône dans la barre des tâches")
        self.taskbar_check.setToolTip(
            "La pilule reste visible en permanence et obtient un bouton dans la "
            "barre des tâches, comme une application classique."
        )
        ui_form.addRow("", self.taskbar_check)

        outer.addWidget(ui_box)

        rec_box = QGroupBox("Enregistrements")
        rec_form = QFormLayout(rec_box)
        rec_form.setContentsMargins(14, 16, 14, 12)
        rec_form.setSpacing(9)

        self.save_recordings_check = QCheckBox("Conserver l'audio de chaque dictée")
        self.save_recordings_check.setToolTip(
            "Chaque dictée est écrite en WAV dans le dossier de données avant "
            "l'appel à l'API. Elles sont réécoutables depuis « Enregistrements… »."
        )
        self.save_recordings_check.toggled.connect(self._sync_recording_state)
        rec_form.addRow("", self.save_recordings_check)

        self.retention_spin = NoWheelSpinBox()
        self.retention_spin.setRange(0, 3650)
        self.retention_spin.setSuffix(" jours")
        self.retention_spin.setSpecialValueText("illimité")
        self.retention_spin.setToolTip(
            "Les enregistrements plus vieux sont effacés au démarrage. "
            "0 = on garde tout."
        )
        rec_form.addRow("Conservation", self.retention_spin)

        rec_actions = QHBoxLayout()
        rec_actions.setSpacing(6)
        self.open_recordings_button = QPushButton("Ouvrir les enregistrements")
        self.open_recordings_button.clicked.connect(self.open_recordings_requested.emit)
        rec_actions.addWidget(self.open_recordings_button)
        rec_actions.addStretch(1)
        self.records_size_label = QLabel("")
        self.records_size_label.setObjectName("hint")
        rec_actions.addWidget(self.records_size_label)
        rec_form.addRow("", rec_actions)

        outer.addWidget(rec_box)

        if sys.platform == "win32":
            tray_text = (
                "Vox n'a pas de fenêtre principale : il vit dans la zone de notification "
                "(derrière le chevron ^ près de l'horloge). Windows y range les nouvelles "
                "icônes par défaut ; le menu « Où est mon icône ? » explique comment "
                "l'épingler à côté de l'horloge. Si tu préfères une présence permanente "
                "dans la barre des tâches, coche l'option ci-dessus."
            )
        else:
            tray_text = (
                "Vox n'a pas de fenêtre principale : il vit dans la barre système, près "
                "de l'horloge. Sur GNOME, l'icône n'apparaît qu'avec l'extension "
                "« AppIndicator » : installe-la puis relance Vox."
            )
        tray_hint = QLabel(tray_text)
        tray_hint.setObjectName("hint")
        tray_hint.setWordWrap(True)
        outer.addWidget(tray_hint)
        outer.addStretch(1)
        return page

    # ------------------------------------------------------------------
    def _build_output(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 12, 4, 4)

        box = QGroupBox("Insertion du texte")
        form = QFormLayout(box)

        self.method_combo = NoWheelComboBox()
        for key, name in INJECT_METHODS:
            self.method_combo.addItem(name, key)
        form.addRow("Méthode", self.method_combo)

        self.paste_combo = NoWheelComboBox()
        self.paste_combo.setEditable(True)
        self.paste_combo.addItems(PASTE_KEYS)
        form.addRow("Raccourci de collage", self.paste_combo)

        note = QLabel(
            "Le collage via presse-papier est le plus rapide et le plus fiable, mais "
            "il écrase temporairement le presse-papier (restauré juste après). "
            "La frappe caractère par caractère respecte totalement le presse-papier "
            "et fonctionne en AZERTY comme en QWERTY."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)

        self.space_check = QCheckBox("Ajouter une espace entre deux dictées rapprochées")
        form.addRow("", self.space_check)

        self.short_period_check = QCheckBox(
            "Retirer le point final des transcriptions de 5 mots ou moins"
        )
        form.addRow("", self.short_period_check)

        outer.addWidget(box)
        outer.addWidget(note)

        if sys.platform == "win32":
            warn_text = (
                "Limite connue : dans une fenêtre élevée (UAC / administrateur), Windows "
                "refuse l'insertion. Vox affiche alors une erreur et le texte reste "
                "disponible via « Réinsérer le dernier texte »."
            )
        else:
            warn_text = (
                "Sous Wayland, la simulation du collage est restreinte par le "
                "gestionnaire de fenêtres : si le collage échoue, choisis la méthode "
                "« Frappe » ci-dessus, qui fonctionne partout."
            )
        warn = QLabel(warn_text)
        warn.setObjectName("hint")
        warn.setWordWrap(True)
        outer.addWidget(warn)
        outer.addStretch(1)
        return page

    # ------------------------------------------------------------------
    def _build_reword(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 12, 4, 4)

        box = QGroupBox("Reformulation (seconde passe LLM)")
        form = QFormLayout(box)

        self.reword_check = QCheckBox("Reformuler automatiquement avant l'insertion")
        form.addRow("", self.reword_check)

        self.tone_combo = NoWheelComboBox()
        for tone_id, tone in TONES.items():
            self.tone_combo.addItem(f"{tone['label']} — {tone['hint']}", tone_id)
        form.addRow("Ton par défaut", self.tone_combo)

        self.custom_prompt_edit = QPlainTextEdit()
        self.custom_prompt_edit.setPlaceholderText(
            "Instruction personnalisée, utilisée quand le ton « Personnalisé » est choisi."
        )
        self.custom_prompt_edit.setFixedHeight(110)
        form.addRow("Prompt personnalisé", self.custom_prompt_edit)

        note = QLabel(
            "La reformulation envoie la transcription à un modèle de chat (coût "
            "marginal négligeable). Désactive-la si tu veux le texte brut, sans "
            "aucune modification."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)

        outer.addWidget(box)
        outer.addWidget(note)
        outer.addStretch(1)
        return page

    def _build_updates(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 12, 4, 4)

        box = QGroupBox("Mises à jour")
        form = QFormLayout(box)

        self.version_label = QLabel(f"Vox {__version__}")
        form.addRow("Version installée", self.version_label)

        self.update_check = QCheckBox("Vérifier les mises à jour au démarrage")
        form.addRow("", self.update_check)

        self.manifest_edit = QLineEdit()
        self.manifest_edit.setPlaceholderText(
            "https://github.com/quentinvled/vox/releases/latest/download/version.json"
        )
        form.addRow("URL du manifeste", self.manifest_edit)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.update_now_button = QPushButton("Vérifier maintenant")
        self.update_now_button.clicked.connect(self._check_updates_now)
        actions.addWidget(self.update_now_button)
        self.download_button = QPushButton("Télécharger la mise à jour")
        self.download_button.setObjectName("primary")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self._on_download_clicked)
        actions.addWidget(self.download_button)
        actions.addStretch(1)
        form.addRow("", actions)

        self.download_bar = QProgressBar()
        self.download_bar.setRange(0, 100)
        self.download_bar.setValue(0)
        self.download_bar.setVisible(False)
        form.addRow("", self.download_bar)

        self.update_status = QLabel("")
        self.update_status.setObjectName("hint")
        self.update_status.setWordWrap(True)
        self.update_status.setText(
            "Clique sur « Vérifier maintenant » pour comparer avec la dernière version publiée."
        )
        form.addRow("", self.update_status)

        outer.addWidget(box)

        hint = QLabel(
            "Vox lit un petit fichier JSON publié par l'auteur et compare son "
            "numéro de version au sien. S'il est plus récent, Vox peut "
            "télécharger le fichier d'installation (avec sa progression) et "
            "te proposer de le lancer. Rien ne s'exécute sans ton accord."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        outer.addStretch(1)
        return page

    # ------------------------------------------------------------------
    def _wire(self) -> None:
        self.test_button.clicked.connect(self._test_key)
        self.reload_button.clicked.connect(self._reload_devices)
        self.reword_check.toggled.connect(self._sync_reword_state)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self._sync_hotkey_state()
        self._sync_hide_delay_state()
        self._sync_recording_state()

    def _on_provider_changed(self, index: int) -> None:
        provider = self.provider_combo.itemData(index)
        if not provider or provider == self._active_provider:
            return
        self._keys[self._active_provider] = self.key_edit.text().strip()
        self._apply_provider(provider)
        self.key_status.setText("")

    def _apply_provider(self, provider: str) -> None:
        info = PROVIDERS[provider]
        self._active_provider = provider
        short = info.label.split(" (")[0]
        self.key_row_label.setText(f"Clé {short}")
        self.key_edit.setText(self._keys.get(provider, ""))
        self.key_link.setText(
            f'<a href="{info.console_url}" style="color:inherit;text-decoration:none">'
            f"Obtenir une cle → {info.console_url}</a>"
        )

    def _sync_hide_delay_state(self) -> None:
        # Le delai ne sert que si la pilule survit a la fin de l'ecoute.
        self.hide_delay_spin.setEnabled(not self.hide_after_check.isChecked())

    def _sync_recording_state(self) -> None:
        on = self.save_recordings_check.isChecked()
        self.retention_spin.setEnabled(on)
        self.open_recordings_button.setEnabled(True)
        self._refresh_records_size()

    def _refresh_records_size(self) -> None:
        info = recordings.stats()
        if not info["count"]:
            self.records_size_label.setText("aucun enregistrement")
            return
        self.records_size_label.setText(
            f"{info['count']} enregistrement(s) · {info['size']}"
        )

    def _sync_hotkey_state(self) -> None:
        custom = self.hotkey_combo.currentData() == "custom"
        self.capture_button.setEnabled(custom)
        if custom:
            self.capture_button.setText(
                f"{describe_combo(self.custom_hotkey)} — modifier"
                if self.custom_hotkey
                else "Enregistrer…"
            )
            self.hotkey_hint.setText(
                "Clique sur « Enregistrer », puis appuie sur ta combinaison "
                "(au moins deux modificateurs). Échap pour annuler."
            )
        else:
            self.capture_button.setText("Enregistrer…")
            self.hotkey_hint.setText(
                "Le raccourci fonctionne en maintenant la combinaison : maintenir "
                "pour parler, relâcher pour insérer."
            )

    def _start_capture(self) -> None:
        self._capturing = True
        self._captured = False
        self.capture_button.setEnabled(False)
        self.capture_button.setText("Appuie maintenant…")
        self.setFocus()

    def _stop_capture(self) -> None:
        self._capturing = False
        self.capture_button.setEnabled(True)
        self._sync_hotkey_state()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key_Escape:
            self._stop_capture()
            event.accept()
            return
        combo = combo_from_event(event)
        if combo:
            self.custom_hotkey = combo
            self._captured = True
            self._stop_capture()
        event.accept()

    def _toggle_key_visibility(self, visible: bool) -> None:
        self.key_edit.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        self.reveal_button.setText("Masquer" if visible else "Afficher")

    def _sync_reword_state(self) -> None:
        enabled = self.reword_check.isChecked()
        self.tone_combo.setEnabled(enabled)
        self.custom_prompt_edit.setEnabled(enabled)

    def _reload_devices(self) -> None:
        current = self.device_combo.currentData()
        self.device_combo.clear()
        self.device_combo.addItem("Peripherique d'entree par defaut", None)
        for device in list_input_devices():
            self.device_combo.addItem(f"{device['name']}  ·  {device['hostapi']}", device["index"])
        index = self.device_combo.findData(current)
        self.device_combo.setCurrentIndex(max(0, index))

    def _test_key(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.key_status.setObjectName("error")
            self.key_status.setText("Saisis une clé avant de tester.")
            self._restyle(self.key_status)
            return
        self.test_button.setEnabled(False)
        self.key_status.setObjectName("hint")
        self.key_status.setText("Vérification…")
        self._restyle(self.key_status)
        self._tester = _KeyTester(self._active_provider, key, self)
        self._tester.tested.connect(self._on_key_tested)
        self._tester.start()

    def _on_key_tested(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self.key_status.setObjectName("success" if ok else "error")
        self.key_status.setText(("Clé valide — " + message) if ok else ("Échec : " + message))
        self._restyle(self.key_status)

    # ------------------------------------------------------------------
    # Mises a jour
    # ------------------------------------------------------------------
    def _set_update_status(self, text: str, level: str = "hint") -> None:
        self.update_status.setObjectName(level)
        self.update_status.setText(text)
        self._restyle(self.update_status)

    def _check_updates_now(self) -> None:
        url = self.manifest_edit.text().strip()
        if not url:
            self._set_update_status("Renseigne d'abord l'URL du manifeste.", "error")
            return
        self.update_now_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self._set_update_status("Vérification en cours…")
        self._update_inspector = _UpdateInspector(url, __version__, self)
        self._update_inspector.checked.connect(self._on_update_checked)
        self._update_inspector.start()

    def _reset_download_state(self) -> None:
        self._download_path = None
        self.download_bar.setVisible(False)
        self.download_bar.setRange(0, 100)
        self.download_bar.setValue(0)
        self.download_bar.setFormat("%p %")
        self.download_button.setText("Télécharger la mise à jour")

    def show_updates_tab(self) -> None:
        """Affiche l'onglet « Mises à jour »."""
        self.tabs.setCurrentIndex(self._updates_tab_index)

    def present_update(self, info) -> None:
        """Affiche une mise a jour deja connue (ouverte depuis le menu)."""
        self._update_info = info
        self._on_update_checked(info, "")
        self.show_updates_tab()

    def _on_update_checked(self, info, reason: str) -> None:
        self.update_now_button.setEnabled(True)
        self._update_info = info
        self._reset_download_state()
        if info is None:
            self.download_button.setEnabled(False)
            if reason.startswith("à jour"):
                self._set_update_status(f"Vox {__version__} est à jour.", "success")
            else:
                self._set_update_status(f"Vérification impossible : {reason}", "error")
            return
        notes = (info.notes or "").strip()
        message = f"Mise à jour disponible : Vox {info.version}."
        if info.published_at:
            message += f" (publiée le {info.published_at})"
        if notes:
            message += f"\n{notes}"
        self._set_update_status(message, "success")
        self.download_button.setEnabled(bool(info.url))

    # ------------------------------------------------------------------
    # Telechargement de la mise a jour
    # ------------------------------------------------------------------
    def _on_download_clicked(self) -> None:
        if self._download_path is not None:
            self._launch_download()
            return
        info = self._update_info
        url = (info.url if info else "") or self.manifest_edit.text().strip()
        if not url:
            self._set_update_status("Aucune adresse de téléchargement.", "error")
            return
        self._start_download(url)

    def _start_download(self, url: str) -> None:
        from ..paths import downloads_dir
        from ..updates import suggested_filename

        destination = downloads_dir() / suggested_filename(url)
        self._download_path = None
        self.download_button.setEnabled(False)
        self.download_button.setText("Téléchargement…")
        self.download_bar.setRange(0, 100)
        self.download_bar.setValue(0)
        self.download_bar.setVisible(True)
        self._set_update_status(f"Téléchargement vers {destination}")
        self._downloader = _Downloader(url, destination, self)
        self._downloader.progress.connect(self._on_download_progress)
        self._downloader.done.connect(self._on_download_done)
        self._downloader.failed.connect(self._on_download_failed)
        self._downloader.start()

    def _on_download_progress(self, received: int, total: int) -> None:
        if total:
            self.download_bar.setRange(0, 100)
            self.download_bar.setValue(int(received * 100 / total))
            self.download_bar.setFormat(
                f"{received / 1048576:.1f} / {total / 1048576:.1f} Mo (%p %)"
            )
            self._set_update_status(
                f"Téléchargement… {received / 1048576:.1f} / {total / 1048576:.1f} Mo"
            )
        else:
            self.download_bar.setRange(0, 0)
            self._set_update_status(f"Téléchargement… {received / 1048576:.1f} Mo")

    def _on_download_done(self, path) -> None:
        self._download_path = Path(path)
        self.download_bar.setRange(0, 100)
        self.download_bar.setValue(100)
        self.download_bar.setFormat("Téléchargement terminé")
        self.download_button.setEnabled(True)
        self.download_button.setText("Installer la mise à jour")
        self._set_update_status(f"Téléchargement terminé : {self._download_path}", "success")

    def _on_download_failed(self, message: str) -> None:
        self._download_path = None
        self.download_bar.setVisible(False)
        self.download_button.setEnabled(True)
        self.download_button.setText("Réessayer le téléchargement")
        self._set_update_status(f"Téléchargement impossible : {message}", "error")

    def _launch_download(self) -> None:
        path = self._download_path
        if path is None or not path.exists():
            return
        if sys.platform == "win32":
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            return
        # Linux : rendre l'AppImage executable puis la lancer.
        import subprocess

        with contextlib.suppress(OSError):
            path.chmod(path.stat().st_mode | 0o111)
        try:
            subprocess.Popen([str(path)])  # noqa: S603 - fichier choisi par l'utilisateur
        except OSError as exc:
            self._set_update_status(f"Lancement impossible : {exc}", "error")

    @staticmethod
    def _restyle(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # ------------------------------------------------------------------
    def _load(self, settings: Settings) -> None:
        self._keys = {
            "openrouter": settings.api_key,
            "groq": settings.groq_api_key,
            "openai": settings.openai_api_key,
        }
        self._select_data(self.provider_combo, settings.provider)
        self._apply_provider(settings.provider)
        self._fill(self.stt_combo, self._catalogue.stt, settings.stt_model)
        self._fill(self.chat_combo, self._catalogue.chat, settings.chat_model)
        self._select_data(self.language_combo, settings.language)
        self.vocabulary_edit.setPlainText(settings.vocabulary_prompt)

        self._select_data(self.hotkey_mode_combo, settings.hotkey_mode)
        # Une combinaison personnalisee n'est pas dans la liste deroulante :
        # on selectionne « Personnalisé » et on garde la valeur a part.
        if settings.hotkey and settings.hotkey not in HOTKEY_CHOICES:
            self._select_data(self.hotkey_combo, "custom")
            self.custom_hotkey = settings.hotkey
        else:
            self._select_data(self.hotkey_combo, settings.hotkey or "ctrl+shift")
            self.custom_hotkey = settings.hotkey or "ctrl+shift"
        self._sync_hotkey_state()
        self.enter_check.setChecked(settings.double_tap_enter)
        self.autostart_check.setChecked(settings.autostart)
        self.update_check.setChecked(settings.check_updates)
        self.manifest_edit.setText(settings.update_manifest_url)

        index = self.device_combo.findData(settings.input_device)
        self.device_combo.setCurrentIndex(max(0, index))
        self.max_seconds_spin.setValue(settings.max_record_seconds)
        self.min_seconds_spin.setValue(settings.min_record_seconds)
        self._select_data(self.theme_combo, settings.theme)
        self.hide_delay_spin.setValue(settings.overlay_hide_delay)
        self.hide_after_check.setChecked(settings.hide_after_listening)
        self.sounds_check.setChecked(settings.sounds)
        self.save_recordings_check.setChecked(settings.save_recordings)
        self.retention_spin.setValue(settings.recording_retention_days)
        self._sync_hide_delay_state()
        self._sync_recording_state()
        self.overlay_result_check.setChecked(settings.show_overlay_on_result)
        self.history_check.setChecked(settings.history_enabled)
        self.notify_check.setChecked(settings.notify_on_start)
        self.taskbar_check.setChecked(settings.show_in_taskbar)
        self.typing_spin.setValue(int(settings.typing_wpm))

        self._select_data(self.method_combo, settings.inject_method)
        if self.paste_combo.findText(settings.paste_keys) < 0:
            self.paste_combo.addItem(settings.paste_keys)
        self.paste_combo.setCurrentText(settings.paste_keys)
        self.space_check.setChecked(settings.add_space)
        self.short_period_check.setChecked(settings.strip_short_period)

        self.reword_check.setChecked(settings.reword_enabled)
        self._select_data(self.tone_combo, settings.reword_tone)
        self.custom_prompt_edit.setPlainText(settings.reword_custom_prompt)
        self._sync_reword_state()

    def _selected_hotkey(self) -> str:
        """Combinaison retenue, en tenant compte du mode personnalise."""
        data = self.hotkey_combo.currentData()
        if data == "custom":
            return self.custom_hotkey or "ctrl+shift"
        return data or "ctrl+shift"

    @staticmethod
    def _fill(combo: QComboBox, items: list[dict], current: str) -> None:
        combo.clear()
        known: set[str] = set()
        for item in items:
            model_id = item.get("id") or ""
            if not model_id or model_id in known:
                continue
            known.add(model_id)
            combo.addItem(model_label(item), model_id)
        if current and current not in known:
            combo.addItem(current, current)
        index = combo.findData(current)
        combo.setCurrentIndex(max(0, index))

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    # ------------------------------------------------------------------
    def values(self) -> Settings:
        """Renvoie une copie des reglages mise a jour."""
        self._keys[self._active_provider] = self.key_edit.text().strip()
        return dataclasses.replace(
            self._settings,
            provider=self.provider_combo.currentData() or "openrouter",
            api_key=self._keys.get("openrouter", ""),
            groq_api_key=self._keys.get("groq", ""),
            openai_api_key=self._keys.get("openai", ""),
            stt_model=self.stt_combo.currentData() or self._settings.stt_model,
            chat_model=self.chat_combo.currentData() or self._settings.chat_model,
            language=self.language_combo.currentData() or "",
            vocabulary_prompt=self.vocabulary_edit.toPlainText().strip(),
            hotkey=self._selected_hotkey(),
            hotkey_mode=self.hotkey_mode_combo.currentData(),
            double_tap_enter=self.enter_check.isChecked(),
            autostart=self.autostart_check.isChecked(),
            check_updates=self.update_check.isChecked(),
            update_manifest_url=self.manifest_edit.text().strip(),
            input_device=self.device_combo.currentData(),
            max_record_seconds=self.max_seconds_spin.value(),
            min_record_seconds=self.min_seconds_spin.value(),
            theme=self.theme_combo.currentData(),
            overlay_hide_delay=self.hide_delay_spin.value(),
            hide_after_listening=self.hide_after_check.isChecked(),
            save_recordings=self.save_recordings_check.isChecked(),
            recording_retention_days=self.retention_spin.value(),
            sounds=self.sounds_check.isChecked(),
            show_overlay_on_result=self.overlay_result_check.isChecked(),
            history_enabled=self.history_check.isChecked(),
            notify_on_start=self.notify_check.isChecked(),
            show_in_taskbar=self.taskbar_check.isChecked(),
            inject_method=self.method_combo.currentData(),
            paste_keys=self.paste_combo.currentText().strip() or "ctrl+v",
            add_space=self.space_check.isChecked(),
            strip_short_period=self.short_period_check.isChecked(),
            reword_enabled=self.reword_check.isChecked(),
            reword_tone=self.tone_combo.currentData(),
            reword_custom_prompt=self.custom_custom_text(),
            typing_wpm=float(self.typing_spin.value()),
        )

    def custom_custom_text(self) -> str:
        return self.custom_prompt_edit.toPlainText().strip()
