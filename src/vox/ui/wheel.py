"""Widgets insensibles a la molette tant qu'ils n'ont pas le focus.

Sans cela, faire defiler un onglet des reglages modifie le combo ou le champ
qui se trouve sous le curseur : tres desagreable, et source d'erreurs
silencieuses (on croit avoir fait defiler, on a change un reglage).

Comportement voulu : la molette fait defiler la page, sauf si le champ a ete
clique au prealable (il a alors le focus) ; dans ce cas elle ajuste la valeur,
comme partout ailleurs.

L'evenement est *ignore* plutot que consomme : il remonte alors jusqu'au
`QScrollArea` parent, et la page defile normalement sous le curseur.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSlider, QSpinBox


class _NoWheelMixin:
    def wheelEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelComboBox(_NoWheelMixin, QComboBox):
    """QComboBox qui n'obéit qu'à la molette quand il a le focus."""


class NoWheelSpinBox(_NoWheelMixin, QSpinBox):
    """QSpinBox qui n'obéit qu'à la molette quand il a le focus."""


class NoWheelDoubleSpinBox(_NoWheelMixin, QDoubleSpinBox):
    """QDoubleSpinBox qui n'obéit qu'à la molette quand il a le focus."""


class NoWheelSlider(_NoWheelMixin, QSlider):
    """QSlider qui n'obéit qu'à la molette quand il a le focus."""


__all__ = [
    "NoWheelComboBox",
    "NoWheelDoubleSpinBox",
    "NoWheelSlider",
    "NoWheelSpinBox",
]
