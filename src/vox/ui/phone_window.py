"""Fenetre « Telephone » : relier un telephone (code QR) et gerer les appareils.

Le code n'est valable que tant que la fenetre est ouverte : a la fermeture,
le jeton non utilise est oublie (voir PhoneLink.end_pairing).
"""

from __future__ import annotations

import segno
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..phone import PhoneLink
from .theme import palette
from .widgets import make_app_icon

CONTENT_WIDTH = 384

STATUS_TEXT = {
    "off": "Aucun téléphone relié",
    "connecting": "Connexion au relais…",
    "online": "Relais connecté",
    "waiting": "Relais injoignable, nouvel essai automatique…",
}


class QrCode(QWidget):
    """Code QR net a toute echelle, sur carte blanche, avec l'icone de Vox."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._matrix: list[bytes] = []
        self._icon = make_app_icon(128).pixmap(128, 128)
        self.setFixedSize(236, 236)

    def set_data(self, data: str) -> None:
        # Correction maximale (« h ») : l'icone au centre ne gene pas la lecture.
        code = segno.make(data, error="h", boost_error=False, micro=False)
        self._matrix = [bytes(row) for row in code.matrix]
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(236, 236)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        card = QPainterPath()
        card.addRoundedRect(rect, 18, 18)
        painter.fillPath(card, QColor("#ffffff"))
        if not self._matrix:
            return

        count = len(self._matrix)
        quiet = 3  # zone de silence (en modules), complete par l'arrondi
        module = rect.width() / (count + 2 * quiet)
        origin = quiet * module
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#0c0e13"))
        for y, row in enumerate(self._matrix):
            for x, bit in enumerate(row):
                if bit:
                    # Leger debordement : pas de liseres blancs entre modules.
                    painter.drawRect(
                        QRectF(origin + x * module, origin + y * module, module + 0.3, module + 0.3)
                    )

        side = rect.width() * 0.2
        badge = QRectF((rect.width() - side) / 2, (rect.height() - side) / 2, side, side)
        frame = QPainterPath()
        frame.addRoundedRect(badge.adjusted(-5, -5, 5, 5), 12, 12)
        painter.fillPath(frame, QColor("#ffffff"))
        painter.drawPixmap(badge.toRect(), self._icon)
        painter.end()


class _Check(QWidget):
    """Pastille verte « relie »."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(64, 64)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(62, 207, 142, 40))
        painter.drawEllipse(self.rect())
        pen = QPen(QColor("#3ecf8e"), 4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath()
        path.moveTo(20, 33)
        path.lineTo(28.5, 41)
        path.lineTo(44, 24)
        painter.drawPath(path)
        painter.end()


class _PhoneRow(QFrame):
    remove_requested = Signal(str)

    def __init__(self, phone: dict, online: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("phoneRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(10)

        dot = QLabel()
        dot.setObjectName("dotOnline" if online else "dotOffline")
        dot.setFixedSize(8, 8)
        layout.addWidget(dot, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(0)
        name = QLabel(phone.get("name") or "Téléphone")
        name.setObjectName("phoneName")
        status = QLabel("En ligne" if online else "Hors ligne")
        status.setObjectName("phoneStatus")
        text.addWidget(name)
        text.addWidget(status)
        layout.addLayout(text, 1)

        remove = QPushButton("Retirer")
        remove.setObjectName("quiet")
        remove.setCursor(Qt.PointingHandCursor)
        remove.clicked.connect(lambda: self.remove_requested.emit(phone["id"]))
        layout.addWidget(remove, 0, Qt.AlignVCenter)


class PhoneWindow(QWidget):
    """Appairage et liste des telephones relies."""

    def __init__(self, link: PhoneLink, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.link = link
        self._url = ""
        self.setWindowTitle("Téléphone — Vox")
        self.setWindowIcon(make_app_icon())
        self._build()
        self._style()

        link.paired.connect(self._on_paired)
        link.phones_changed.connect(self._refresh_list)
        link.state_changed.connect(self._refresh_status)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 26, 28, 22)
        outer.setSpacing(0)
        # La fenetre epouse son contenu (code, confirmation, liste) sans jamais
        # comprimer les espacements.
        outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        title = QLabel("Téléphone")
        title.setObjectName("phoneTitle")
        outer.addWidget(title)
        lead = QLabel("Dicte depuis ton téléphone : le texte s’écrit ici, là où se trouve ton curseur.")
        lead.setObjectName("phoneLead")
        lead.setWordWrap(True)
        lead.setFixedWidth(CONTENT_WIDTH)
        outer.addSpacing(4)
        outer.addWidget(lead)
        outer.addSpacing(26)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_code())
        self.stack.addWidget(self._build_success())
        outer.addWidget(self.stack)

        outer.addSpacing(22)
        self.list_title = QLabel("TÉLÉPHONES RELIÉS")
        self.list_title.setObjectName("panelTitle")
        outer.addWidget(self.list_title)
        outer.addSpacing(8)
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(6)
        outer.addLayout(self.list_box)

        outer.addSpacing(18)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(7, 7)
        footer.addWidget(self.status_dot, 0, Qt.AlignVCenter)
        self.status_label = QLabel()
        self.status_label.setObjectName("phoneStatus")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        self.retry_button = QPushButton("Relancer la connexion")
        self.retry_button.setToolTip("Reconnecte Vox sans quitter ni refaire l’appairage.")
        self.retry_button.clicked.connect(self.link.reconnect)
        footer.addWidget(self.retry_button)
        outer.addLayout(footer)

    def _build_code(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.qr = QrCode()
        layout.addWidget(self.qr, 0, Qt.AlignHCenter)
        caption = QLabel(
            "Scanne ce code depuis l’app Vox du téléphone, ou avec l’appareil photo la "
            "première fois."
        )
        caption.setObjectName("phoneCaption")
        caption.setAlignment(Qt.AlignCenter)
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self.copy_button = QPushButton("Copier le lien")
        self.copy_button.setObjectName("quiet")
        self.copy_button.setCursor(Qt.PointingHandCursor)
        self.copy_button.clicked.connect(self._copy_link)
        layout.addWidget(self.copy_button, 0, Qt.AlignHCenter)
        return page

    def _build_success(self) -> QWidget:
        page = QWidget()
        page.setMinimumHeight(self.qr.height() + 60)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 18, 0, 0)
        layout.setSpacing(12)
        layout.addStretch(1)
        layout.addWidget(_Check(), 0, Qt.AlignHCenter)
        self.success_title = QLabel()
        self.success_title.setObjectName("successTitle")
        self.success_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.success_title)
        hint = QLabel(
            "Sur le téléphone, ajoute la page à l’écran d’accueil pour l’ouvrir d’un geste."
        )
        hint.setObjectName("phoneCaption")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(6)
        again = QPushButton("Relier un autre téléphone")
        again.setCursor(Qt.PointingHandCursor)
        again.clicked.connect(self.new_code)
        layout.addWidget(again, 0, Qt.AlignHCenter)
        layout.addStretch(1)
        return page

    def _style(self) -> None:
        c = palette("dark")
        self.setStyleSheet(
            f"""
            QLabel#phoneTitle {{ font-size: 20px; font-weight: 700; }}
            QLabel#phoneLead {{ color: {c['muted']}; font-size: 13px; }}
            QLabel#phoneCaption {{ color: {c['muted']}; font-size: 13px; }}
            QLabel#successTitle {{ font-size: 16px; font-weight: 600; }}
            QLabel#phoneName {{ font-size: 13px; font-weight: 600; }}
            QLabel#phoneStatus {{ color: {c['muted']}; font-size: 12px; }}
            QLabel#phoneEmpty {{ color: {c['muted']}; font-size: 12px; }}
            QLabel#dotOnline {{ background: {c['success']}; border-radius: 4px; }}
            QLabel#dotOffline {{ background: {c['border_strong']}; border-radius: 4px; }}
            QFrame#phoneRow {{
                background: {c['input_bg']};
                border: 1px solid {c['border']};
                border-radius: 10px;
            }}
            QPushButton#quiet {{
                background: transparent;
                border: none;
                color: {c['accent']};
                padding: 4px 8px;
            }}
            QPushButton#quiet:hover {{ color: {c['wave_hi']}; }}
            """
        )

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._url:
            self.new_code()
        self._refresh_list()
        self._refresh_status(self.link.state)

    def closeEvent(self, event) -> None:
        self.link.end_pairing()
        self._url = ""
        super().closeEvent(event)

    def new_code(self) -> None:
        self._url = self.link.begin_pairing()
        self.qr.set_data(self._url)
        self.copy_button.setText("Copier le lien")
        self.stack.setCurrentIndex(0)

    def _copy_link(self) -> None:
        if self._url:
            QGuiApplication.clipboard().setText(self._url)
            self.copy_button.setText("Lien copié")
            QTimer.singleShot(1800, lambda: self.copy_button.setText("Copier le lien"))

    def _on_paired(self, name: str) -> None:
        self.success_title.setText(f"{name or 'Le téléphone'} est relié")
        self.stack.setCurrentIndex(1)
        self._url = ""
        self._refresh_list()
        self.raise_()
        self.activateWindow()

    def _refresh_list(self) -> None:
        while self.list_box.count():
            item = self.list_box.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        phones = self.link.phones
        if not phones:
            empty = QLabel("Aucun pour l’instant.")
            empty.setObjectName("phoneEmpty")
            self.list_box.addWidget(empty)
        for phone in phones:
            row = _PhoneRow(phone, self.link.is_online(phone["id"]))
            row.remove_requested.connect(self.link.remove_phone)
            row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            self.list_box.addWidget(row)

    def _refresh_status(self, state: str) -> None:
        self.status_label.setText(STATUS_TEXT.get(state, ""))
        self.retry_button.setEnabled(self.link.wanted)
        c = palette("dark")
        color = {"online": c["success"], "waiting": c["warn"], "connecting": c["warn"]}.get(
            state, c["border_strong"]
        )
        self.status_dot.setStyleSheet(f"background: {color}; border-radius: 3px;")
