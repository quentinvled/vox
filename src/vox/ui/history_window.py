"""Bibliothèque : dictées et imports, écoute, transcript cliquable, corrections.

La fenêtre ne connaît ni le pipeline ni le réseau : elle émet des signaux que
`app.VoxApp` branche sur le pipeline (dictées) et sur l'import (fichiers). Les
corrections de locuteurs (renommer, fusionner) passent par `library` et sont
immédiates : elles ne coûtent rien.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, library, recordings
from ..config import Settings
from ..library import ImportEntry
from ..recordings import Recording
from ..stats import format_money
from ..transcript import Transcript, stamp
from .wheel import NoWheelSlider
from .widgets import Chip, IconButton

# Au dela, la liste devient penible a parcourir : on n'affiche que le recent.
MAX_LISTED = 400

# Pastilles de locuteur : fixes pour rester lisibles sur le theme sombre et
# distinguables entre elles, quel que soit le theme force.
SPEAKER_COLORS = (
    "#6d9dfb",
    "#f2a65a",
    "#4ade80",
    "#f472b6",
    "#a78bfa",
    "#facc15",
    "#38bdf8",
    "#fb7185",
)

KIND_DICTATION = "dictee"
KIND_IMPORT = "import"
FILTERS = (("tout", "Tout"), ("dictees", "Dictées"), ("imports", "Imports"))
PAGE_DICTATION = 0
PAGE_IMPORT = 1

EXPORTS = (
    ("md", "Markdown (.md)"),
    ("txt", "Texte horodaté (.txt)"),
    ("srt", "Sous-titres SRT (.srt)"),
    ("vtt", "Sous-titres VTT (.vtt)"),
    ("json", "Données JSON (.json)"),
)


def _clock(ms: int) -> str:
    total = max(0, int(ms)) // 1000
    return f"{total // 60}:{total % 60:02d}"


def _speaker_color(index: int) -> str:
    return SPEAKER_COLORS[index % len(SPEAKER_COLORS)]


def _safe_name(text: str) -> str:
    """Nom de fichier convenable sur Windows comme sur Linux."""
    cleaned = "".join("-" if char in '<>:"/\\|?*' else char for char in text).strip()
    return cleaned or "transcript"


class RecordingsWindow(QWidget):
    """Bibliothèque : dictées conservées et fichiers importés."""

    copy_requested = Signal(str)
    insert_requested = Signal(str)
    retranscribe_requested = Signal(str)
    delete_requested = Signal(str)
    settings_requested = Signal()

    import_requested = Signal(list)
    import_cancel_requested = Signal()
    import_retranscribe_requested = Signal(str)
    import_delete_requested = Signal(str)
    names_requested = Signal(str)

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._theme = "dark"
        self._dictations: list[Recording] = []
        self._imports: list[ImportEntry] = []
        self._current: Recording | None = None
        self._current_import: ImportEntry | None = None
        self._transcript: Transcript | None = None
        self._seeking = False
        self._filter = "tout"
        self._highlighted = -1

        self.setWindowTitle(f"Bibliothèque — Vox {__version__}")
        self.setMinimumSize(940, 660)

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.audio_out.setVolume(0.9)
        self.player.setAudioOutput(self.audio_out)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.errorOccurred.connect(self._on_player_error)

        self._build()
        self.refresh()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        outer.addLayout(self._build_header())
        outer.addWidget(self._build_progress())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_list())
        splitter.addWidget(self._build_detail())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([320, 560])
        outer.addWidget(splitter, 1)

        outer.addWidget(self._build_player())

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        title = QLabel("Bibliothèque")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("hint")
        header.addWidget(self.summary_label)
        header.addSpacing(6)

        self.filter_chips: dict[str, Chip] = {}
        for key, label in FILTERS:
            chip = Chip(label, checkable=True)
            chip.setChecked(key == "tout")
            chip.setToolTip(f"Afficher : {label.lower()}")
            chip.clicked.connect(lambda _=False, kind=key: self._set_filter(kind))
            header.addWidget(chip)
            self.filter_chips[key] = chip

        header.addStretch(1)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Rechercher…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(220)
        self.search_edit.textChanged.connect(self._apply_filter)
        header.addWidget(self.search_edit)

        self.import_button = QPushButton("Importer")
        self.import_button.setObjectName("primary")
        self.import_button.setToolTip(
            "Transcrire et diariser un fichier audio (mp3, m4a, wav, ogg…)"
        )
        self.import_button.clicked.connect(self._on_import_clicked)
        header.addWidget(self.import_button)

        self.folder_button = QPushButton("Dossier")
        self.folder_button.setToolTip("Ouvrir le dossier des enregistrements dans l'explorateur")
        self.folder_button.clicked.connect(self._open_folder)
        header.addWidget(self.folder_button)

        self.settings_button = QPushButton("Réglages")
        self.settings_button.setToolTip("Ouvrir les réglages de Vox")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.settings_button)
        return header

    def _build_progress(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self.progress_label = QLabel("Préparation…")
        layout.addWidget(self.progress_label, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setFixedWidth(220)
        layout.addWidget(self.progress_bar)

        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.clicked.connect(self.import_cancel_requested.emit)
        layout.addWidget(self.cancel_button)

        frame.hide()
        self.progress_frame = frame
        return frame

    def _build_list(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_widget.setTextElideMode(Qt.ElideRight)
        self.list_widget.currentItemChanged.connect(self._on_selection)
        layout.addWidget(self.list_widget, 1)

        self.list_hint = QLabel("")
        self.list_hint.setObjectName("hint")
        self.list_hint.setWordWrap(True)
        layout.addWidget(self.list_hint)
        return box

    def _build_detail(self) -> QStackedWidget:
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_dictation_detail())
        self.stack.addWidget(self._build_import_detail())
        return self.stack

    def _build_dictation_detail(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self.detail_title = QLabel("Aucune dictée sélectionnée")
        self.detail_title.setObjectName("panelTitle")
        self.detail_title.setWordWrap(True)
        layout.addWidget(self.detail_title)

        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("hint")
        self.detail_meta.setWordWrap(True)
        layout.addWidget(self.detail_meta)

        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setPlaceholderText(
            "Le texte transcrit apparaîtra ici. Sélectionne une dictée dans la "
            "liste de gauche."
        )
        layout.addWidget(self.text_view, 1)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.copy_button = QPushButton("Copier")
        self.copy_button.setObjectName("primary")
        self.copy_button.setToolTip("Copier le texte dans le presse-papier")
        self.copy_button.clicked.connect(self._on_copy)
        actions.addWidget(self.copy_button)

        self.insert_button = QPushButton("Réinsérer")
        self.insert_button.setToolTip("Écrire le texte dans la fenêtre actuellement active")
        self.insert_button.clicked.connect(self._on_insert)
        actions.addWidget(self.insert_button)

        self.retranscribe_button = QPushButton("Retranscrire")
        self.retranscribe_button.setToolTip(
            "Relancer la reconnaissance vocale sur l'audio d'origine"
        )
        self.retranscribe_button.clicked.connect(self._on_retranscribe)
        actions.addWidget(self.retranscribe_button)

        actions.addStretch(1)
        self.delete_button = QPushButton("Supprimer")
        self.delete_button.setToolTip("Effacer l'enregistrement audio et son texte")
        self.delete_button.clicked.connect(self._on_delete)
        actions.addWidget(self.delete_button)
        layout.addLayout(actions)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.retention_label = QLabel("")
        self.retention_label.setObjectName("hint")
        footer.addWidget(self.retention_label)
        layout.addLayout(footer)
        return panel

    def _build_import_detail(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self.import_kind = QLabel("IMPORT")
        self.import_kind.setObjectName("panelTitle")
        layout.addWidget(self.import_kind)

        self.import_title = QLabel("Aucun import sélectionné")
        self.import_title.setWordWrap(True)
        layout.addWidget(self.import_title)

        self.import_meta = QLabel("")
        self.import_meta.setObjectName("hint")
        self.import_meta.setWordWrap(True)
        layout.addWidget(self.import_meta)

        speakers_box = QWidget()
        self.speakers_layout = QHBoxLayout(speakers_box)
        self.speakers_layout.setContentsMargins(0, 0, 0, 0)
        self.speakers_layout.setSpacing(6)
        layout.addWidget(speakers_box)

        self.segments_list = QListWidget()
        self.segments_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.segments_list.setWordWrap(True)
        self.segments_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.segments_list.setToolTip(
            "Clique une réplique pour écouter à partir de ce moment."
        )
        self.segments_list.itemClicked.connect(self._on_segment_clicked)
        layout.addWidget(self.segments_list, 1)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.copy_import_button = QPushButton("Copier")
        self.copy_import_button.setObjectName("primary")
        self.copy_import_button.setToolTip("Copier tout le transcript, une réplique par ligne")
        self.copy_import_button.clicked.connect(self._on_copy_import)
        actions.addWidget(self.copy_import_button)

        self.export_button = QPushButton("Exporter")
        self.export_button.setToolTip("Enregistrer le transcript dans un fichier")
        export_menu = QMenu(self)
        for key, label in EXPORTS:
            action = export_menu.addAction(label)
            action.triggered.connect(lambda _=False, kind=key: self._export_import(kind))
        self.export_button.setMenu(export_menu)
        actions.addWidget(self.export_button)

        self.names_button = QPushButton("Proposer les prénoms")
        self.names_button.setToolTip(
            "Demander à un modèle de retrouver qui parle d'après la conversation"
        )
        self.names_button.clicked.connect(self._on_names)
        actions.addWidget(self.names_button)

        self.retranscribe_import_button = QPushButton("Retranscrire")
        self.retranscribe_import_button.setToolTip(
            "Relancer la transcription et la diarisation du fichier d'origine"
        )
        self.retranscribe_import_button.clicked.connect(self._on_import_retranscribe)
        actions.addWidget(self.retranscribe_import_button)

        actions.addStretch(1)
        self.delete_import_button = QPushButton("Retirer")
        self.delete_import_button.setToolTip(
            "Retirer l'import de la bibliothèque (le fichier d'origine reste intact)"
        )
        self.delete_import_button.clicked.connect(self._on_import_delete)
        actions.addWidget(self.delete_import_button)
        layout.addLayout(actions)

        self.import_notice = QLabel("")
        self.import_notice.setObjectName("hint")
        self.import_notice.setWordWrap(True)
        layout.addWidget(self.import_notice)
        return panel

    def _build_player(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("panel")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self.play_button = IconButton("▶", "Réécouter l'enregistrement sélectionné")
        self.play_button.setFixedWidth(40)
        self.play_button.clicked.connect(self._on_play)
        layout.addWidget(self.play_button)

        self.position_slider = NoWheelSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderPressed.connect(self._on_seek_start)
        self.position_slider.sliderReleased.connect(self._on_seek_end)
        self.position_slider.sliderMoved.connect(self._on_seek_move)
        layout.addWidget(self.position_slider, 1)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("hint")
        self.time_label.setFixedWidth(96)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.time_label)
        return bar

    # ------------------------------------------------------------------
    # Import : progression
    # ------------------------------------------------------------------
    def set_import_running(self, running: bool, label: str = "") -> None:
        self.progress_frame.setVisible(running)
        self.import_button.setEnabled(not running)
        if running:
            self.progress_label.setText(label or "Import en cours…")
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)

    def set_import_progress(self, done: int, total: int, message: str) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(max(0, done))
        self.progress_label.setText(message or f"{done}/{total}")

    def on_imported(self, entry_id: str) -> None:
        self.refresh()
        self._select_key(KIND_IMPORT, entry_id)

    def on_import_failed(self, _path: str) -> None:
        self.refresh()

    def on_import_deleted(self, entry_id: str) -> None:
        if self._current_import and self._current_import.id == entry_id:
            self.player.stop()
            self._current_import = None
            self._transcript = None
        self.refresh()

    def on_import_updated(self, entry_id: str) -> None:
        self.refresh()
        self._select_key(KIND_IMPORT, entry_id)

    def on_names_applied(self, entry_id: str, mapping: dict) -> None:
        self.refresh()
        self._select_key(KIND_IMPORT, entry_id)
        if mapping:
            self.import_notice.setText(
                "Prénoms appliqués : "
                + ", ".join(f"{label} → {name}" for label, name in mapping.items())
            )
        else:
            self.import_notice.setText("Aucun prénom identifié avec certitude.")

    def on_names_failed(self, entry_id: str, message: str) -> None:
        if self._current_import is not None and self._current_import.id == entry_id:
            self.names_button.setEnabled(self._transcript is not None)
            self.import_notice.setText(f"Prénoms : {message}")

    # ------------------------------------------------------------------
    # Donnees
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Recharge les deux index depuis le disque."""
        selected = self._selected_data()
        self._dictations = recordings.load(limit=MAX_LISTED)
        self._imports = library.load(limit=MAX_LISTED)
        info = recordings.stats()
        imported = library.stats()
        self.summary_label.setText(
            f"{info['count']} dictée(s) · {imported['count']} import(s) · "
            f"{info['size']} de dictées · {imported['seconds'] / 60:.0f} min importées"
        )
        days = self.settings.recording_retention_days
        self.retention_label.setText(
            "Conservation illimitée"
            if days <= 0
            else f"Purge automatique après {days} jours"
        )
        self._populate(selected)

    def _populate(self, selected: str = "") -> None:
        self.list_widget.clear()
        rows: list[tuple[str, str, Recording | ImportEntry]] = []
        for entry in self._dictations:
            rows.append((entry.at, KIND_DICTATION, entry))
        for entry in self._imports:
            rows.append((entry.at, KIND_IMPORT, entry))
        rows.sort(key=lambda item: item[0], reverse=True)

        for _at, kind, entry in rows:
            if kind == KIND_DICTATION:
                text = self._dictation_row(entry)  # type: ignore[arg-type]
                key = entry.audio
                haystack = f"{entry.text} {entry.transcript} {entry.at}".lower()  # type: ignore[union-attr]
                tooltip = entry.preview(200)  # type: ignore[union-attr]
            else:
                text = self._import_row(entry)  # type: ignore[arg-type]
                key = entry.id
                haystack = (
                    f"{entry.label} {entry.source} {' '.join(entry.speakers)} "  # type: ignore[union-attr]
                    f"{entry.at}"
                ).lower()
                tooltip = f"{entry.label}\n{entry.source}"  # type: ignore[union-attr]
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, f"{kind}:{key}")
            item.setData(Qt.UserRole + 2, haystack)
            item.setToolTip(tooltip)
            self.list_widget.addItem(item)

        if not rows:
            self.list_hint.setText(
                "Rien pour l'instant. Dicte avec "
                f"{self.settings.hotkey}, ou clique sur « Importer » pour "
                "transcrire un fichier audio."
            )
            self._show_nothing()
            return

        self.list_hint.setText("")
        if len(rows) >= MAX_LISTED:
            self.list_hint.setText(
                f"Les {MAX_LISTED} entrées les plus récentes sont affichées. "
                "Les plus anciennes restent sur le disque."
            )
        if selected:
            self._select_data(selected)
        elif self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self._apply_filter(self.search_edit.text())

    @staticmethod
    def _dictation_row(entry: Recording) -> str:
        head = f"🎙 {entry.short_when()} · {entry.duration_label()}"
        if entry.status != "ok":
            label = recordings.STATUS_LABELS.get(entry.status, entry.status)
            return f"{head} · {label}"
        return f"{head} · {entry.preview(36)}"

    @staticmethod
    def _import_row(entry: ImportEntry) -> str:
        head = f"📄 {entry.short_when()} · {entry.duration_label()}"
        if entry.status != "ok":
            return f"{head} · Échec — {entry.label}"
        speakers = entry.speakers_label()
        middle = f"{speakers} · " if speakers else ""
        return f"{head} · {middle}{entry.label}"

    def _selected_data(self) -> str:
        item = self.list_widget.currentItem()
        return str(item.data(Qt.UserRole) or "") if item is not None else ""

    def _select_key(self, kind: str, key: str) -> bool:
        return self._select_data(f"{kind}:{key}")

    def _select_data(self, data: str) -> bool:
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.data(Qt.UserRole) == data and not item.isHidden():
                self.list_widget.setCurrentItem(item)
                return True
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        return False

    def _set_filter(self, kind: str) -> None:
        self._filter = kind
        for key, chip in self.filter_chips.items():
            chip.setChecked(key == kind)
        self._apply_filter(self.search_edit.text())

    def _apply_filter(self, needle: str) -> None:
        needle = (needle or "").strip().lower()
        visible = 0
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            data = str(item.data(Qt.UserRole) or "")
            kind = data.split(":", 1)[0]
            kind_ok = self._filter == "tout" or (
                self._filter == "dictees" and kind == KIND_DICTATION
            ) or (self._filter == "imports" and kind == KIND_IMPORT)
            haystack = str(item.data(Qt.UserRole + 2) or "")
            match = kind_ok and (not needle or needle in haystack)
            item.setHidden(not match)
            visible += match
        if (needle or self._filter != "tout") and not visible:
            self.list_hint.setText("Aucune entrée ne correspond à ce filtre.")
        elif not needle and self._filter == "tout" and self.list_widget.count():
            self.list_hint.setText("")

    def _current_dictation(self) -> Recording | None:
        data = self._selected_data()
        kind, _, key = data.partition(":")
        if kind != KIND_DICTATION:
            return None
        return next((entry for entry in self._dictations if entry.audio == key), None)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------
    def _on_selection(self, *_args) -> None:
        data = self._selected_data()
        kind, _, key = data.partition(":")
        if kind == KIND_DICTATION:
            entry = next((e for e in self._dictations if e.audio == key), None)
            self._show_dictation(entry)
        elif kind == KIND_IMPORT:
            entry = next((e for e in self._imports if e.id == key), None)
            self._show_import(entry)
        else:
            self._show_nothing()

    def _show_nothing(self) -> None:
        self.player.stop()
        self._current = None
        self._current_import = None
        self._transcript = None
        self.stack.setCurrentIndex(PAGE_DICTATION)
        self.detail_title.setText("Aucune sélection")
        self.detail_meta.setText("")
        self.text_view.setPlainText("")
        for widget in (
            self.copy_button,
            self.insert_button,
            self.retranscribe_button,
            self.delete_button,
            self.play_button,
        ):
            widget.setEnabled(False)
        self.position_slider.setEnabled(False)
        self.position_slider.setRange(0, 0)
        self.time_label.setText("0:00 / 0:00")

    def _show_dictation(self, entry: Recording | None) -> None:
        self.player.stop()
        self._current = entry
        self._current_import = None
        self._transcript = None
        self.stack.setCurrentIndex(PAGE_DICTATION)
        has_entry = entry is not None
        for widget in (
            self.copy_button,
            self.insert_button,
            self.retranscribe_button,
            self.delete_button,
            self.play_button,
        ):
            widget.setEnabled(has_entry)
        self.position_slider.setEnabled(has_entry)
        self.position_slider.setRange(0, 0)
        self.time_label.setText("0:00 / 0:00")

        if entry is None:
            self.detail_title.setText("Aucune dictée sélectionnée")
            self.detail_meta.setText("")
            self.text_view.setPlainText("")
            return

        self.detail_title.setText(entry.when_label())
        self.text_view.setPlainText(entry.text or "")
        self.detail_meta.setText(self._meta_text(entry))
        playable = entry.exists
        self.play_button.setEnabled(playable)
        self.position_slider.setEnabled(playable)

    def _meta_text(self, entry: Recording) -> str:
        bits = [f"Durée {entry.duration_label()}"]
        status = recordings.STATUS_LABELS.get(entry.status, entry.status)
        bits.append(status)
        if entry.model:
            bits.append(entry.model)
        if entry.language:
            bits.append(entry.language.upper())
        if entry.words:
            bits.append(f"{entry.words} mot(s)")
        if entry.latency_ms:
            bits.append(f"{entry.latency_ms / 1000:.1f} s de traitement")
        if entry.cost:
            bits.append(format_money(entry.cost))
        if not entry.exists:
            bits.append("fichier audio absent")
        text = " · ".join(bits)
        if entry.error:
            text += f"\nErreur : {entry.error}"
        return text

    def _show_import(self, entry: ImportEntry | None) -> None:
        self.player.stop()
        self._current = None
        self._current_import = entry
        self._transcript = None
        self.import_notice.setText("")
        self.stack.setCurrentIndex(PAGE_IMPORT)
        if entry is None:
            self.import_title.setText("Aucun import sélectionné")
            self.import_meta.setText("")
            self._clear_speakers()
            self.segments_list.clear()
            for widget in (
                self.copy_import_button,
                self.export_button,
                self.names_button,
                self.retranscribe_import_button,
                self.delete_import_button,
                self.play_button,
            ):
                widget.setEnabled(False)
            self.position_slider.setEnabled(False)
            self.position_slider.setRange(0, 0)
            self.time_label.setText("0:00 / 0:00")
            return

        self._transcript = library.load_transcript(entry.id)
        self.import_title.setText(entry.label)
        self.import_meta.setText(self._import_meta(entry))
        self._rebuild_speakers()
        self._rebuild_segments()

        has_transcript = self._transcript is not None
        self.copy_import_button.setEnabled(has_transcript)
        self.export_button.setEnabled(has_transcript)
        self.names_button.setEnabled(has_transcript and entry.status != "erreur")
        self.retranscribe_import_button.setEnabled(entry.exists)
        self.delete_import_button.setEnabled(True)
        playable = entry.exists and has_transcript
        self.play_button.setEnabled(playable)
        self.position_slider.setEnabled(playable)
        self.position_slider.setRange(0, 0)
        self.time_label.setText("0:00 / 0:00")
        self._highlighted = -1

    def _import_meta(self, entry: ImportEntry) -> str:
        bits = [f"Durée {entry.duration_label()}"]
        if entry.speakers:
            bits.append(entry.speakers_label())
        if entry.words:
            bits.append(f"{entry.words} mot(s)")
        if entry.status != "ok":
            bits.append(recordings.STATUS_LABELS.get("erreur", "Échec"))
        if entry.model:
            bits.append(entry.model)
        if entry.language:
            bits.append(entry.language.upper())
        if entry.cost:
            bits.append(format_money(entry.cost))
        if entry.source:
            bits.append(Path(entry.source).name)
        if not entry.exists:
            bits.append("fichier d'origine introuvable")
        text = " · ".join(bits)
        if entry.error:
            text += f"\nErreur : {entry.error}"
        if entry.extras.get("avertissements"):
            text += "\n" + "\n".join(
                f"⚠ {warning}" for warning in entry.extras["avertissements"]
            )
        return text

    # ------------------------------------------------------------------
    # Locuteurs et segments
    # ------------------------------------------------------------------
    def _clear_speakers(self) -> None:
        while self.speakers_layout.count():
            item = self.speakers_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_speakers(self) -> None:
        self._clear_speakers()
        transcript = self._transcript
        if transcript is None:
            return
        for index, speaker in enumerate(transcript.speakers):
            label = transcript.label_for(speaker.id)
            chip = Chip(label)
            chip.setToolTip("Cliquer pour renommer ou fusionner ce locuteur")
            chip.setStyleSheet(f"QPushButton#chip {{ color: {_speaker_color(index)}; }}")
            chip.clicked.connect(
                lambda _=False, speaker_id=speaker.id: self._speaker_menu(speaker_id)
            )
            self.speakers_layout.addWidget(chip)
        self.speakers_layout.addStretch(1)

    def _speaker_menu(self, speaker_id: str) -> None:
        transcript = self._transcript
        entry = self._current_import
        if transcript is None or entry is None:
            return
        menu = QMenu(self)
        rename = menu.addAction("Renommer…")
        rename.triggered.connect(lambda: self._rename_speaker(speaker_id))
        others = [s for s in transcript.speakers if s.id != speaker_id]
        if others:
            menu.addSeparator()
            for other in others:
                action = menu.addAction(
                    f"Fusionner dans « {transcript.label_for(other.id)} »"
                )
                action.triggered.connect(
                    lambda _=False, keep=other.id, drop=speaker_id: self._merge_speakers(
                        keep, drop
                    )
                )
        menu.exec(QCursor.pos())

    def _rename_speaker(self, speaker_id: str) -> None:
        transcript = self._transcript
        entry = self._current_import
        if transcript is None or entry is None:
            return
        current = transcript.label_for(speaker_id)
        if current.startswith("Locuteur "):
            current = ""
        name, ok = QInputDialog.getText(
            self, "Renommer le locuteur", "Nom :", text=current
        )
        if not ok or not name.strip():
            return
        transcript.rename(speaker_id, name.strip())
        library.refresh(entry.id, transcript)
        self.on_import_updated(entry.id)

    def _merge_speakers(self, keep: str, drop: str) -> None:
        transcript = self._transcript
        entry = self._current_import
        if transcript is None or entry is None:
            return
        transcript.merge_speakers(keep, drop)
        library.refresh(entry.id, transcript)
        self.on_import_updated(entry.id)

    def _rebuild_segments(self) -> None:
        self.segments_list.clear()
        transcript = self._transcript
        if transcript is None:
            return
        order = {speaker.id: index for index, speaker in enumerate(transcript.speakers)}
        for index, segment in enumerate(transcript.segments):
            label = transcript.label_for(segment.speaker)
            item = QListWidgetItem(
                f"[{stamp(segment.start, short=True)}] {label} : {segment.text}"
            )
            item.setData(Qt.UserRole, segment.start)
            item.setData(Qt.UserRole + 1, index)
            item.setForeground(QColor(_speaker_color(order.get(segment.speaker, 0))))
            self.segments_list.addItem(item)

    def _on_segment_clicked(self, item: QListWidgetItem) -> None:
        entry = self._current_import
        if entry is None or not entry.exists:
            return
        start = float(item.data(Qt.UserRole) or 0.0)
        if self.player.source().toLocalFile() != str(entry.path):
            self.player.setSource(QUrl.fromLocalFile(str(entry.path)))
        self.player.setPosition(int(start * 1000))
        self.player.play()

    def _highlight_segment(self, ms: int) -> None:
        transcript = self._transcript
        if self._current_import is None or transcript is None or not transcript.segments:
            return
        seconds = max(0.0, ms / 1000.0)
        target = -1
        for index, segment in enumerate(transcript.segments):
            if segment.start - 0.3 <= seconds <= segment.end + 0.5:
                target = index
                break
        if target != self._highlighted and 0 <= target < self.segments_list.count():
            self._highlighted = target
            self.segments_list.setCurrentRow(target)

    # ------------------------------------------------------------------
    # Lecture audio
    # ------------------------------------------------------------------
    def _on_play(self) -> None:
        path: Path | None = None
        if self._current is not None and self._current.exists:
            path = self._current.path
        elif self._current_import is not None and self._current_import.exists:
            path = self._current_import.path
        if path is None:
            return
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        if playing:
            self.player.pause()
            return
        if self.player.source().toLocalFile() != str(path):
            self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def _on_playback_state(self, state) -> None:
        self.play_button.setText("❚❚" if state == QMediaPlayer.PlayingState else "▶")

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.EndOfMedia:
            self.position_slider.setValue(0)
            self.time_label.setText(f"0:00 / {_clock(self.player.duration())}")

    def _on_player_error(self, _error, message: str) -> None:
        if not message:
            return
        if self._current_import is not None:
            self.import_notice.setText(f"Lecture impossible : {message}")
        else:
            self.detail_meta.setText(f"Lecture impossible : {message}")

    def _on_position(self, ms: int) -> None:
        if not self._seeking:
            self.position_slider.setValue(int(ms))
        self.time_label.setText(f"{_clock(ms)} / {_clock(self.player.duration())}")
        if self._current_import is not None:
            self._highlight_segment(int(ms))

    def _on_duration(self, ms: int) -> None:
        self.position_slider.setRange(0, int(ms))
        self.time_label.setText(f"{_clock(self.player.position())} / {_clock(ms)}")

    def _on_seek_start(self) -> None:
        self._seeking = True

    def _on_seek_end(self) -> None:
        self._seeking = False
        self.player.setPosition(self.position_slider.value())

    def _on_seek_move(self, value: int) -> None:
        self.time_label.setText(f"{_clock(value)} / {_clock(self.player.duration())}")

    # ------------------------------------------------------------------
    # Actions : dictees
    # ------------------------------------------------------------------
    def _on_copy(self) -> None:
        if self._current and self._current.text:
            self.copy_requested.emit(self._current.text)

    def _on_insert(self) -> None:
        if self._current and self._current.text:
            self.insert_requested.emit(self._current.text)

    def _on_retranscribe(self) -> None:
        if self._current and self._current.exists:
            self.retranscribe_requested.emit(self._current.audio)

    def _on_delete(self) -> None:
        entry = self._current
        if entry is None:
            return
        answer = QMessageBox.question(
            self,
            "Supprimer l'enregistrement",
            f"Supprimer définitivement l'enregistrement du {entry.when_label()} "
            "et son texte ?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.delete_requested.emit(entry.audio)

    # ------------------------------------------------------------------
    # Actions : imports
    # ------------------------------------------------------------------
    def _on_import_clicked(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Importer des fichiers audio",
            str(Path.home()),
            "Audio (*.mp3 *.m4a *.wav *.ogg *.flac *.aac *.opus *.wma *.mp4 *.mkv);;"
            "Tous les fichiers (*)",
        )
        if paths:
            self.import_requested.emit(list(paths))

    def _on_copy_import(self) -> None:
        if self._transcript is not None:
            self.copy_requested.emit(self._transcript.to_text(with_time=True).strip())

    def _on_import_retranscribe(self) -> None:
        entry = self._current_import
        if entry is not None and entry.exists:
            self.retranscribe_import_button.setEnabled(False)
            self.import_retranscribe_requested.emit(entry.id)

    def _on_import_delete(self) -> None:
        entry = self._current_import
        if entry is None:
            return
        answer = QMessageBox.question(
            self,
            "Retirer l'import",
            f"Retirer « {entry.label} » de la bibliothèque ?\n"
            "Le fichier audio d'origine n'est pas touché.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.import_delete_requested.emit(entry.id)

    def _on_names(self) -> None:
        entry = self._current_import
        if entry is not None:
            self.names_button.setEnabled(False)
            self.import_notice.setText("Recherche des prénoms…")
            self.names_requested.emit(entry.id)

    def _export_import(self, fmt: str) -> None:
        transcript = self._transcript
        entry = self._current_import
        if transcript is None or entry is None:
            return
        builders = {
            "md": (".md", "Markdown (*.md)", transcript.to_markdown),
            "txt": (
                ".txt",
                "Texte horodaté (*.txt)",
                lambda: transcript.to_text(with_time=True),
            ),
            "srt": (".srt", "Sous-titres (*.srt)", transcript.to_srt),
            "vtt": (".vtt", "Sous-titres (*.vtt)", transcript.to_vtt),
            "json": (
                ".json",
                "JSON (*.json)",
                lambda: json.dumps(
                    transcript.to_dict(), ensure_ascii=False, indent=2
                ),
            ),
        }
        extension, filters, build = builders[fmt]
        suggested = str(Path.home() / f"{_safe_name(entry.label)}{extension}")
        target, _ = QFileDialog.getSaveFileName(
            self, "Exporter le transcript", suggested, filters
        )
        if not target:
            return
        try:
            Path(target).write_text(build(), encoding="utf-8")
        except OSError as exc:
            self.import_notice.setText(f"Export impossible : {exc}")
            return
        self.import_notice.setText(f"Exporté : {Path(target).name}")

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(recordings.audio_path("").parent)))

    # ------------------------------------------------------------------
    def on_retranscribed(self, audio: str, text: str) -> None:
        """Appele par l'application quand une retranscription de dictee aboutit."""
        self.refresh()
        self._select_key(KIND_DICTATION, audio)
        if text:
            self.text_view.setPlainText(text)

    def on_deleted(self, audio: str) -> None:
        recordings.delete(audio)
        if self._current and self._current.audio == audio:
            self.player.stop()
            self._current = None
        self.refresh()

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        # Les pastilles et les segments portent la couleur : on les reconstruit.
        if self._current_import is not None:
            self._rebuild_speakers()
            self._rebuild_segments()

    def closeEvent(self, event) -> None:
        """Fermer la fenetre coupe la lecture ; l'app continue dans la barre."""
        self.player.stop()
        super().closeEvent(event)


__all__ = ["RecordingsWindow"]
