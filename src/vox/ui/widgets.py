"""Widgets maison : indicateur de niveau, rotateur, puces, icone de l'app."""

from __future__ import annotations

import math
from collections import deque

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from .theme import palette


class LevelBars(QWidget):
    """Petit histogramme de niveau audio, style « waveform »."""

    def __init__(self, bars: int = 22, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: deque[float] = deque([0.0] * bars, maxlen=bars)
        self._active = False
        self.setFixedSize(74, 46)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._theme = "dark"

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = active
        if not active:
            self._values = deque([0.0] * self._values.maxlen, maxlen=self._values.maxlen)
        self.update()

    def push(self, value: float) -> None:
        self._values.append(max(0.0, min(1.0, value)))
        self.update()

    def paintEvent(self, _event) -> None:
        colors = palette(self._theme)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        count = len(self._values)
        width = self.width()
        height = self.height()
        gap = 2.0
        bar_width = max(2.0, (width - gap * (count - 1)) / count)
        center = height / 2.0
        radius = bar_width / 2.0

        gradient = QLinearGradient(0, 0, width, 0)
        gradient.setColorAt(0.0, QColor(colors["wave_lo"]))
        gradient.setColorAt(1.0, QColor(colors["wave_hi"]))

        idle = QColor(colors["border_strong"])

        for index, value in enumerate(self._values):
            x = index * (bar_width + gap)
            amplitude = 0.16 + 0.84 * math.sqrt(value) if self._active else 0.10
            bar_height = max(3.0, amplitude * height)
            rect = QRectF(x, center - bar_height / 2.0, bar_width, bar_height)
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            painter.fillPath(path, QBrush(gradient) if self._active else idle)

        painter.end()


class Spinner(QWidget):
    """Arc rotatif pour l'etat « traitement »."""

    def __init__(self, size: int = 34, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._theme = "dark"
        self._timer = QTimer(self)
        self._timer.setInterval(28)
        self._timer.timeout.connect(self._tick)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, _event) -> None:
        colors = palette(self._theme)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        margin = 5.0
        rect = QRectF(margin, margin, self.width() - 2 * margin, self.height() - 2 * margin)

        track = QPen(QColor(colors["border_strong"]), 2.6)
        track.setCapStyle(Qt.RoundCap)
        painter.setPen(track)
        painter.drawArc(rect, 0, 360 * 16)

        arc = QPen(QColor(colors["accent"]), 2.6)
        arc.setCapStyle(Qt.RoundCap)
        painter.setPen(arc)
        painter.drawArc(rect, -self._angle * 16, 100 * 16)
        painter.end()


class Chip(QPushButton):
    """Petite puce arrondie, eventuellement basculable."""

    def __init__(self, text: str = "", checkable: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("chip")
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)


class IconButton(QPushButton):
    """Bouton rond sans fond, pour les icones discretes."""

    def __init__(self, text: str = "✕", tooltip: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("iconButton")
        self.setFixedSize(26, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        if tooltip:
            self.setToolTip(tooltip)


def make_app_icon(size: int = 256) -> QIcon:
    """Icone « onde vocale » dessinee en code (aucun asset externe).

    Les barres sont volontairement epaisses et peu margées : a 16 px, dans la
    zone de notification, l'icone doit rester lisible et reconnaissable.
    Les tailles multiples couvrent les mises a l'echelle Windows (100 a 250 %).
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#5b4fe8"))
    gradient.setColorAt(0.55, QColor("#3f7ef0"))
    gradient.setColorAt(1.0, QColor("#35c8f5"))
    painter.fillPath(path, QBrush(gradient))

    # Onde vocale : 5 barres, la centrale plus haute.
    heights = [0.34, 0.60, 0.86, 0.60, 0.34]
    bar_width = size * 0.104
    gap = size * 0.048
    total = len(heights) * bar_width + (len(heights) - 1) * gap
    left = (size - total) / 2.0
    center = size / 2.0

    painter.setPen(Qt.NoPen)
    for index, ratio in enumerate(heights):
        bar_height = ratio * size
        rect = QRectF(
            left + index * (bar_width + gap),
            center - bar_height / 2.0,
            bar_width,
            bar_height,
        )
        inner = QPainterPath()
        inner.addRoundedRect(rect, bar_width / 2.0, bar_width / 2.0)
        painter.fillPath(inner, QColor(255, 255, 255, 248))

    painter.end()

    icon = QIcon()
    # Tailles logiques + versions x1.25 / x1.5 / x1.75 / x2 pour le High-DPI.
    for dimension in (16, 20, 24, 28, 32, 36, 40, 48, 56, 64, 96, 128, 256):
        icon.addPixmap(
            pixmap.scaled(dimension, dimension, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
    return icon
