"""Tableau de bord : usage, temps gagne, modeles, depenses, historique.

Tout est calcule localement a partir de `history.jsonl` (une ligne par dictee
reussie) et de l'index des enregistrements. La seule donnee distante est le
quota consomme sur la cle OpenRouter, recupere a la demande.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QThread, Signal
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
from .. import recordings as recordings_module
from ..api import Client
from ..config import Settings
from ..stats import (
    DEFAULT_TYPING_WPM,
    Stats,
    compute,
    format_duration,
    format_money,
)
from .charts import DailyChart, HourlyChart, KpiCard, ShareBars, StatRow
from .widgets import Chip
from .wheel import NoWheelComboBox

LANGUAGES = {
    "fr": "Français", "en": "Anglais", "es": "Espagnol", "de": "Allemand",
    "it": "Italien", "pt": "Portugais", "nl": "Néerlandais", "ru": "Russe",
    "zh": "Chinois", "ja": "Japonais", "ko": "Coréen", "ar": "Arabe",
}

PERIODS: list[tuple[str, str]] = [
    ("7", "7 jours"),
    ("30", "30 jours"),
    ("90", "90 jours"),
    ("all", "Tout"),
]

TONES = {
    "clean": "Nettoyage", "concise": "Concis", "formal": "Formel",
    "casual": "Décontracté", "bullet": "Liste", "custom": "Personnalisé",
}


def _num(value: float) -> str:
    return f"{int(value):,}".replace(",", " ")


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

    history_requested = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.stats = Stats()
        self._theme = settings.theme
        self._fetcher: _UsageFetcher | None = None
        self._period = "30"

        self.setWindowTitle("Tableau de bord — Vox")
        self.setMinimumSize(920, 720)
        self._build()
        self.refresh()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Tableau de bord")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.period_combo = NoWheelComboBox()
        for key, label in PERIODS:
            self.period_combo.addItem(label, key)
        self.period_combo.setCurrentIndex(1)
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        header.addWidget(self.period_combo)
        self.history_button = QPushButton("Historique")
        self.history_button.setToolTip("Ouvrir la liste des enregistrements (audio + texte)")
        self.history_button.clicked.connect(self.history_requested.emit)
        header.addWidget(self.history_button)
        self.refresh_button = QPushButton("Actualiser")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        outer.addLayout(header)

        self.period_label = QLabel("")
        self.period_label.setObjectName("hint")
        outer.addWidget(self.period_label)

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
        self.card_cost = KpiCard("Coût total")
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        for card in (self.card_saved, self.card_words, self.card_listen, self.card_cost):
            kpi_row.addWidget(card)
        page_layout.addLayout(kpi_row)

        self.card_dictations = KpiCard("Dictées")
        self.card_transcribe = KpiCard("Temps de transcription")
        self.card_latency = KpiCard("Latence médiane")
        self.card_streak = KpiCard("Série en cours")
        kpi_row2 = QHBoxLayout()
        kpi_row2.setSpacing(10)
        for card in (
            self.card_dictations,
            self.card_transcribe,
            self.card_latency,
            self.card_streak,
        ):
            kpi_row2.addWidget(card)
        page_layout.addLayout(kpi_row2)

        # --- activite (jours) ---
        activity_frame, activity = self._make_panel("Activité par jour")
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

        # --- activite (heures) ---
        hourly_frame, hourly = self._make_panel("Répartition sur la journée")
        self.hourly_chart = HourlyChart()
        hourly.addWidget(self.hourly_chart)
        page_layout.addWidget(hourly_frame)

        # --- modeles ---
        models_frame, models = self._make_panel("Modèles utilisés")
        self.models_grid = QGridLayout()
        self.models_grid.setHorizontalSpacing(18)
        self.models_grid.setVerticalSpacing(4)
        models.addLayout(self.models_grid)
        self.models_empty = QLabel("Aucune donnée pour cette période.")
        self.models_empty.setObjectName("hint")
        models.addWidget(self.models_empty)
        page_layout.addWidget(models_frame)

        # --- qualite + repartitions ---
        middle = QHBoxLayout()
        middle.setSpacing(12)

        quality_frame, quality = self._make_panel("Qualité & rythme")
        self.rows = {}
        for key, label in (
            ("median", "Latence médiane"),
            ("p95", "Latence p95"),
            ("transcribe", "Temps total de transcription"),
            ("wpm", "Débit de parole"),
            ("typing", "Vitesse de frappe réf."),
            ("avg_len", "Durée moyenne d'une dictée"),
            ("max_len", "Dictée la plus longue"),
            ("reword", "Reformulation utilisée"),
            ("active", "Jours actifs"),
            ("streak", "Série en cours"),
            ("best", "Meilleure journée"),
            ("peak", "Heure la plus active"),
        ):
            row = StatRow(label)
            quality.addWidget(row)
            self.rows[key] = row
        quality.addStretch(1)
        middle.addWidget(quality_frame, 3)

        shares_frame, shares = self._make_panel("Langues & tons")
        shares.addWidget(QLabel("Langues"))
        self.language_bars = ShareBars()
        shares.addWidget(self.language_bars)
        shares.addSpacing(8)
        shares.addWidget(QLabel("Tons de reformulation"))
        self.tone_bars = ShareBars()
        shares.addWidget(self.tone_bars)
        shares.addStretch(1)
        middle.addWidget(shares_frame, 2)
        page_layout.addLayout(middle)

        # --- enregistrements locaux ---
        rec_frame, rec = self._make_panel("Enregistrements conservés")
        rec_grid = QGridLayout()
        rec_grid.setHorizontalSpacing(20)
        rec_grid.setVerticalSpacing(3)
        self.rec_count = QLabel("—")
        self.rec_size = QLabel("—")
        self.rec_duration = QLabel("—")
        for label, widget in (
            ("Fichiers", self.rec_count),
            ("Espace disque", self.rec_size),
            ("Durée cumulée", self.rec_duration),
        ):
            widget.setObjectName("big")
        rec_grid.addWidget(QLabel("Fichiers"), 0, 0)
        rec_grid.addWidget(self.rec_count, 0, 1)
        rec_grid.addWidget(QLabel("Espace disque"), 1, 0)
        rec_grid.addWidget(self.rec_size, 1, 1)
        rec_grid.addWidget(QLabel("Durée cumulée"), 2, 0)
        rec_grid.addWidget(self.rec_duration, 2, 1)
        rec.addLayout(rec_grid)
        rec_actions = QHBoxLayout()
        self.open_recordings_button = QPushButton("Voir l'historique")
        self.open_recordings_button.clicked.connect(self.history_requested.emit)
        rec_actions.addWidget(self.open_recordings_button)
        rec_actions.addStretch(1)
        rec.addLayout(rec_actions)
        page_layout.addWidget(rec_frame)

        # --- compte OpenRouter ---
        spend_frame, spend = self._make_panel("Compte OpenRouter")
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(3)
        self.spend_usage = QLabel("—")
        self.spend_remaining = QLabel("—")
        self.spend_local = QLabel("—")
        for widget in (self.spend_usage, self.spend_remaining, self.spend_local):
            widget.setObjectName("big")
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
            "même texte (à 40 mots/minute) au temps réellement passé à dicter "
            "(parole + attente de la transcription). Le « mesuré localement » ne "
            "compte que les dictées faites avec Vox ; s'il est inférieur au total du "
            "compte, la différence vient d'autres usages de la même clé."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        page_layout.addWidget(note)
        page_layout.addStretch(1)

    def _make_panel(self, title: str) -> tuple[QFrame, QVBoxLayout]:
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
        self.hourly_chart.set_theme(theme)
        self.language_bars.set_theme(theme)
        self.tone_bars.set_theme(theme)

    def _select_metric(self, metric: str) -> None:
        for key, chip in self.metric_chips.items():
            chip.setChecked(key == metric)
        self.chart.set_metric(metric)

    def _on_period_changed(self, index: int) -> None:
        self._period = self.period_combo.itemData(index) or "30"
        self.refresh()

    def _period_window(self, stats: Stats) -> tuple[datetime | None, int]:
        """Renvoie (depuis, nombre_de_jours_pour_le_graphique)."""
        days = {"7": 7, "30": 30, "90": 90}.get(self._period, 0)
        if days:
            return datetime.now() - timedelta(days=days - 1), days
        if stats.first_at:
            span = (datetime.now().date() - stats.first_at.date()).days + 1
            return None, max(7, min(span, 180))
        return None, 30

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        all_stats = compute(typing_wpm=DEFAULT_TYPING_WPM, window_days=1)
        since, chart_days = self._period_window(all_stats)
        stats = self.stats = compute(
            typing_wpm=DEFAULT_TYPING_WPM, window_days=chart_days, since=since
        )

        self.card_saved.set(
            format_duration(stats.saved_seconds),
            f"×{stats.saved_ratio:.1f} plus rapide" if stats.saved_ratio else "pas encore de données",
        )
        self.card_words.set(_num(stats.words), f"{_num(stats.characters)} caractères")
        self.card_listen.set(format_duration(stats.audio_seconds), f"{stats.entries} dictée(s)")
        self.card_cost.set(
            format_money(stats.cost),
            f"{stats.cost_per_hour:.3f} $/h" if stats.audio_seconds else "—",
        )
        self.card_dictations.set(
            _num(stats.entries),
            f"{stats.active_days} jour(s) actif(s)",
        )
        self.card_transcribe.set(
            format_duration(stats.transcription_seconds),
            f"{stats.median_latency_ms} ms en médiane",
        )
        self.card_latency.set(
            f"{stats.median_latency_ms} ms",
            f"p95 : {stats.p95_latency_ms} ms",
        )
        self.card_streak.set(
            f"{stats.streak} jour(s)",
            f"mieux : {_num(stats.best_day.words) + ' mots le ' + stats.best_day.day.strftime('%d/%m') if stats.best_day else '—'}",
        )

        if stats.first_at and stats.last_at:
            self.period_label.setText(
                f"Du {stats.first_at.strftime('%d/%m/%Y')} au {stats.last_at.strftime('%d/%m/%Y')} "
                f"· période : {self.period_combo.currentText().lower()}"
            )
        else:
            self.period_label.setText("Aucune dictée enregistrée pour cette période.")

        self.chart.set_days(stats.days)
        self.hourly_chart.set_hours(stats.hourly, stats.hourly_words)
        self._fill_models(stats)
        self._fill_rows(stats)
        self._fill_shares(stats)
        self._fill_recordings()

        self.spend_local.setText(format_money(stats.cost))
        self._fetch_usage()

    # ------------------------------------------------------------------
    def _fill_models(self, stats: Stats) -> None:
        while self.models_grid.count():
            item = self.models_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        columns = ("Modèle", "Dictées", "Mots", "Audio", "Coût", "$/h mesuré", "Latence")
        for col, text in enumerate(columns):
            label = QLabel(text.upper())
            label.setObjectName("panelTitle")
            if col:
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.models_grid.addWidget(label, 0, col)

        if not stats.models:
            self.models_empty.setVisible(True)
            return
        self.models_empty.setVisible(False)

        total = stats.entries or 1
        for row, model in enumerate(stats.models[:12], start=1):
            name = model.model.split("/")[-1]
            share = model.dictations / total * 100
            cells = (
                f"{name}  ({share:.0f} %)",
                _num(model.dictations),
                _num(model.words),
                format_duration(model.seconds),
                format_money(model.cost),
                f"{model.cost / (model.seconds / 3600):.2f} $/h" if model.seconds else "—",
                f"{model.median_latency_ms} ms",
            )
            for col, text in enumerate(cells):
                label = QLabel(text)
                if col == 0:
                    label.setToolTip(model.model)
                else:
                    label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.models_grid.addWidget(label, row, col)

    def _fill_rows(self, stats: Stats) -> None:
        self.rows["median"].set(f"{stats.median_latency_ms} ms")
        self.rows["p95"].set(f"{stats.p95_latency_ms} ms")
        self.rows["transcribe"].set(format_duration(stats.transcription_seconds))
        self.rows["wpm"].set(f"{stats.speaking_wpm:.0f} mots/min")
        self.rows["typing"].set(f"{DEFAULT_TYPING_WPM:.0f} mots/min")
        self.rows["avg_len"].set(format_duration(stats.avg_recording_seconds))
        self.rows["max_len"].set(format_duration(stats.max_recording_seconds))
        if stats.entries:
            self.rows["reword"].set(f"{stats.reword_count} ({stats.reword_share:.0%})")
        else:
            self.rows["reword"].set("—")
        self.rows["active"].set(str(stats.active_days))
        self.rows["streak"].set(f"{stats.streak} jour(s)")
        best = stats.best_day
        self.rows["best"].set(
            f"{_num(best.words)} mots le {best.day.strftime('%d/%m')}" if best else "—"
        )
        self.rows["peak"].set(f"{stats.peak_hour:02d}h" if stats.peak_hour >= 0 else "—")

    def _fill_shares(self, stats: Stats) -> None:
        total = stats.entries or 1
        languages = [
            (LANGUAGES.get(code, code.upper() or "?"), count / total, str(count))
            for code, count in stats.languages.most_common(6)
        ]
        self.language_bars.set_rows(languages or [("—", 0.0, "0")])
        tones = [
            (TONES.get(tone, tone), count / total, str(count))
            for tone, count in stats.tones.most_common(6)
        ]
        self.tone_bars.set_rows(tones or [("—", 0.0, "0")])

    def _fill_recordings(self) -> None:
        info = recordings_module.stats()
        self.rec_count.setText(_num(info["count"]))
        self.rec_size.setText(info["size"])
        self.rec_duration.setText(format_duration(info["seconds"]))

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


__all__ = ["StatsWindow"]
