"""Fenêtre de réglages."""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..api import PROVIDERS, Client
from ..config import HOTKEY_CHOICES, Settings
from ..models import Catalogue
from ..models import label as model_label
from ..recorder import list_input_devices
from ..reword import TONES

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


class SettingsWindow(QDialog):
    """Boîte de dialogue de configuration."""

    def __init__(self, settings: Settings, catalogue: Catalogue, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Réglages — Vox")
        self.setMinimumSize(680, 620)
        self._settings = settings
        self._catalogue = catalogue
        self._tester: _KeyTester | None = None
        self._keys: dict[str, str] = {}
        self._active_provider: str = settings.provider

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
        layout.addWidget(title)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_general(), "Général")
        self.tabs.addTab(self._build_audio(), "Audio")
        self.tabs.addTab(self._build_output(), "Sortie")
        self.tabs.addTab(self._build_reword(), "Reformulation")
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
    def _build_general(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 12, 4, 4)

        # --- API ---
        api_box = QGroupBox("Fournisseur et clé API")
        api_form = QFormLayout(api_box)

        self.provider_combo = QComboBox()
        for name, provider in PROVIDERS.items():
            self.provider_combo.addItem(provider.label, name)
        api_form.addRow("Fournisseur", self.provider_combo)

        self.key_row_label = QLabel("Clé API")
        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-or-v1-…")
        self.key_edit.setMinimumWidth(320)
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

        self.stt_combo = QComboBox()
        self.stt_combo.setMinimumWidth(360)
        api_form.addRow("Modèle de transcription", self.stt_combo)

        self.chat_combo = QComboBox()
        self.chat_combo.setMinimumWidth(360)
        api_form.addRow("Modèle de reformulation", self.chat_combo)

        self.language_combo = QComboBox()
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

        self.hotkey_combo = QComboBox()
        for key, name in HOTKEY_CHOICES.items():
            self.hotkey_combo.addItem(name, key)
        trigger_form.addRow("Raccourci", self.hotkey_combo)

        self.hotkey_mode_combo = QComboBox()
        for key, name in HOTKEY_MODES:
            self.hotkey_mode_combo.addItem(name, key)
        trigger_form.addRow("Comportement", self.hotkey_mode_combo)

        self.enter_check = QCheckBox("Ajouter un « Entrée » après un double appui")
        self.enter_check.setToolTip(
            "Uniquement en mode bascule : un second appui sur le raccourci termine "
            "la dictée et valide la ligne. Sans effet en push-to-talk."
        )
        trigger_form.addRow("", self.enter_check)

        self.autostart_check = QCheckBox("Lancer Vox au démarrage de Windows")
        trigger_form.addRow("", self.autostart_check)

        outer.addWidget(trigger_box)
        outer.addStretch(1)
        return page

    # ------------------------------------------------------------------
    def _build_audio(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 12, 4, 4)

        device_box = QGroupBox("Microphone")
        device_form = QFormLayout(device_box)

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(380)
        self.device_combo.addItem("Périphérique par défaut de Windows", None)
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

        self.max_seconds_spin = QSpinBox()
        self.max_seconds_spin.setRange(10, 3600)
        self.max_seconds_spin.setSuffix(" s")
        limits_form.addRow("Durée maximale d'un enregistrement", self.max_seconds_spin)

        limit_hint = QLabel(
            "Au-delà de deux minutes, l'API peut dépasser le délai amont de 60 s et "
            "échouer. Pour de longues prises de parole, enregistre en plusieurs fois."
        )
        limit_hint.setObjectName("hint")
        limit_hint.setWordWrap(True)

        self.min_seconds_spin = QDoubleSpinBox()
        self.min_seconds_spin.setRange(0.0, 5.0)
        self.min_seconds_spin.setSingleStep(0.1)
        self.min_seconds_spin.setDecimals(1)
        self.min_seconds_spin.setSuffix(" s")
        limits_form.addRow("Durée minimale (en dessous : abandon)", self.min_seconds_spin)

        outer.addWidget(limits_box)
        outer.addWidget(limit_hint)

        ui_box = QGroupBox("Interface")
        ui_form = QFormLayout(ui_box)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Sombre", "dark")
        self.theme_combo.addItem("Clair", "light")
        ui_form.addRow("Thème", self.theme_combo)

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

        tray_hint = QLabel(
            "Vox n'a pas de fenêtre principale : il vit dans la zone de notification "
            "(derrière le chevron ^ près de l'horloge). Windows y range les nouvelles "
            "icônes par défaut ; le menu « Où est mon icône ? » explique comment "
            "l'épingler à côté de l'horloge. Si tu préfères une présence permanente "
            "dans la barre des tâches, coche l'option ci-dessus."
        )
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

        self.method_combo = QComboBox()
        for key, name in INJECT_METHODS:
            self.method_combo.addItem(name, key)
        form.addRow("Méthode", self.method_combo)

        self.paste_combo = QComboBox()
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

        warn = QLabel(
            "Limite connue : dans une fenêtre élevée (UAC / administrateur), Windows "
            "refuse l'insertion. Vox affiche alors une erreur et le texte reste "
            "disponible via « Réinsérer le dernier texte »."
        )
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

        self.tone_combo = QComboBox()
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

    # ------------------------------------------------------------------
    def _wire(self) -> None:
        self.test_button.clicked.connect(self._test_key)
        self.reload_button.clicked.connect(self._reload_devices)
        self.reword_check.toggled.connect(self._sync_reword_state)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)

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
        self.device_combo.addItem("Peripherique par defaut de Windows", None)
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

        self._select_data(self.hotkey_combo, settings.hotkey)
        self._select_data(self.hotkey_mode_combo, settings.hotkey_mode)
        self.enter_check.setChecked(settings.double_tap_enter)
        self.autostart_check.setChecked(settings.autostart)

        index = self.device_combo.findData(settings.input_device)
        self.device_combo.setCurrentIndex(max(0, index))
        self.max_seconds_spin.setValue(settings.max_record_seconds)
        self.min_seconds_spin.setValue(settings.min_record_seconds)
        self._select_data(self.theme_combo, settings.theme)
        self.sounds_check.setChecked(settings.sounds)
        self.overlay_result_check.setChecked(settings.show_overlay_on_result)
        self.history_check.setChecked(settings.history_enabled)
        self.notify_check.setChecked(settings.notify_on_start)
        self.taskbar_check.setChecked(settings.show_in_taskbar)

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
            hotkey=self.hotkey_combo.currentData(),
            hotkey_mode=self.hotkey_mode_combo.currentData(),
            double_tap_enter=self.enter_check.isChecked(),
            autostart=self.autostart_check.isChecked(),
            input_device=self.device_combo.currentData(),
            max_record_seconds=self.max_seconds_spin.value(),
            min_record_seconds=self.min_seconds_spin.value(),
            theme=self.theme_combo.currentData(),
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
        )

    def custom_custom_text(self) -> str:
        return self.custom_prompt_edit.toPlainText().strip()
