"""Molette : elle defile la page, jamais la valeur ; la liste ouverte defile seule."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget

from vox.ui.wheel import NoWheelComboBox


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _page(qapp, count: int = 28):
    area = QScrollArea()
    area.setWidgetResizable(True)
    page = QWidget()
    layout = QVBoxLayout(page)
    combo = NoWheelComboBox()
    combo.addItems([f"Modèle {index}" for index in range(count)])
    layout.addWidget(combo)
    for index in range(60):
        layout.addWidget(QLabel(f"ligne {index}"))
    area.setWidget(page)
    area.resize(700, 400)
    area.show()
    qapp.processEvents()
    return area, combo


def _wheel(widget, target: QPoint) -> QWheelEvent:
    return QWheelEvent(
        QPointF(target),
        QPointF(widget.mapToGlobal(target)),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase,
        False,
    )


def test_liste_ouverte_ne_depasse_pas_douze_lignes(qapp):
    area, combo = _page(qapp)
    try:
        combo.showPopup()
        qapp.processEvents()
        view = combo.view()
        row = view.sizeHintForRow(0)
        container = view.parentWidget()

        # Sans plafond, Qt etire la liste sur tout l'ecran des qu'il y a
        # beaucoup d'entrees (c'est le defaut qui rendait la liste illisible).
        assert container.height() <= row * NoWheelComboBox.MAX_VISIBLE_ROWS + 40
        assert view.verticalScrollBar().maximum() > 0
    finally:
        combo.hidePopup()
        area.close()


def test_liste_ouverte_sous_le_champ(qapp):
    """Le menu doit rester accole au champ, pas se retrouver en haut de l'ecran."""
    area, combo = _page(qapp)
    try:
        combo.showPopup()
        qapp.processEvents()
        container = combo.view().parentWidget()
        combo_bottom = combo.mapToGlobal(combo.rect().bottomLeft()).y()

        assert container.y() >= combo_bottom - 40
        assert container.y() <= combo_bottom + 40
    finally:
        combo.hidePopup()
        area.close()


def test_molette_sur_la_liste_ouverte_la_fait_defiler(qapp):
    area, combo = _page(qapp)
    try:
        combo.showPopup()
        qapp.processEvents()
        view = combo.view()
        bar = view.verticalScrollBar()
        page_bar = area.verticalScrollBar()
        page_before = page_bar.value()

        viewport = view.viewport()
        QApplication.sendEvent(viewport, _wheel(viewport, viewport.rect().center()))
        qapp.processEvents()

        assert bar.value() > 0
        assert page_bar.value() == page_before
        assert combo.currentIndex() == 0
    finally:
        combo.hidePopup()
        area.close()


def test_molette_sur_le_champ_ferme_fait_defiler_la_page(qapp):
    area, combo = _page(qapp)
    try:
        page_bar = area.verticalScrollBar()
        assert page_bar.value() == 0

        QApplication.sendEvent(combo, _wheel(combo, combo.rect().center()))
        qapp.processEvents()

        assert page_bar.value() > 0
        assert combo.currentIndex() == 0
    finally:
        area.close()
