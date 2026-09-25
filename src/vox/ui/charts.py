"""Composants graphiques de la fenetre Statistiques (dessines en QPainter)."""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..stats import DayPoint, format_duration
from .theme import palette


class KpiCard(QFrame):
    """Carte d'indicateur : intitule, valeur mise en avant, precision."""

    def __init__(self, title: str, accent: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("kpiAccent" if accent else "kpi")
        self.setMinimumWidth(148)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 12)
        layout.setSpacing(1)

        self.title = QLabel(title.upper())
        self.title.setObjectName("kpiTitle")
        self.value = QLabel("—")
        self.value.setObjectName("kpiValue")
        self.detail = QLabel("")
        self.detail.setObjectName("kpiDetail")

        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        layout.addStretch(1)

    def set(self, value: str, detail: str = "") -> None:
        self.value.setText(value)
        self.detail.setText(detail)


class DailyChart(QWidget):
    """Histogramme des N derniers jours, avec survol."""

    METRICS: ClassVar[dict] = {
        "words": ("Mots dictés", lambda d: d.words),
        "dictations": ("Dictées", lambda d: d.dictations),
        "seconds": ("Temps d'écoute", lambda d: d.seconds),
    }

    metric_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(168)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self._days: list[DayPoint] = []
        self._metric = "words"
        self._theme = "dark"
        self._bars: list[tuple[QRectF, DayPoint]] = []

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def set_metric(self, metric: str) -> None:
        if metric in self.METRICS:
            self._metric = metric
            self.update()

    def set_days(self, days: list[DayPoint]) -> None:
        self._days = days
        self.update()

    # ------------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        colors = palette(self._theme)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        left, right, top, bottom = 46.0, 8.0, 12.0, 26.0
        plot_width = max(1.0, self.width() - left - right)
        plot_height = max(1.0, self.height() - top - bottom)

        if not self._days:
            painter.setPen(QPen(QColor(colors["muted"])))
            painter.drawText(self.rect(), Qt.AlignCenter, "Aucune donnée")
            painter.end()
            return

        getter = self.METRICS[self._metric][1]
        values = [getter(day) for day in self._days]
        peak = max(values) or 1.0

        # --- grille + graduations ---
        grid_pen = QPen(QColor(colors["border"]))
        grid_pen.setWidth(1)
        painter.setPen(grid_pen)
        for step in range(3):
            y = top + plot_height * step / 2
            painter.drawLine(QPointF(left, y), QPointF(left + plot_width, y))

        label_font = QFont(self.font())
        label_font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1.5))
        painter.setFont(label_font)

        def scale_label(value: float) -> str:
            if self._metric == "seconds":
                return format_duration(value)
            if value >= 1000:
                return f"{value / 1000:.0f}k"
            return str(int(value))

        painter.setPen(QPen(QColor(colors["muted"])))
        for step in range(3):
            y = top + plot_height * step / 2
            value = peak * (1 - step / 2)
            painter.drawText(
                QRectF(0, y - 8, left - 8, 16),
                Qt.AlignRight | Qt.AlignVCenter,
                scale_label(value),
            )

        # --- barres ---
        count = len(self._days)
        slot = plot_width / count
        bar_width = max(2.0, slot * 0.58)
        radius = min(4.0, bar_width / 2.0)
        self._bars = []

        for index, (day, value) in enumerate(zip(self._days, values, strict=True)):
            x = left + index * slot + (slot - bar_width) / 2
            height = (value / peak) * plot_height if value else 0.0
            minimum = 2.0 if value else 0.0
            rect = QRectF(
                x,
                top + plot_height - max(height, minimum),
                bar_width,
                max(height, 0.0),
            )
            self._bars.append((QRectF(x, top, bar_width, plot_height), day))

            if value <= 0:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(colors["border"]))
                painter.drawRoundedRect(
                    QRectF(x, top + plot_height - 2, bar_width, 2), 1, 1
                )
                continue

            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
            gradient.setColorAt(0.0, QColor(colors["wave_hi"]))
            gradient.setColorAt(1.0, QColor(colors["wave_lo"]))
            painter.setPen(Qt.NoPen)
            painter.fillPath(path, QBrush(gradient))

        # --- etiquettes de dates ---
        painter.setPen(QPen(QColor(colors["muted"])))
        every = max(1, count // 6)
        for index, day in enumerate(self._days):
            if index % every and index != count - 1:
                continue
            x = left + index * slot
            painter.drawText(
                QRectF(x - 18, top + plot_height + 5, 36, 16),
                Qt.AlignCenter,
                f"{day.day.day:02d}/{day.day.month:02d}",
            )

        painter.end()

    # ------------------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:
        point = event.position()
        for rect, day in self._bars:
            if rect.contains(point):
                getter = self.METRICS[self._metric][1]
                value = getter(day)
                if self._metric == "seconds":
                    shown = format_duration(value)
                else:
                    shown = f"{value:,}".replace(",", " ")
                self.setToolTip(
                    f"{day.day.strftime('%d/%m/%Y')}\n"
                    f"{self.METRICS[self._metric][0]} : {shown}\n"
                    f"{day.dictations} dictée(s) · {day.cost:.4f} $"
                )
                return
        self.setToolTip("")


class ShareBars(QWidget):
    """Barres horizontales : repartition par modele."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._rows: list[tuple[str, float, str]] = []
        self._theme = "dark"

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def set_rows(self, rows: list[tuple[str, float, str]]) -> None:
        """rows : (libelle, part 0-1, texte a droite)."""
        self._rows = rows
        self.setMinimumHeight(max(1, len(rows)) * 26 + 6)
        self.updateGeometry()
        self.update()

    def paintEvent(self, _event) -> None:
        colors = palette(self._theme)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self._rows:
            painter.setPen(QPen(QColor(colors["muted"])))
            painter.drawText(self.rect(), Qt.AlignCenter, "Aucune donnée")
            painter.end()
            return

        row_height = 26.0
        label_width = min(190.0, self.width() * 0.42)
        value_width = 116.0
        track_x = label_width + 8
        track_width = max(20.0, self.width() - track_x - value_width)
        track_height = 8.0

        font = QFont(self.font())
        painter.setFont(font)

        for index, (label, share, right) in enumerate(self._rows):
            y = index * row_height + 4
            painter.setPen(QPen(QColor(colors["text"])))
            painter.drawText(
                QRectF(0, y, label_width, row_height - 6),
                Qt.AlignLeft | Qt.AlignVCenter,
                label,
            )

            track = QRectF(track_x, y + (row_height - 6 - track_height) / 2, track_width, track_height)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(colors["input_bg"]))
            painter.drawRoundedRect(track, track_height / 2, track_height / 2)

            filled = QRectF(track)
            filled.setWidth(max(3.0, track_width * max(0.0, min(1.0, share))))
            gradient = QLinearGradient(filled.left(), 0, filled.right(), 0)
            gradient.setColorAt(0.0, QColor(colors["wave_lo"]))
            gradient.setColorAt(1.0, QColor(colors["wave_hi"]))
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(filled, track_height / 2, track_height / 2)

            painter.setPen(QPen(QColor(colors["muted"])))
            painter.drawText(
                QRectF(self.width() - value_width, y, value_width, row_height - 6),
                Qt.AlignRight | Qt.AlignVCenter,
                right,
            )

        painter.end()


class StatRow(QWidget):
    """Petite ligne intitule / valeur."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(10)
        self.label = QLabel(label)
        self.label.setObjectName("hint")
        self.value = QLabel("—")
        layout.addWidget(self.label)
        layout.addStretch(1)
        layout.addWidget(self.value)

    def set(self, value: str) -> None:
        self.value.setText(value)


class HourlyChart(QWidget):
    """Repartition des dictees selon l'heure de la journee (0 a 23 h)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self._hours: list[int] = [0] * 24
        self._words: list[int] = [0] * 24
        self._theme = "dark"
        self._bars: list[tuple[QRectF, int]] = []

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def set_hours(self, hours: list[int], words: list[int] | None = None) -> None:
        self._hours = list(hours) if hours else [0] * 24
        self._words = list(words) if words else [0] * 24
        self.update()

    def paintEvent(self, _event) -> None:
        colors = palette(self._theme)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        left, right, top, bottom = 34.0, 8.0, 12.0, 24.0
        plot_width = max(1.0, self.width() - left - right)
        plot_height = max(1.0, self.height() - top - bottom)
        peak = max(self._hours) or 1

        grid_pen = QPen(QColor(colors["border"]))
        painter.setPen(grid_pen)
        for step in range(3):
            y = top + plot_height * step / 2
            painter.drawLine(QPointF(left, y), QPointF(left + plot_width, y))

        label_font = QFont(self.font())
        label_font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1.5))
        painter.setFont(label_font)

        slot = plot_width / 24
        bar_width = max(2.0, slot * 0.62)
        self._bars = []
        peak_hour = max(range(24), key=lambda h: self._hours[h]) if any(self._hours) else -1

        for hour, value in enumerate(self._hours):
            x = left + hour * slot + (slot - bar_width) / 2
            height = (value / peak) * plot_height if value else 0.0
            rect = QRectF(x, top + plot_height - max(height, 2.0 if value else 0.0), bar_width, max(height, 0.0))
            self._bars.append((QRectF(x, top, bar_width, plot_height), hour))
            if value <= 0:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(colors["border"]))
                painter.drawRoundedRect(QRectF(x, top + plot_height - 2, bar_width, 2), 1, 1)
                continue
            path = QPainterPath()
            path.addRoundedRect(rect, min(4.0, bar_width / 2.0), min(4.0, bar_width / 2.0))
            gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
            if hour == peak_hour:
                gradient.setColorAt(0.0, QColor(colors["wave_hi"]))
                gradient.setColorAt(1.0, QColor(colors["wave_hi"]))
            else:
                gradient.setColorAt(0.0, QColor(colors["wave_hi"]))
                gradient.setColorAt(1.0, QColor(colors["wave_lo"]))
            painter.setPen(Qt.NoPen)
            painter.fillPath(path, QBrush(gradient))

        painter.setPen(QPen(QColor(colors["muted"])))
        painter.drawText(QRectF(0, top - 4, left - 6, 16), Qt.AlignRight | Qt.AlignVCenter, str(peak))
        painter.drawText(QRectF(0, top + plot_height - 8, left - 6, 16), Qt.AlignRight | Qt.AlignVCenter, "0")
        for hour in (0, 6, 12, 18, 23):
            x = left + hour * slot + slot / 2
            painter.drawText(QRectF(x - 16, top + plot_height + 4, 32, 16), Qt.AlignCenter, f"{hour:02d}h")
        painter.end()

    def mouseMoveEvent(self, event) -> None:
        point = event.position()
        for rect, hour in self._bars:
            if rect.contains(point):
                self.setToolTip(
                    f"{hour:02d}h – {hour + 1:02d}h\n"
                    f"{self._hours[hour]} dictée(s)\n"
                    f"{self._words[hour]:,} mot(s)".replace(",", " ")
                )
                return
        self.setToolTip("")

