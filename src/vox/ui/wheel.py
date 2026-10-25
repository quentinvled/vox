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

from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication
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
    """QComboBox : la molette ne change jamais la valeur.

    Liste fermee, la molette fait defiler la page qui contient le champ. Liste
    ouverte, c'est la **liste** qui defile : Qt ignore volontairement la
    molette du champ dans ce cas, donc on la redirige explicitement vers la vue.

    La hauteur de la liste est aussi plafonnee (`MAX_VISIBLE_ROWS`). Sans cela,
    Qt l'etire sur tout l'ecran des qu'il y a beaucoup d'entrees — 28 modeles de
    transcription suffisent — et l'on ne voit plus ou l'on en est.
    """

    # Nombre de lignes visibles avant que la liste ne defile.
    MAX_VISIBLE_ROWS = 12
    # QWIDGETSIZE_MAX : valeur Qt « aucune limite ».
    _NO_LIMIT = 16777215

    def wheelEvent(self, event) -> None:  # noqa: N802 - API Qt
        # La liste est ouverte : la molette la fait defiler, jamais la page.
        if self.view().isVisible():
            self._wheel_popup(event)
            return
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

    def showPopup(self) -> None:  # noqa: N802 - API Qt
        container = self.view().parentWidget()
        if container is not None:
            # La hauteur est recalculee a chaque ouverture : on repart de zero.
            container.setMaximumHeight(self._NO_LIMIT)
            container.setMinimumHeight(0)
        super().showPopup()
        self._limit_popup()

    def _limit_popup(self) -> None:
        """Plafonne la hauteur de la liste a `MAX_VISIBLE_ROWS` lignes.

        Qt dimensionne le menu sur la liste **entiere** (28 modeles suffisent a
        remplir l'ecran). On reduit la fenetre du menu apres son ouverture, puis
        on la replace : sans cela elle resterait collee en haut de l'ecran.
        """
        view = self.view()
        container = view.parentWidget()
        if container is None:
            return
        rows = view.model().rowCount() if view.model() is not None else 0
        row = view.sizeHintForRow(0) if rows else 0
        if row <= 0:
            row = self.fontMetrics().height() + 12
        target = row * self.MAX_VISIBLE_ROWS + 2 * view.frameWidth() + 16
        if container.height() <= target:
            return
        container.setFixedHeight(target)
        self._place_popup(container)

    def _place_popup(self, container) -> None:
        """Place le menu sous le champ, ou au-dessus s'il n'y a pas la place."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        height = container.height()
        bottom = self.mapToGlobal(QPoint(0, self.height())).y()
        top = self.mapToGlobal(QPoint(0, 0)).y()
        y = bottom
        if y + height > area.bottom():
            y = top - height
            if y < area.top():
                y = max(area.top(), area.bottom() - height)
        x = min(container.x(), area.right() - container.width())
        x = max(x, area.left())
        container.move(x, y)

    def _wheel_popup(self, event) -> None:
        """Fait defiler la liste ouverte, en gardant les proportions habituelles."""
        view = self.view()
        bar = view.verticalScrollBar()
        delta = event.angleDelta().y()
        if not delta or bar.maximum() <= bar.minimum():
            # Rien a faire defiler : l'evenement continue sa route.
            event.ignore()
            return
        row = view.sizeHintForRow(0) if view.model() is not None and view.model().rowCount() else 0
        if row <= 0:
            row = self.fontMetrics().height() + 6
        lines = QApplication.wheelScrollLines()
        if lines < 0:
            lines = 3
        step = max(1, int(delta / 120.0 * lines * row))
        bar.setValue(bar.value() - step)
        event.accept()


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
