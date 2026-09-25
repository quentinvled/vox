"""Fenetre Statistiques : usage, temps gagne, modeles, depenses."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_module
from ..api import Client
from ..config import Settings
from ..stats import (
    DEFAULT_TYPING_WPM,
    Stats,
    compute,
    format_duration,
    format_money,
)
from .charts import DailyChart, KpiCard, ShareBars, StatRow
from .widgets import Chip


class _UsageFetcher(QThread):
    """Recupere le quota consomme sur la cle, hors du thread d'interface."""

    fetched = Signal(bool, dict, str)

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings

    def run(self) -> None:
        key = config_module.resolve_api_key(self._settings)
        if not key:
            self.fetched.emit(False, {}, "aucune clé configurée")
            return
        try:
            info = Client(self._settings.provider, key).check_key()
        except Exception as exc:
            self.fetched.emit(False, {}, str(exc))
            return
        self.fetched.emit(True, info, "")


class StatsWindow(QWidget):
    """Tableau de bord d'usage."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.stats = Stats()
        self._theme = settings.theme
        self._fetcher: _UsageFetcher | None = None

        self.setWindowTitle("Statistiques — Vox")
        self.setMinimumSize(800, 660)
        self._build()
        self.refresh()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Statistiques")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.period_label = QLabel("")
        self.period_label.setObjectName("hint")
        header.addWidget(self.period_label)
        self.refresh_button = QPushButton("Actualiser")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 8, 0)
        page_layout.setSpacing(12)
        scroll.setWidget(page)
        outer.addWidget(scroll, 1)

        # --- indicateurs principaux ---
        self.card_saved = KpiCard("Temps gagné", accent=True)
        self.card_words = KpiCard("Mots écrits")
        self.card_listen = KpiCard("Temps d'écoute")
        self.card_cost = KpiCard("Coût")
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        for card in (self.card_saved, self.card_words, self.card_listen, self.card_cost):
            kpi_row.addWidget(card)
        page_layout.addLayout(kpi_row)

        # --- activite ---
        activity_frame, activity = self._make_panel("Activité — 30 derniers jours")
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.metric_chips: dict[str, Chip] = {}
        for key, label in (("words", "Mots"), ("dictations", "Dictées"), ("seconds", "Temps")):
            chip = Chip(label, checkable=True)
            chip.setChecked(key == "words")
            chip.clicked.connect(lambda _=False, k=key: self._select_metric(k))
            toolbar.addWidget(chip)
            self.metric_chips[key] = chip
        toolbar.addStretch(1)
        activity.addLayout(toolbar)
        self.chart = DailyChart()
        activity.addWidget(self.chart)
        page_layout.addWidget(activity_frame)

        # --- modeles + qualite, cote a cote ---
        middle = QHBoxLayout()
        middle.setSpacing(12)

        models_frame, models = self._make_panel("Modèles utilisés")
        self.models_bars = ShareBars()
        models.addWidget(self.models_bars)
        models.addStretch(1)
        middle.addWidget(models_frame, 3)

        quality_frame, quality = self._make_panel("Qualité & rythme")
        self.row_median = StatRow("Latence médiane")
        self.row_p95 = StatRow("Latence p95")
        self.row_wpm = StatRow("Débit de parole")
        self.row_typing = StatRow("Vitesse de frappe réf.")
        self.row_dict = StatRow("Dictées")
        self.row_active = StatRow("Jours actifs")
        self.row_streak = StatRow("Série en cours")
        self.row_best = StatRow("Meilleure journée")
        for row in (
            self.row_median,
            self.row_p95,
            self.row_wpm,
            self.row_typing,
            self.row_dict,
            self.row_active,
            self.row_streak,
            self.row_best,
        ):
            quality.addWidget(row)
        quality.addStretch(1)
        middle.addWidget(quality_frame, 2)
        page_layout.addLayout(middle)

        # --- depenses ---
        spend_frame, spend = self._make_panel("Clé OpenRouter")
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(3)
        self.spend_usage = QLabel("—")
        self.spend_usage.setObjectName("big")
        self.spend_remaining = QLabel("—")
        self.spend_remaining.setObjectName("big")
        self.spend_local = QLabel("—")
        self.spend_local.setObjectName("big")
        grid.addWidget(QLabel("Utilisé sur le compte"), 0, 0)
        grid.addWidget(self.spend_usage, 0, 1)
        grid.addWidget(QLabel("Restant"), 1, 0)
        grid.addWidget(self.spend_remaining, 1, 1)
        grid.addWidget(QLabel("Mesuré localement"), 2, 0)
        grid.addWidget(self.spend_local, 2, 1)
        spend.addLayout(grid)
        self.spend_status = QLabel("")
        self.spend_status.setObjectName("hint")
        self.spend_status.setWordWrap(True)
        spend.addWidget(self.spend_status)
        page_layout.addWidget(spend_frame)

        note = QLabel(
            "Le « temps gagné » compare le temps qu'il aurait fallu pour taper le "
            "même texte (à 40 mots/minute) au temps réellement passé à dicter, "
            "latence de transcription incluse. Le « mesuré localement » ne compte "
            "que les dictées faites avec Vox : s'il est inférieur au total du "
            "compte, la différence vient d'autres usages de la même clé."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        page_layout.addWidget(note)

        page_layout.addStretch(1)

    def _make_panel(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        """Cree un panneau et renvoie (cadre, mise en page interne)."""
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)
        label = QLabel(title.upper())
        label.setObjectName("panelTitle")
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(label)
        return frame, layout

    # ------------------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        self.chart.set_theme(theme)
        self.models_bars.set_theme(theme)

    def _select_metric(self, metric: str) -> None:
        for key, chip in self.metric_chips.items():
            chip.setChecked(key == metric)
        self.chart.set_metric(metric)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        stats = self.stats = compute(typing_wpm=DEFAULT_TYPING_WPM)

        self.card_saved.set(
            format_duration(stats.saved_seconds),
            f"×{stats.saved_ratio:.1f} plus rapide" if stats.saved_ratio else "pas encore de données",
        )
        self.card_words.set(
            f"{stats.words:,}".replace(",", " "),
            f"{stats.characters:,}".replace(",", " ") + " caractères",
        )
        self.card_listen.set(format_duration(stats.audio_seconds), f"{stats.entries} dictée(s)")
        self.card_cost.set(
            format_money(stats.cost),
            f"{stats.cost_per_hour:.3f} $/h" if stats.audio_seconds else "—",
        )

        if stats.first_at and stats.last_at:
            self.period_label.setText(
                f"du {stats.first_at.strftime('%d/%m/%Y')} au {stats.last_at.strftime('%d/%m/%Y')}"
            )
        else:
            self.period_label.setText("aucune dictée enregistrée")

        self.chart.set_days(stats.days)

        total = stats.entries or 1
        rows = [
            (
                model.model.split("/")[-1],
                model.dictations / total,
                f"{model.dictations} · {format_money(model.cost)}",
            )
            for model in stats.models[:6]
        ]
        self.models_bars.set_rows(rows)

        self.row_median.set(f"{stats.median_latency_ms} ms")
        self.row_p95.set(f"{stats.p95_latency_ms} ms")
        self.row_wpm.set(f"{stats.speaking_wpm:.0f} mots/min")
        self.row_typing.set(f"{DEFAULT_TYPING_WPM:.0f} mots/min")
        self.row_dict.set(str(stats.entries))
        self.row_active.set(str(stats.active_days))
        self.row_streak.set(f"{stats.streak} jour(s)")
        best = stats.best_day
        self.row_best.set(f"{best.words} mots le {best.day.strftime('%d/%m')}" if best else "—")

        self.spend_local.setText(format_money(stats.cost))
        self._fetch_usage()

    # ------------------------------------------------------------------
    def _fetch_usage(self) -> None:
        if not config_module.resolve_api_key(self.settings):
            self.spend_usage.setText("—")
            self.spend_remaining.setText("—")
            self.spend_status.setText("Aucune clé configurée : renseigne-la dans les réglages.")
            return
        self.spend_status.setText("Interrogation du compte…")
        self._fetcher = _UsageFetcher(self.settings, self)
        self._fetcher.fetched.connect(self._on_usage)
        self._fetcher.start()

    def _on_usage(self, ok: bool, info: dict, error: str) -> None:
        if not ok:
            self.spend_usage.setText("—")
            self.spend_remaining.setText("—")
            self.spend_status.setText(f"Compte indisponible : {error}")
            return

        usage = float(info.get("usage") or 0)
        limit = info.get("limit")
        remaining = info.get("limit_remaining")
        reset = info.get("limit_reset") or ""

        if limit:
            share = usage / float(limit) * 100 if float(limit) else 0
            self.spend_usage.setText(f"{format_money(usage)} / {format_money(float(limit))}")
            self.spend_status.setText(f"Quota {reset} · {share:.0f} % consommé")
        else:
            self.spend_usage.setText(format_money(usage))
            self.spend_status.setText("Pas de quota mensuel sur cette clé.")
        self.spend_remaining.setText(
            format_money(float(remaining)) if remaining is not None else "—"
        )
