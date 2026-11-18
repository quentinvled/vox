"""Génère les icônes PNG de la page du téléphone à partir de web/icons/icon.svg.

À relancer seulement si l'icône change, depuis la racine du dépôt Vox (qui
fournit PySide6) :

    uv run python relay/tools/make_icons.py
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ICONS = Path(__file__).resolve().parent.parent / "web" / "icons"


def _render(svg: str, size: int, path: Path, inset: float = 0.0) -> None:
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    margin = size * inset
    renderer.render(painter, QRectF(margin, margin, size - 2 * margin, size - 2 * margin))
    painter.end()
    image.save(str(path), "PNG")


def main() -> None:
    QGuiApplication([])
    rounded = (ICONS / "icon.svg").read_text(encoding="utf-8")
    # Fond plein (sans coins arrondis) : iOS et Android découpent eux-mêmes.
    square = re.sub(r'rx="58"', 'rx="0"', rounded)
    # Version « maskable » : les barres réduites dans la zone sûre (80 %).
    maskable = square.replace(
        '<g fill="#fff">', '<g fill="#fff" transform="translate(25.6 25.6) scale(0.8)">'
    )

    _render(rounded, 192, ICONS / "icon-192.png")
    _render(rounded, 512, ICONS / "icon-512.png")
    _render(square, 180, ICONS / "apple-touch-icon.png")
    _render(maskable, 512, ICONS / "maskable-512.png")
    print("Icônes écrites dans", ICONS)


if __name__ == "__main__":
    main()
