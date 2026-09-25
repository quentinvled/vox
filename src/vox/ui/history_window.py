"""Historique des enregistrements : ecoute, copie, retranscription.

La fenetre ne connait ni le pipeline ni le reseau : elle emet des signaux que
`app.VoxApp` branche sur le pipeline. Cela la rend testable seule.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import recordings
from ..config import Settings
from ..recordings import Recording
from ..stats import format_money
from .widgets import IconButton

# Au dela, la liste devient penible a parcourir : on n'affiche que le recent.
MAX_LISTED = 400


def _clock(ms: int) -> str:
    total = max(0, int(ms)) // 1000
    return f"{total // 60}:{total % 60:02d}"


class RecordingsWindow(QWidget):
    """Liste des dictees conservees, avec lecteur audio integre."""

    copy_requested = Signal(str)
    insert_requested = Signal(str)
    retranscribe_requested = Signal(str)
    delete_requested = Signal(str)
    settings_requested = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._entries: list[Recording] = []
        self._current: Recording | None = None
        self._seeking = False

        self.setWindowTitle("Enregistrements — Vox")
        self.setMinimumSize(820, 620)

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

        header = QHBoxLayout()
        title = QLabel("Enregistrements")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("hint")
        header.addWidget(self.summary_label)
        header.addStretch(1)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Rechercher dans les textes…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(240)
        self.search_edit.textChanged.connect(self._apply_filter)
        header.addWidget(self.search_edit)
        self.folder_button = QPushButton("Dossier")
        self.folder_button.setToolTip("Ouvrir le dossier des enregistrements dans l'explorateur")
        self.folder_button.clicked.connect(self._open_folder)
        header.addWidget(self.folder_button)
        self.settings_button = QPushButton("Réglages")
        self.settings_button.setToolTip("Ouvrir les réglages de Vox")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.settings_button)
        outer.addLayout(header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_list())
        splitter.addWidget(self._build_detail())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([330, 470])
        outer.addWidget(splitter, 1)

        outer.addWidget(self._build_player())

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

    def _build_detail(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self.detail_title = QLabel("Aucun enregistrement sélectionné")
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
            "Le texte transcrit apparaîtra ici. Sélectionne un enregistrement "
            "dans la liste de gauche."
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

        self.position_slider = QSlider(Qt.Horizontal)
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
    # Donnees
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Recharge l'index depuis le disque."""
        selected = self._current.audio if self._current else ""
        self._entries = recordings.load(limit=MAX_LISTED)
        info = recordings.stats()
        self.summary_label.setText(
            f"{info['count']} enregistrement(s) · {info['size']} · "
            f"{info['seconds'] / 60:.0f} min d'audio"
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
        for entry in self._entries:
            item = QListWidgetItem(self._row_text(entry))
            item.setData(Qt.UserRole, entry.audio)
            item.setToolTip(entry.preview(200))
            self.list_widget.addItem(item)

        if not self._entries:
            self.list_hint.setText(
                "Aucun enregistrement pour l'instant. Dicte quelque chose avec "
                f"{self.settings.hotkey} et il apparaîtra ici."
            )
            self._show_entry(None)
            return

        self.list_hint.setText("")
        if len(self._entries) >= MAX_LISTED:
            self.list_hint.setText(
                f"Les {MAX_LISTED} enregistrements les plus récents sont affichés. "
                "Les plus anciens restent sur le disque."
            )
        if selected:
            self._select_audio(selected)
        self._apply_filter(self.search_edit.text())

    @staticmethod
    def _row_text(entry: Recording) -> str:
        head = f"{entry.short_when()} · {entry.duration_label()}"
        if entry.status != "ok":
            label = recordings.STATUS_LABELS.get(entry.status, entry.status)
            return f"{head} · {label}"
        return f"{head} · {entry.preview(40)}"

    def _select_audio(self, audio: str) -> bool:
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.data(Qt.UserRole) == audio:
                self.list_widget.setCurrentItem(item)
                return True
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        return False

    def _apply_filter(self, needle: str) -> None:
        needle = (needle or "").strip().lower()
        visible = 0
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            audio = item.data(Qt.UserRole)
            entry = next((e for e in self._entries if e.audio == audio), None)
            if entry is None:
                continue
            haystack = f"{entry.text} {entry.transcript} {entry.at}".lower()
            match = not needle or needle in haystack
            item.setHidden(not match)
            visible += match
        if needle and not visible:
            self.list_hint.setText("Aucun enregistrement ne correspond à cette recherche.")

    def _current_entry(self) -> Recording | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        audio = item.data(Qt.UserRole)
        return next((e for e in self._entries if e.audio == audio), None)

    # ------------------------------------------------------------------
    # Affichage du detail
    # ------------------------------------------------------------------
    def _on_selection(self, *_args) -> None:
        entry = self._current_entry()
        if entry is None or (self._current and entry.audio == self._current.audio):
            return
        self._show_entry(entry)

    def _show_entry(self, entry: Recording | None) -> None:
        self.player.stop()
        self._current = entry
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
            self.detail_title.setText("Aucun enregistrement sélectionné")
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

    # ------------------------------------------------------------------
    # Lecture audio
    # ------------------------------------------------------------------
    def _on_play(self) -> None:
        entry = self._current
        if entry is None or not entry.exists:
            return
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        if playing:
            self.player.pause()
            return
        # Un autre fichier etait charge : on repart du debut.
        if self.player.source().toLocalFile() != str(entry.path):
            self.player.setSource(QUrl.fromLocalFile(str(entry.path)))
        self.player.play()

    def _on_playback_state(self, state) -> None:
        self.play_button.setText("❚❚" if state == QMediaPlayer.PlayingState else "▶")

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.EndOfMedia:
            self.position_slider.setValue(0)
            self.time_label.setText(f"0:00 / {_clock(self.player.duration())}")

    def _on_player_error(self, _error, message: str) -> None:
        if message:
            self.detail_meta.setText(f"Lecture impossible : {message}")

    def _on_position(self, ms: int) -> None:
        if not self._seeking:
            self.position_slider.setValue(int(ms))
        self.time_label.setText(f"{_clock(ms)} / {_clock(self.player.duration())}")

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
    # Actions
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

    def _open_folder(self) -> None:
        from PySide6.QtCore import QUrl as _QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(_QUrl.fromLocalFile(str(recordings.audio_path("").parent)))

    # ------------------------------------------------------------------
    def on_retranscribed(self, audio: str, text: str) -> None:
        """Appele par l'application quand une retranscription aboutit."""
        self.refresh()
        self._select_audio(audio)
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

    def closeEvent(self, event) -> None:
        """Fermer la fenetre coupe la lecture ; l'app continue dans la barre."""
        self.player.stop()
        super().closeEvent(event)


__all__ = ["RecordingsWindow"]
