"""Fenêtre flottante (« pilule ») : état, niveau, choix du modèle, reformulation."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from .theme import palette
from .widgets import Chip, IconButton, LevelBars, Spinner

SHADOW_MARGIN = 24
CARD_WIDTH = 524
CARD_HEIGHT = 96
TEXT_BUDGET = 220      # largeur utile (px) pour le titre et le sous-titre
CHIP_BUDGET = 116      # largeur utile (px) pour le texte des puces
CHIP_WIDTH = 132

STATE_TITLES = {
    "idle": "Prêt",
    "recording": "J'écoute…",
    "transcribing": "Transcription…",
    "rewording": "Reformulation…",
    "done": "Texte inséré",
    "error": "Problème",
}


def _elide_px(widget: QWidget, text: str, budget: int) -> str:
    """Tronque proprement (…) selon la largeur réelle des glyphes."""
    metrics = QFontMetrics(widget.font())
    return metrics.elidedText(" ".join((text or "").split()), Qt.ElideRight, budget)


class Overlay(QWidget):
    """Pilule flottante toujours au-dessus, déplaçable à la souris."""

    model_selected = Signal(str)
    reword_toggled = Signal(bool)
    reinsert_requested = Signal()
    copy_requested = Signal()
    settings_requested = Signal()
    hidden_by_user = Signal()
    moved = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Vox")

        self._theme = "dark"
        self._state = "idle"
        self._tone = "clean"
        self._drag_offset: QPoint | None = None
        self._hide_delay = 0
        self._taskbar_visible = False
        self._full_text = ""

        self._build()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._auto_hide)

        self._menu = QMenu(self)

    def set_hide_delay(self, seconds: int) -> None:
        """Delai avant masquage automatique. 0 = ne se masque jamais."""
        self._hide_delay = max(0, int(seconds))

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)

        self.card = QFrame(self)
        self.card.setObjectName("card")
        self.card.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        outer.addWidget(self.card)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 150))
        self.card.setGraphicsEffect(shadow)

        row = QHBoxLayout(self.card)
        row.setContentsMargins(16, 14, 12, 14)
        row.setSpacing(12)

        # --- niveau audio / rotateur ---
        self.bars = LevelBars()
        row.addWidget(self.bars)

        self.spinner = Spinner()
        self.spinner.hide()
        row.addWidget(self.spinner)

        # --- textes ---
        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(STATE_TITLES["idle"])
        self.title.setObjectName("overlayTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("overlaySubtitle")
        texts.addStretch(1)
        texts.addWidget(self.title)
        texts.addWidget(self.subtitle)
        texts.addStretch(1)
        row.addLayout(texts, 1)

        # --- puces (modele + reformulation) ---
        chips = QVBoxLayout()
        chips.setSpacing(6)
        chips.setContentsMargins(0, 0, 0, 0)
        self.model_chip = Chip("Modèle")
        self.model_chip.setFixedWidth(CHIP_WIDTH)
        self.model_chip.setToolTip("Modèle de transcription")
        self.model_chip.clicked.connect(self._show_model_menu)
        chips.addWidget(self.model_chip)

        self.reword_chip = Chip("Reformuler", checkable=True)
        self.reword_chip.setFixedWidth(CHIP_WIDTH)
        self.reword_chip.setToolTip("Reformuler le texte avant insertion")
        self.reword_chip.clicked.connect(self._on_reword_clicked)
        chips.addWidget(self.reword_chip)
        row.addLayout(chips)

        # --- actions ---
        actions = QVBoxLayout()
        actions.setSpacing(6)
        actions.setContentsMargins(0, 0, 0, 0)
        self.close_button = IconButton("✕", "Masquer")
        self.close_button.clicked.connect(self._on_close)
        actions.addWidget(self.close_button)

        self.settings_button = IconButton("⚙", "Réglages")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        actions.addWidget(self.settings_button)
        row.addLayout(actions)

    # ------------------------------------------------------------------
    # Apparence
    # ------------------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        self.bars.set_theme(theme)
        self.spinner.set_theme(theme)

    def set_taskbar_visible(self, visible: bool) -> None:
        """Affiche ou non la pilule comme une vraie application de la barre des taches.

        Avec `Qt.Tool`, la fenetre est exclue de la barre des taches ; avec
        `Qt.Window`, elle y obtient un bouton. Le changement de drapeaux masque
        la fenetre : il faut la reafficher.
        """
        self._taskbar_visible = visible
        flags = self.windowFlags()
        flags &= ~Qt.Tool
        flags &= ~Qt.Window
        flags |= Qt.Window if visible else Qt.Tool
        self.setWindowFlags(flags)
        # Sans interet quand la fenetre est deja accessible par la barre des taches.
        self.close_button.setVisible(not visible)
        if not visible:
            self.close_button.setToolTip("Masquer")

    def set_models(self, models: list[dict], current: str) -> None:
        self._menu.clear()
        for model in models:
            model_id = model.get("id")
            action = self._menu.addAction(model.get("name") or model_id or "?")
            action.setCheckable(True)
            action.setChecked(model_id == current)
            action.triggered.connect(lambda _=False, mid=model_id: self.model_selected.emit(mid))
        self.set_current_model(current, models)

    def set_current_model(self, model_id: str, models: list[dict] | None = None) -> None:
        label = model_id
        for model in models or []:
            if model.get("id") == model_id:
                label = model.get("name") or model_id
                break
        short = label.split(" · ")[0].split("/")[-1]
        self.model_chip.setText(_elide_px(self.model_chip, short, CHIP_BUDGET))

    def set_tone(self, tone: str, label: str) -> None:
        self._tone = tone
        self.reword_chip.setText(_elide_px(self.reword_chip, label, CHIP_BUDGET))

    def set_reword_enabled(self, enabled: bool) -> None:
        self.reword_chip.setChecked(enabled)

    def set_state(self, state: str, message: str = "", detail: str = "") -> None:
        self._state = state
        self.bars.setVisible(state == "recording")
        self.spinner.setVisible(state in {"transcribing", "rewording"})
        if state in {"transcribing", "rewording"}:
            self.spinner.start()
        else:
            self.spinner.stop()

        self.bars.set_active(state == "recording")
        self.title.setText(STATE_TITLES.get(state, state))

        wanted = "overlaySubtitleError" if state == "error" else "overlaySubtitle"
        if self.subtitle.objectName() != wanted:
            self.subtitle.setObjectName(wanted)
            self.subtitle.style().unpolish(self.subtitle)
            self.subtitle.style().polish(self.subtitle)

        if state == "recording":
            self._set_subtitle(detail or "Ctrl + Maj pour arrêter")
        elif state in {"transcribing", "rewording"}:
            self._set_subtitle(detail or "Patientez…")
        elif state == "done":
            self._set_subtitle(message or detail)
        elif state == "error":
            self._set_subtitle(message)
        else:
            self._set_subtitle(detail or "Ctrl + Maj pour dicter")

    def _set_subtitle(self, text: str) -> None:
        self._full_text = text or ""
        self.subtitle.setText(_elide_px(self.subtitle, self._full_text, TEXT_BUDGET))
        self.subtitle.setToolTip(self._full_text if len(self._full_text) > 90 else "")

    def push_level(self, value: float) -> None:
        if self._state == "recording":
            self.bars.push(value)

    # ------------------------------------------------------------------
    def show_pill(self, delay: int | None = None) -> None:
        """Affiche la pilule.

        `delay=None` applique le reglage de l'utilisateur, `delay=0` force
        l'affichage permanent, toute autre valeur impose un delai ponctuel.
        """
        self._hide_timer.stop()
        self._reposition_if_needed()
        self.show()
        self.raise_()
        effective = self._hide_delay if delay is None else delay
        if effective > 0:
            self._hide_timer.start(effective * 1000)

    def hide_pill(self) -> None:
        # En mode barre des taches, masquer la pilule ferait disparaitre le
        # bouton : on l'ignore.
        if self._taskbar_visible:
            return
        self._hide_timer.stop()
        self.hide()

    def pin(self) -> None:
        """Affiche sans jamais masquer automatiquement."""
        self.show_pill(0)

    # ------------------------------------------------------------------
    # Interactions
    # ------------------------------------------------------------------
    def _on_reword_clicked(self) -> None:
        self.reword_toggled.emit(self.reword_chip.isChecked())

    def _on_close(self) -> None:
        self.hide_pill()
        self.hidden_by_user.emit()

    def _show_model_menu(self) -> None:
        self._menu.exec(self.model_chip.mapToGlobal(QPoint(0, self.model_chip.height())))

    def _auto_hide(self) -> None:
        if self._state in {"recording", "transcribing", "rewording"}:
            return
        self.hide()

    # ------------------------------------------------------------------
    # Position et déplacement
    # ------------------------------------------------------------------
    def _reposition_if_needed(self) -> None:
        screen = (
            QGuiApplication.screenAt(self.frameGeometry().center())
            or QGuiApplication.primaryScreen()
        )
        if screen is None:
            return
        area = screen.availableGeometry()
        if self.x() == 0 and self.y() == 0:
            x = area.center().x() - self.width() // 2
            y = area.bottom() - self.height() - 40
            self.move(int(x), int(y))

    def restore_position(self, position: list[int] | None) -> None:
        if position and len(position) == 2:
            self.move(int(position[0]), int(position[1]))
        else:
            self._reposition_if_needed()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self.moved.emit(self.x(), self.y())
            event.accept()


__all__ = ["Overlay", "palette"]
