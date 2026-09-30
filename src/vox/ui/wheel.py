"""Widgets insensibles a la molette : elle fait toujours defiler la page.

Sans cela, faire defiler une page de reglages modifie le champ qui se trouve
sous le curseur : tres desagreable, et source d'erreurs silencieuses. Le champ
changeait meme quand il avait le focus — or Qt donne le focus au premier champ
des l'ouverture, donc un simple scroll pouvait modifier un reglage sans qu'on
s'en apercoive. La valeur ne change desormais plus **jamais** a la molette : on
la modifie en cliquant (fleches, liste deroulante, saisie).

On ne se contente pas d'ignorer l'evenement : selon le contexte, Qt ne le
propage pas toujours au `QScrollArea` parent, et la page se retrouve bloquee des
que le curseur survole un champ. On agit donc directement sur la barre de
defilement du conteneur, ce qui garantit que la page defile normalement tout en
laissant la valeur intacte.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QSlider,
    QSpinBox,
)


class _NoWheelMixin:
    def _scroll_area(self) -> QAbstractScrollArea | None:
        """Premier conteneur defilable englobant (le QScrollArea des onglets)."""
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def _wheel_pixels(self, delta: int) -> int:
        """Convertit un delta de molette (1 cran = 120) en pixels a defiler.

        On imite le comportement standard : nombre de lignes configure par le
        systeme (3 par defaut) multiplie par la hauteur d'une ligne.
        """
        lines = QApplication.wheelScrollLines()
        if lines < 0:
            lines = 3
        line = self.fontMetrics().height() or 16
        return int(delta / 120.0 * lines * line)

    def wheelEvent(self, event) -> None:  # noqa: N802 - API Qt
        # On ne delegue jamais au widget de base : meme focalise, il ne doit pas
        # changer de valeur a la molette. Seul le focus clavier (fleches, saisie)
        # ou un clic modifie le champ.
        area = self._scroll_area()
        if area is None:
            event.ignore()
            return
        delta = event.angleDelta()
        if delta.y():
            bar = area.verticalScrollBar()
            bar.setValue(bar.value() - self._wheel_pixels(delta.y()))
        if delta.x():
            bar = area.horizontalScrollBar()
            bar.setValue(bar.value() - self._wheel_pixels(delta.x()))
        event.accept()


class NoWheelComboBox(_NoWheelMixin, QComboBox):
    """QComboBox : la molette fait defiler la page, pas la selection."""


class NoWheelSpinBox(_NoWheelMixin, QSpinBox):
    """QSpinBox : la molette fait defiler la page, pas la valeur."""


class NoWheelDoubleSpinBox(_NoWheelMixin, QDoubleSpinBox):
    """QDoubleSpinBox : la molette fait defiler la page, pas la valeur."""


class NoWheelSlider(_NoWheelMixin, QSlider):
    """QSlider : la molette fait defiler la page, pas la position."""


__all__ = [
    "NoWheelComboBox",
    "NoWheelDoubleSpinBox",
    "NoWheelSlider",
    "NoWheelSpinBox",
]
