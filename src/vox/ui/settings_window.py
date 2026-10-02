"""Fenêtre de réglages."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, audiofiles, library, recordings
from ..api import PROVIDERS, Client, diarizes_by_default
from ..config import DEFAULT_DIARIZATION_MODEL, HOTKEY_CHOICES, HOTKEY_MODIFIERS, Settings
from ..models import Catalogue
from ..models import label as model_label
from ..recorder import list_input_devices
from ..reword import TONES
from ..stats import format_duration, format_money
from .wheel import NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox

# Fichiers audio acceptés à l'import (mêmes formats que la bibliothèque).
AUDIO_FILTER = (
    "Audio (*.mp3 *.m4a *.wav *.ogg *.flac *.aac *.opus *.wma *.mp4 *.mkv *.webm);;"
    "Tous les fichiers (*)"
)

IMPORT_HINT = (
    "L'import tourne en arrière-plan : tu peux réduire cette fenêtre ou passer "
    "à un autre onglet, il continue. Chaque fichier rejoint la bibliothèque dès "
    "qu'il est prêt."
)


def _file_size(path: str) -> str:
    try:
        size = float(Path(path).stat().st_size)
    except OSError:
        return "?"
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            return f"{size:.0f} {unit}" if unit == "o" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} Go"

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


class _FileDropList(QListWidget):
    """Liste de fichiers qui accepte le glisser-déposer depuis l'explorateur."""

    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setAlternatingRowColors(False)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class SettingsWindow(QDialog):
    """Boîte de dialogue de configuration."""

    open_recordings_requested = Signal()
    dashboard_requested = Signal()
    check_requested = Signal(str)
    update_requested = Signal()
    import_requested = Signal(list, dict)
    import_cancel_requested = Signal()

    def __init__(self, settings: Settings, catalogue: Catalogue, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Réglages — Vox {__version__}")
        self.setMinimumSize(680, 620)
        self._settings = settings
        self._catalogue = catalogue
        self._tester: _KeyTester | None = None
        self._update_info = None
        self._keys: dict[str, str] = {}
        self._active_provider: str = settings.provider
        self._capturing = False
        self._captured = False
        self._import_running = False
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
        self.tabs.addTab(self._scrollable(self._build_import()), "Importer")
        self.tabs.addTab(self._scrollable(self._build_audio()), "Audio")
        self.tabs.addTab(self._scrollable(self._build_output()), "Sortie")
        self.tabs.addTab(self._scrollable(self._build_reword()), "Reformulation")
        self.tabs.addTab(self._scrollable(self._build_processing()), "Traitement")
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
        self.reword_check.setToolTip(
            "Une seconde passe LLM rapide (0,5 à 2 s, coût négligeable) nettoie "
            "les hésitations, les répétitions et la ponctuation avant "
            "l'insertion. Si elle échoue, le texte brut est inséré normalement. "
            "Le bouton « Reformuler » de la pilule permet de la couper d'un "
            "clic pour une note rapide."
        )
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
            "marginal négligeable). Elle est active par défaut, avec le ton "
            "« Nettoyer » ; le texte brut reste disponible dans l'historique "
            "si tu veux comparer."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)

        outer.addWidget(box)
        outer.addWidget(note)
        outer.addStretch(1)
        return page

    # ------------------------------------------------------------------
    # Importer : l'assistant d'import (fichiers, options, progression)
    # ------------------------------------------------------------------
    def _build_import(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 12, 4, 4)

        # --- fichiers a importer ---
        files_box = QGroupBox("Fichiers à importer")
        files_layout = QVBoxLayout(files_box)
        files_layout.setSpacing(8)

        self.import_files_list = _FileDropList()
        self.import_files_list.setMinimumHeight(110)
        self.import_files_list.setToolTip(
            "Glisse-dépose des fichiers audio (appel, réunion, vocal WhatsApp) "
            "ici, ou utilise « Ajouter des fichiers… »."
        )
        self.import_files_list.files_dropped.connect(self.add_import_files)
        files_layout.addWidget(self.import_files_list)

        files_actions = QHBoxLayout()
        files_actions.setSpacing(6)

        self.import_add_button = QPushButton("Ajouter des fichiers…")
        self.import_add_button.clicked.connect(self._choose_import_files)
        files_actions.addWidget(self.import_add_button)

        self.import_remove_button = QPushButton("Retirer")
        self.import_remove_button.setToolTip("Retirer les fichiers sélectionnés de la liste")
        self.import_remove_button.clicked.connect(self._remove_selected_import_files)
        files_actions.addWidget(self.import_remove_button)

        self.import_clear_button = QPushButton("Vider")
        self.import_clear_button.setToolTip("Vider la liste des fichiers à importer")
        self.import_clear_button.clicked.connect(self._clear_import_files)
        files_actions.addWidget(self.import_clear_button)

        files_actions.addStretch(1)
        self.import_estimate_label = QLabel("")
        self.import_estimate_label.setObjectName("hint")
        files_actions.addWidget(self.import_estimate_label)
        files_layout.addLayout(files_actions)
        outer.addWidget(files_box)

        # --- options ---
        options_box = QGroupBox("Options avant de lancer")
        options_form = QFormLayout(options_box)

        self.import_model_combo = NoWheelComboBox()
        self.import_model_combo.setMinimumWidth(280)
        self.import_model_combo.addItem("Automatique (recommandé par Vox)", "")
        for item in self._catalogue.stt:
            model_id = item.get("id") or ""
            if model_id:
                self.import_model_combo.addItem(model_label(item), model_id)
        self.import_model_combo.setToolTip(
            "Modèle qui transcrit le fichier importé. « Automatique » utilise le "
            "modèle recommandé (MAI Transcribe 2), qui sépare les locuteurs."
        )
        self.import_model_combo.currentIndexChanged.connect(self._sync_import_options)
        options_form.addRow("Modèle", self.import_model_combo)

        self.import_diarize_check = QCheckBox("Identifier les locuteurs (diarisation)")
        self.import_diarize_check.setToolTip(
            "Décoche pour un texte brut, sans chercher qui parle (utile pour un "
            "vocal d'une seule personne ou une note vocale)."
        )
        self.import_diarize_check.toggled.connect(self._sync_import_options)
        options_form.addRow("", self.import_diarize_check)

        self.import_speakers_spin = NoWheelSpinBox()
        self.import_speakers_spin.setRange(0, 8)
        self.import_speakers_spin.setSpecialValueText("automatique")
        self.import_speakers_spin.setSuffix(" personne(s)")
        self.import_speakers_spin.setToolTip(
            "Indique combien de personnes parlent dans le fichier : Vox s'en sert "
            "pour raccorder proprement les locuteurs entre les tranches. "
            "« automatique » laisse Vox décider."
        )
        options_form.addRow("Personnes", self.import_speakers_spin)

        self.clean_imports_check = QCheckBox(
            "Nettoyer après l'import (LLM) : ponctuation, noms propres, « euh »"
        )
        self.clean_imports_check.setToolTip(
            "Une passe de correction est lancée après la transcription, avant de "
            "ranger le transcript. Coût indicatif : 0,05 à 0,10 $ par heure "
            "d'audio. Le transcript brut est conservé à côté du nettoyé."
        )
        options_form.addRow("", self.clean_imports_check)

        self.import_options_hint = QLabel("")
        self.import_options_hint.setObjectName("hint")
        self.import_options_hint.setWordWrap(True)
        options_form.addRow("", self.import_options_hint)

        outer.addWidget(options_box)

        # --- lancer et progression ---
        run_box = QGroupBox("Lancer")
        run_layout = QVBoxLayout(run_box)
        run_layout.setSpacing(8)

        self.import_start_button = QPushButton("Importer maintenant")
        self.import_start_button.setObjectName("primary")
        self.import_start_button.setEnabled(False)
        self.import_start_button.setToolTip(
            "La transcription démarre en arrière-plan : la fenêtre peut être "
            "réduite ou fermée, l'import continue et la bibliothèque se remplit."
        )
        self.import_start_button.clicked.connect(self._start_import)
        run_layout.addWidget(self.import_start_button)

        self.import_progress_frame = QFrame()
        self.import_progress_frame.setObjectName("panel")
        progress_layout = QHBoxLayout(self.import_progress_frame)
        progress_layout.setContentsMargins(12, 8, 12, 8)
        progress_layout.setSpacing(10)
        self.import_progress_label = QLabel("Préparation…")
        progress_layout.addWidget(self.import_progress_label, 1)
        self.import_progress_bar = QProgressBar()
        self.import_progress_bar.setTextVisible(False)
        self.import_progress_bar.setRange(0, 1)
        self.import_progress_bar.setFixedWidth(200)
        progress_layout.addWidget(self.import_progress_bar)
        self.import_cancel_button = QPushButton("Annuler")
        self.import_cancel_button.clicked.connect(self.import_cancel_requested.emit)
        progress_layout.addWidget(self.import_cancel_button)
        self.import_progress_frame.hide()
        run_layout.addWidget(self.import_progress_frame)

        self.import_results_list = QListWidget()
        self.import_results_list.setMaximumHeight(96)
        self.import_results_list.setToolTip(
            "Double-clique sur un import terminé pour l'ouvrir dans la bibliothèque"
        )
        self.import_results_list.itemDoubleClicked.connect(
            lambda _item: self.open_recordings_requested.emit()
        )
        run_layout.addWidget(self.import_results_list)

        self.import_status_label = QLabel(IMPORT_HINT)
        self.import_status_label.setObjectName("hint")
        self.import_status_label.setWordWrap(True)
        run_layout.addWidget(self.import_status_label)

        outer.addWidget(run_box)
        outer.addStretch(1)
        return page

    # ------------------------------------------------------------------
    # Importer : logique
    # ------------------------------------------------------------------
    def import_options(self) -> dict:
        """Options choisies dans l'onglet, pour le prochain import."""
        return {
            "model": self.import_model_combo.currentData() or "",
            "diarize": self.import_diarize_check.isChecked(),
            "speakers": int(self.import_speakers_spin.value()),
            "clean": self.clean_imports_check.isChecked(),
        }

    def _choose_import_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Importer des fichiers audio", str(Path.home()), AUDIO_FILTER
        )
        if paths:
            self.add_import_files(list(paths))

    def add_import_files(self, paths: list) -> None:
        """Ajoute des fichiers a la liste (bouton ou glisser-deposer)."""
        known = set(self._import_paths())
        for raw in paths:
            path = str(Path(str(raw)).expanduser()) if raw else ""
            if not path or path in known or not Path(path).is_file():
                continue
            known.add(path)
            item = QListWidgetItem(f"{Path(path).name}  ·  {_file_size(path)}")
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self.import_files_list.addItem(item)
        self._sync_import_options()

    def _remove_selected_import_files(self) -> None:
        for item in self.import_files_list.selectedItems():
            self.import_files_list.takeItem(self.import_files_list.row(item))
        self._sync_import_options()

    def _clear_import_files(self) -> None:
        self.import_files_list.clear()
        self._sync_import_options()

    def _import_paths(self) -> list[str]:
        return [
            self.import_files_list.item(index).data(Qt.UserRole)
            for index in range(self.import_files_list.count())
        ]

    def _start_import(self) -> None:
        paths = self._import_paths()
        if not paths:
            return
        self.import_requested.emit(paths, self.import_options())

    def _sync_import_options(self) -> None:
        """Accorde les widgets entre eux et met a jour les indications."""
        has_files = self.import_files_list.count() > 0
        self.import_start_button.setEnabled(has_files and not self._import_running)
        diarize = self.import_diarize_check.isChecked()
        self.import_speakers_spin.setEnabled(diarize)

        model = self.import_model_combo.currentData() or DEFAULT_DIARIZATION_MODEL
        if not diarize:
            hint = "Diarisation coupée : tout le texte sera attribué à un seul locuteur."
        elif not diarizes_by_default(model):
            hint = (
                "Ce modèle ne sépare pas les locuteurs : choisis-en un autre ou "
                "décoche la diarisation."
            )
        else:
            hint = ""
        self.import_options_hint.setText(hint)
        self._refresh_import_estimate()

    def _refresh_import_estimate(self) -> None:
        paths = self._import_paths()
        if not paths:
            self.import_estimate_label.setText("")
            return
        seconds = 0.0
        unknown = 0
        for path in paths:
            try:
                seconds += float(audiofiles.probe(Path(path)).duration)
            except Exception:
                unknown += 1
        parts = [f"{len(paths)} fichier" + ("s" if len(paths) > 1 else "")]
        if seconds:
            parts.append(format_duration(seconds))
            rate = self._import_model_rate()
            if rate:
                parts.append(f"≈ {format_money(seconds / 3600.0 * rate)}")
        if unknown:
            parts.append(f"{unknown} durée(s) inconnue(s)")
        self.import_estimate_label.setText(" · ".join(parts))

    def _import_model_rate(self) -> float | None:
        model = self.import_model_combo.currentData() or DEFAULT_DIARIZATION_MODEL
        for item in self._catalogue.stt:
            if item.get("id") == model:
                return item.get("per_hour")
        return None

    # ------------------------------------------------------------------
    # Importer : progression (pilotee par l'application)
    # ------------------------------------------------------------------
    def set_import_running(self, running: bool, label: str = "") -> None:
        self._import_running = bool(running)
        self.import_progress_frame.setVisible(self._import_running)
        if self._import_running:
            self.import_progress_label.setText(label or "Import en cours…")
            self.import_progress_bar.setRange(0, 1)
            self.import_progress_bar.setValue(0)
        self.import_start_button.setEnabled(
            bool(self._import_paths()) and not self._import_running
        )

    def set_import_progress(self, done: int, total: int, message: str) -> None:
        self.import_progress_bar.setRange(0, max(total, 1))
        self.import_progress_bar.setValue(max(0, done))
        if total:
            self.import_progress_label.setText(f"{message} — fichier {done}/{total}")
        else:
            self.import_progress_label.setText(message or "Import en cours…")

    def on_import_done(self, entry_id: str) -> None:
        entry = library.get(entry_id)
        if entry is None:
            return
        bits: list[str] = []
        if entry.seconds:
            bits.append(format_duration(entry.seconds))
        if len(entry.speakers) > 1:
            bits.append(f"{len(entry.speakers)} locuteurs")
        if entry.cost:
            bits.append(format_money(entry.cost))
        text = f"✔ {entry.title}" + (" — " + " · ".join(bits) if bits else "")
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, entry_id)
        item.setToolTip(f"{entry.source}\nDouble-clique pour l'ouvrir dans la bibliothèque")
        self.import_results_list.addItem(item)
        self.import_results_list.scrollToBottom()

    def on_import_failed(self, path: str, message: str = "") -> None:
        item = QListWidgetItem(f"✕ {Path(path).name} — échec")
        item.setToolTip(message or "L'import a échoué.")
        self.import_results_list.addItem(item)
        self.import_results_list.scrollToBottom()

    def on_import_finished(self, message: str = "") -> None:
        if self.import_results_list.count():
            self._clear_import_files()
        self.set_import_running(False)
        self.import_status_label.setText(message or IMPORT_HINT)

    def _build_processing(self) -> QWidget:
        """Réglages techniques des imports : tout est automatique par défaut."""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 12, 4, 4)

        box = QGroupBox("Imports audio : réglages techniques")
        form = QFormLayout(box)

        self.chunk_spin = NoWheelSpinBox()
        self.chunk_spin.setRange(0, 1500)
        self.chunk_spin.setSuffix(" s")
        self.chunk_spin.setSpecialValueText("automatique")
        self.chunk_spin.setToolTip(
            "Durée visée des morceaux envoyés à l'API. Vox découpe aux silences "
            "autour de cette valeur. Réduis-la si un modèle refuse les fichiers "
            "trop lourds. 0 = valeurs conseillées par modèle."
        )
        form.addRow("Taille des tranches", self.chunk_spin)

        self.parallel_spin = NoWheelSpinBox()
        self.parallel_spin.setRange(1, 6)
        form.addRow("Tranches en parallèle", self.parallel_spin)

        self.merge_speakers_check = QCheckBox(
            "Raccorder les locuteurs entre tranches (passe LLM)"
        )
        self.merge_speakers_check.setToolTip(
            "Quand un fichier est découpé, chaque tranche a ses propres "
            "étiquettes de locuteurs : une passe de modèle les raccorde. "
            "Désactive-la pour économiser quelques centimes, au prix de "
            "locuteurs en double (fusionnables à la main dans la bibliothèque)."
        )
        form.addRow("", self.merge_speakers_check)
        outer.addWidget(box)

        hint = QLabel(
            "Ces réglages ne concernent que les fichiers importés (onglet "
            "« Importer », bouton « Importer » de la bibliothèque, commande "
            "vox --import). La dictée garde ses propres modèles, dans l'onglet "
            "Général. Le modèle, la diarisation et le nettoyage se choisissent "
            "dans l'onglet « Importer »."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)
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

        self.update_status = QLabel("")
        self.update_status.setObjectName("hint")
        self.update_status.setWordWrap(True)
        self.update_status.setText(
            "Clique sur « Vérifier les mises à jour » pour comparer avec la "
            "dernière version publiée."
        )
        form.addRow("", self.update_status)

        self.update_notes = QLabel("")
        self.update_notes.setObjectName("hint")
        self.update_notes.setWordWrap(True)
        self.update_notes.setVisible(False)
        form.addRow("", self.update_notes)

        self.download_bar = QProgressBar()
        self.download_bar.setRange(0, 100)
        self.download_bar.setValue(0)
        self.download_bar.setVisible(False)
        form.addRow("", self.download_bar)

        self.manifest_edit = QLineEdit()
        self.manifest_edit.setPlaceholderText(
            "https://github.com/quentinvled/vox/releases/latest/download/version.json"
        )

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.update_button = QPushButton("Mettre à jour maintenant")
        self.update_button.setObjectName("primary")
        self.update_button.setToolTip(
            "Télécharge la nouvelle version, l'installe et redémarre Vox."
        )
        self.update_button.setVisible(False)
        self.update_button.clicked.connect(self.update_requested.emit)
        actions.addWidget(self.update_button)
        self.check_button = QPushButton("Vérifier les mises à jour")
        self.check_button.clicked.connect(
            lambda: self.check_requested.emit(self.manifest_edit.text().strip())
        )
        actions.addWidget(self.check_button)
        actions.addStretch(1)
        form.addRow("", actions)

        outer.addWidget(box)

        advanced = QGroupBox("Options avancées")
        advanced.setCheckable(True)
        advanced.setChecked(False)
        advanced_form = QFormLayout(advanced)
        advanced_form.addRow("Adresse de mise à jour", self.manifest_edit)
        outer.addWidget(advanced)

        hint = QLabel(
            "Vox compare son numéro de version à celui publié, puis télécharge "
            "et installe la nouvelle version en un clic — sans rien laisser "
            "dans ton dossier Téléchargements."
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

    def show_updates_tab(self) -> None:
        """Affiche l'onglet « Mises à jour »."""
        self.tabs.setCurrentIndex(self._updates_tab_index)

    # ------------------------------------------------------------------
    # Etat de l'onglet (pilote par l'application)
    # ------------------------------------------------------------------
    def show_checking(self) -> None:
        self.check_button.setEnabled(False)
        self.update_button.setEnabled(False)
        self._set_update_status("Vérification en cours…")

    def show_update_available(self, info) -> None:
        self._update_info = info
        notes = (info.notes or "").strip()
        message = f"Vox {info.version} est disponible."
        if info.published_at:
            message += f" (publiée le {info.published_at})"
        self._set_update_status(message, "success")
        self.update_notes.setText(notes)
        self.update_notes.setVisible(bool(notes))
        self._hide_progress()
        has_url = bool(info.url)
        self.update_button.setVisible(has_url)
        self.update_button.setEnabled(has_url)
        self.check_button.setEnabled(True)

    def show_up_to_date(self) -> None:
        self._update_info = None
        self.update_notes.setVisible(False)
        self.update_button.setVisible(False)
        self._hide_progress()
        self.check_button.setEnabled(True)
        self._set_update_status(f"Vox {__version__} est à jour.", "success")

    def show_check_error(self, reason: str) -> None:
        self._update_info = None
        self.update_notes.setVisible(False)
        self.update_button.setVisible(False)
        self._hide_progress()
        self.check_button.setEnabled(True)
        self._set_update_status(f"Vérification impossible : {reason}", "error")

    def show_progress(self, received: int, total: int) -> None:
        self.update_button.setVisible(False)
        self.download_bar.setVisible(True)
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

    def show_installing(self) -> None:
        self.download_bar.setRange(0, 100)
        self.download_bar.setValue(100)
        self.download_bar.setFormat("Installation…")
        self._set_update_status("Installation… Vox va redémarrer.", "success")

    def show_download_error(self, message: str) -> None:
        self._hide_progress()
        has_url = bool(self._update_info and self._update_info.url)
        self.update_button.setVisible(has_url)
        self.update_button.setEnabled(has_url)
        self.check_button.setEnabled(True)
        self._set_update_status(f"Échec : {message}", "error")

    def _hide_progress(self) -> None:
        self.download_bar.setVisible(False)
        self.download_bar.setRange(0, 100)
        self.download_bar.setValue(0)
        self.download_bar.setFormat("%p %")

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
        self.manifest_edit.setText(settings.update_manifest_url)

        index = self.device_combo.findData(settings.input_device)
        self.device_combo.setCurrentIndex(max(0, index))
        self.max_seconds_spin.setValue(settings.max_record_seconds)
        self.min_seconds_spin.setValue(settings.min_record_seconds)
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

        if settings.diarization_model and self.import_model_combo.findData(
            settings.diarization_model
        ) < 0:
            self.import_model_combo.addItem(
                settings.diarization_model, settings.diarization_model
            )
        self._select_data(self.import_model_combo, settings.diarization_model)
        self.import_diarize_check.setChecked(settings.import_diarize)
        self.import_speakers_spin.setValue(
            max(0, min(8, int(settings.import_speakers or 0)))
        )
        self.clean_imports_check.setChecked(settings.clean_imports)
        self.chunk_spin.setValue(int(settings.import_chunk_seconds or 0))
        self.parallel_spin.setValue(max(1, int(settings.import_parallel or 3)))
        self.merge_speakers_check.setChecked(settings.import_merge_speakers)
        self._sync_import_options()

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
            update_manifest_url=self.manifest_edit.text().strip(),
            input_device=self.device_combo.currentData(),
            max_record_seconds=self.max_seconds_spin.value(),
            min_record_seconds=self.min_seconds_spin.value(),
            theme="dark",
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
            diarization_model=self.import_model_combo.currentData() or "",
            import_diarize=self.import_diarize_check.isChecked(),
            import_speakers=int(self.import_speakers_spin.value()),
            import_chunk_seconds=int(self.chunk_spin.value()),
            import_parallel=int(self.parallel_spin.value()),
            import_merge_speakers=self.merge_speakers_check.isChecked(),
            clean_imports=self.clean_imports_check.isChecked(),
        )

    def custom_custom_text(self) -> str:
        return self.custom_prompt_edit.toPlainText().strip()
