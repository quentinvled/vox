"""Palettes et feuille de style (QSS) de l'application."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

from ..paths import data_dir

PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "card": "rgba(20, 22, 27, 244)",
        "card_alt": "rgba(31, 34, 41, 255)",
        "border": "rgba(255, 255, 255, 24)",
        "border_strong": "rgba(255, 255, 255, 42)",
        "text": "#f3f5f9",
        "muted": "#949cad",
        "accent": "#6d9dfb",
        "accent_soft": "rgba(109, 157, 251, 38)",
        "danger": "#f87171",
        "success": "#4ade80",
        "wave_lo": "#4b7bd6",
        "wave_hi": "#7fd4ff",
        "input_bg": "rgba(255, 255, 255, 14)",
        "hover": "rgba(255, 255, 255, 20)",
    },
    "light": {
        "card": "rgba(255, 255, 255, 248)",
        "card_alt": "rgba(244, 246, 250, 255)",
        "border": "rgba(15, 23, 42, 22)",
        "border_strong": "rgba(15, 23, 42, 45)",
        "text": "#141821",
        "muted": "#5d6779",
        "accent": "#2f6bd8",
        "accent_soft": "rgba(47, 107, 216, 30)",
        "danger": "#c02b2b",
        "success": "#15803d",
        "wave_lo": "#5b8def",
        "wave_hi": "#8fb8ff",
        "input_bg": "rgba(15, 23, 42, 10)",
        "hover": "rgba(15, 23, 42, 14)",
    },
}


def palette(theme: str) -> dict[str, str]:
    return PALETTES.get(theme, PALETTES["dark"])


def _check_icon_url() -> str:
    """Genere (une fois) un pictogramme de coche et renvoie son chemin QSS."""
    path = data_dir() / "check.png"
    if not path.exists():
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#ffffff"), 2.1)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(
            [QPointF(3.6, 8.4), QPointF(6.7, 11.4), QPointF(12.4, 4.8)]
        )
        painter.end()
        pixmap.save(str(path), "PNG")
    return path.as_posix()


def app_qss(theme: str) -> str:
    c = palette(theme)
    check_icon = _check_icon_url()
    return f"""
    QWidget {{
        color: {c['text']};
        font-family: "Segoe UI Variable Display", "Segoe UI", sans-serif;
        font-size: 13px;
    }}

    /* ---------- Carte de l'overlay ---------- */
    QFrame#card {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 24px;
    }}
    QLabel#overlayTitle {{
        font-size: 15px;
        font-weight: 600;
        color: {c['text']};
    }}
    QLabel#overlaySubtitle {{
        font-size: 12px;
        color: {c['muted']};
    }}
    QLabel#overlaySubtitleError {{
        font-size: 12px;
        color: {c['danger']};
    }}

    /* ---------- Puce (chip) ---------- */
    QPushButton#chip {{
        background: {c['accent_soft']};
        border: 1px solid {c['border']};
        border-radius: 12px;
        padding: 4px 10px;
        font-size: 11px;
        color: {c['text']};
    }}
    QPushButton#chip:hover {{ background: {c['hover']}; }}
    QPushButton#chip:checked {{
        background: {c['accent']};
        color: #ffffff;
        border-color: transparent;
        font-weight: 600;
    }}
    QPushButton#iconButton {{
        background: transparent;
        border: none;
        border-radius: 12px;
        padding: 0px;
        color: {c['muted']};
        font-size: 15px;
    }}
    QPushButton#iconButton:hover {{ background: {c['hover']}; color: {c['text']}; }}

    /* ---------- Fenetre de reglages ---------- */
    QDialog {{ background: {c['card_alt']}; }}
    QGroupBox {{
        border: 1px solid {c['border']};
        border-radius: 14px;
        margin-top: 14px;
        padding: 16px 14px 12px 14px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 14px;
        padding: 0 6px;
        color: {c['muted']};
        font-size: 11px;
        text-transform: uppercase;
    }}
    QLabel#hint {{ color: {c['muted']}; font-size: 11px; }}
    QLabel#sectionTitle {{ font-size: 15px; font-weight: 700; }}
    QLabel#error {{ color: {c['danger']}; font-size: 11px; }}
    QLabel#success {{ color: {c['success']}; font-size: 11px; }}

    QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {c['input_bg']};
        border: 1px solid {c['border']};
        border-radius: 9px;
        padding: 6px 9px;
        selection-background-color: {c['accent']};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QComboBox:focus {{
        border: 1px solid {c['accent']};
    }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{
        background: {c['card_alt']};
        border: 1px solid {c['border']};
        border-radius: 9px;
        padding: 4px;
        selection-background-color: {c['accent_soft']};
        outline: none;
    }}

    QPushButton {{
        background: {c['input_bg']};
        border: 1px solid {c['border']};
        border-radius: 9px;
        padding: 7px 14px;
    }}
    QPushButton:hover {{ background: {c['hover']}; }}
    QPushButton#primary {{
        background: {c['accent']};
        color: #ffffff;
        border: none;
        font-weight: 600;
    }}
    QPushButton#primary:hover {{ background: {c['wave_hi']}; }}

    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {c['border_strong']};
        border-radius: 5px;
        background: {c['input_bg']};
    }}
    QCheckBox::indicator:checked {{
        background: {c['accent']};
        border-color: {c['accent']};
        image: url("{check_icon}");
    }}

    QScrollArea {{ border: none; background: transparent; }}

    /* ---------- Statistiques ---------- */
    QFrame#kpi {{
        background: {c['input_bg']};
        border: 1px solid {c['border']};
        border-radius: 14px;
    }}
    QFrame#kpiAccent {{
        background: {c['accent_soft']};
        border: 1px solid {c['accent']};
        border-radius: 14px;
    }}
    QLabel#kpiTitle {{ color: {c['muted']}; font-size: 10px; font-weight: 600; }}
    QLabel#kpiValue {{ color: {c['text']}; font-size: 21px; font-weight: 600; }}
    QLabel#kpiDetail {{ color: {c['muted']}; font-size: 11px; }}
    QFrame#panel {{
        background: {c['input_bg']};
        border: 1px solid {c['border']};
        border-radius: 14px;
    }}
    QLabel#panelTitle {{ color: {c['muted']}; font-size: 11px; font-weight: 600; }}
    QLabel#big {{ font-size: 15px; font-weight: 700; }}
    QScrollBar:vertical {{
        background: transparent; width: 9px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border_strong']}; border-radius: 4px; min-height: 30px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QMenu {{
        background: {c['card_alt']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        padding: 5px;
    }}
    QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 7px; }}
    QMenu::item:selected {{ background: {c['accent_soft']}; }}
    QMenu::separator {{ height: 1px; background: {c['border']}; margin: 5px 8px; }}
    """
