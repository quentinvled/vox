"""Statistiques d'usage : agregation de l'historique local.

Chaque dictee est journalisee dans `history.jsonl` (voir Pipeline._save_history).
Ce module en tire les indicateurs affiches dans la fenetre Statistiques et par
`vox --stats`.

Convention pour le « temps gagne » : on compare le temps qu'il aurait fallu pour
TAPER le texte (a une vitesse de frappe de reference) au temps reellement passe
a dicter, latence de transcription incluse. C'est la meme logique que les
statistiques de Whisper Typing.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .paths import history_file

# Vitesses de reference (mots par minute).
DEFAULT_TYPING_WPM = 40.0
DEFAULT_SPEAKING_WPM = 140.0


@dataclass
class DayPoint:
    day: date
    dictations: int = 0
    words: int = 0
    seconds: float = 0.0
    cost: float = 0.0


@dataclass
class ModelStat:
    model: str
    dictations: int = 0
    words: int = 0
    seconds: float = 0.0
    cost: float = 0.0
    latency_ms: list[int] = field(default_factory=list)

    @property
    def median_latency_ms(self) -> int:
        return int(statistics.median(self.latency_ms)) if self.latency_ms else 0


@dataclass
class Stats:
    """Tous les indicateurs calcules a partir de l'historique."""

    entries: int = 0
    first_at: datetime | None = None
    last_at: datetime | None = None

    audio_seconds: float = 0.0
    words: int = 0
    characters: int = 0
    cost: float = 0.0

    latencies_ms: list[int] = field(default_factory=list)
    typing_wpm: float = DEFAULT_TYPING_WPM

    models: list[ModelStat] = field(default_factory=list)
    tones: Counter = field(default_factory=Counter)
    languages: Counter = field(default_factory=Counter)
    days: list[DayPoint] = field(default_factory=list)
    # Repartition par heure de la journee (index 0-23).
    hourly: list[int] = field(default_factory=lambda: [0] * 24)
    hourly_words: list[int] = field(default_factory=lambda: [0] * 24)

    # Complement issus de l'historique.
    reword_count: int = 0
    reword_cost: float = 0.0
    max_recording_seconds: float = 0.0

    # ------------------------------------------------------------------
    @property
    def active_days(self) -> int:
        return len([d for d in self.days if d.dictations])

    @property
    def transcription_seconds(self) -> float:
        """Temps total passe a attendre la transcription (somme des latences)."""
        return self.latency_seconds

    @property
    def avg_recording_seconds(self) -> float:
        return self.audio_seconds / self.entries if self.entries else 0.0

    @property
    def avg_words(self) -> float:
        return self.words / self.entries if self.entries else 0.0

    @property
    def reword_share(self) -> float:
        return self.reword_count / self.entries if self.entries else 0.0

    @property
    def peak_hour(self) -> int:
        if not any(self.hourly):
            return -1
        return max(range(24), key=lambda h: self.hourly[h])

    @property
    def median_latency_ms(self) -> int:
        return int(statistics.median(self.latencies_ms)) if self.latencies_ms else 0

    @property
    def avg_latency_ms(self) -> int:
        return int(statistics.mean(self.latencies_ms)) if self.latencies_ms else 0

    @property
    def p95_latency_ms(self) -> int:
        if not self.latencies_ms:
            return 0
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
        return ordered[index]

    @property
    def latency_seconds(self) -> float:
        return sum(self.latencies_ms) / 1000.0

    # --- temps ---------------------------------------------------------
    @property
    def speaking_wpm(self) -> float:
        if self.audio_seconds <= 0:
            return 0.0
        return self.words / (self.audio_seconds / 60.0)

    @property
    def typing_seconds(self) -> float:
        """Temps qu'aurait pris la frappe du meme texte."""
        if self.typing_wpm <= 0:
            return 0.0
        return self.words / self.typing_wpm * 60.0

    @property
    def dictation_seconds(self) -> float:
        """Temps reellement consomme : parole + attente de la transcription."""
        return self.audio_seconds + self.latency_seconds

    @property
    def saved_seconds(self) -> float:
        return self.typing_seconds - self.dictation_seconds

    @property
    def saved_ratio(self) -> float:
        """Combien de fois plus rapide que la frappe (0 si pas de donnees)."""
        if self.dictation_seconds <= 0:
            return 0.0
        return self.typing_seconds / self.dictation_seconds

    # --- cout ---------------------------------------------------------
    @property
    def cost_per_hour(self) -> float:
        if self.audio_seconds <= 0:
            return 0.0
        return self.cost / (self.audio_seconds / 3600.0)

    @property
    def avg_cost(self) -> float:
        return self.cost / self.entries if self.entries else 0.0

    @property
    def best_day(self) -> DayPoint | None:
        candidates = [d for d in self.days if d.dictations]
        return max(candidates, key=lambda d: d.words, default=None)

    @property
    def streak(self) -> int:
        """Jours consecutifs d'usage en terminant par aujourd'hui ou hier."""
        active = {d.day for d in self.days if d.dictations}
        if not active:
            return 0
        today = date.today()
        cursor = today if today in active else today - timedelta(days=1)
        if cursor not in active:
            return 0
        count = 0
        while cursor in active:
            count += 1
            cursor -= timedelta(days=1)
        return count


# ----------------------------------------------------------------------
def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _count_words(text: str) -> int:
    return len((text or "").split())


@dataclass
class MeasuredRate:
    """Tarif effectivement constate pour un modele, d'apres l'historique."""

    model: str
    per_hour: float = 0.0
    seconds: float = 0.0
    cost: float = 0.0
    dictations: int = 0


def measured_rates(
    entries: list[dict] | None = None, min_seconds: float = 8.0
) -> dict[str, MeasuredRate]:
    """Tarif horaire reel par modele, mesure sur tes propres dictees.

    C'est la seule source fiable : l'unite du catalogue OpenRouter n'est pas
    homogene selon les fournisseurs (voir `api.per_hour_from_catalogue`).
    Les modeles vus sur moins de `min_seconds` d'audio sont ignores, leur taux
    etant trop bruite.
    """
    rows = load_entries() if entries is None else entries
    acc: dict[str, MeasuredRate] = {}
    for row in rows:
        model = row.get("model") or ""
        seconds = _to_float(row.get("seconds"))
        if not model or seconds <= 0:
            continue
        cost = _to_float(row.get("cost")) + _to_float(row.get("reword_cost"))
        entry = acc.setdefault(model, MeasuredRate(model=model))
        entry.seconds += seconds
        entry.cost += cost
        entry.dictations += 1

    out: dict[str, MeasuredRate] = {}
    for model, entry in acc.items():
        if entry.seconds < min_seconds:
            continue
        entry.per_hour = entry.cost / (entry.seconds / 3600.0)
        out[model] = entry
    return out


def load_entries(path=None) -> list[dict]:
    """Lit l'historique en ignorant les lignes corrompues."""
    target = path or history_file()
    if not target.exists():
        return []
    entries: list[dict] = []
    try:
        with target.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries


def _parse_at(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def compute(
    entries: list[dict] | None = None,
    *,
    typing_wpm: float = DEFAULT_TYPING_WPM,
    window_days: int = 30,
    since: datetime | None = None,
) -> Stats:
    """Agrege l'historique en indicateurs.

    `since` restreint aux dictees posterieures a cette date (None = tout).
    """
    rows = load_entries() if entries is None else entries
    if since is not None:
        rows = [
            row
            for row in rows
            if (moment := _parse_at(row.get("at"))) is None or moment >= since
        ]
    stats = Stats(entries=len(rows), typing_wpm=typing_wpm)
    if not rows:
        return stats

    per_model: dict[str, ModelStat] = {}
    per_day: dict[date, DayPoint] = {}

    for row in rows:
        text = row.get("text") or row.get("transcript") or ""
        words = _count_words(text)
        seconds = _to_float(row.get("seconds"))
        cost = _to_float(row.get("cost")) + _to_float(row.get("reword_cost"))
        latency = int(_to_float(row.get("latency_ms")))

        stats.words += words
        stats.characters += len(text)
        stats.audio_seconds += seconds
        stats.cost += cost
        stats.reword_cost += _to_float(row.get("reword_cost"))
        if row.get("tone"):
            stats.reword_count += 1
        stats.max_recording_seconds = max(stats.max_recording_seconds, seconds)
        if latency:
            stats.latencies_ms.append(latency)

        # horodatage
        moment = _parse_at(row.get("at"))
        if moment is not None:
            if stats.first_at is None or moment < stats.first_at:
                stats.first_at = moment
            if stats.last_at is None or moment > stats.last_at:
                stats.last_at = moment

        # modele
        model = row.get("model") or "inconnu"
        entry = per_model.setdefault(model, ModelStat(model=model))
        entry.dictations += 1
        entry.words += words
        entry.seconds += seconds
        entry.cost += cost
        if latency:
            entry.latency_ms.append(latency)

        tone = row.get("tone")
        if tone:
            stats.tones[tone] += 1
        language = row.get("language")
        if language:
            stats.languages[language] += 1

        if moment is not None:
            point = per_day.setdefault(moment.date(), DayPoint(day=moment.date()))
            point.dictations += 1
            point.words += words
            point.seconds += seconds
            point.cost += cost
            stats.hourly[moment.hour] += 1
            stats.hourly_words[moment.hour] += words

    stats.models = sorted(per_model.values(), key=lambda m: -m.dictations)

    # serie continue sur la fenetre demandee (jours vides inclus)
    today = date.today()
    start = today - timedelta(days=window_days - 1)
    stats.days = [
        per_day.get(start + timedelta(days=offset), DayPoint(day=start + timedelta(days=offset)))
        for offset in range(window_days)
    ]
    return stats


# ----------------------------------------------------------------------
def format_duration(seconds: float) -> str:
    """« 2 h 14 min », « 7 min 03 s », « 42 s »."""
    total = round(max(0.0, seconds))
    if total < 60:
        return f"{total} s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min {secs:02d} s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes:02d} min"
    days, hours = divmod(hours, 24)
    return f"{days} j {hours:02d} h"


def format_money(value: float) -> str:
    if value and value < 0.01:
        return f"{value:.4f} $"
    return f"{value:.2f} $"


def summary_lines(stats: Stats) -> list[tuple[str, str]]:
    """Paires (libelle, valeur) pour l'affichage CLI."""
    return [
        ("Dictées", str(stats.entries)),
        ("Temps d'écoute", format_duration(stats.audio_seconds)),
        ("Mots écrits", f"{stats.words:,}".replace(",", " ")),
        ("Caractères", f"{stats.characters:,}".replace(",", " ")),
        ("Débit de parole", f"{stats.speaking_wpm:.0f} mots/min"),
        ("Temps de frappe évité", format_duration(stats.typing_seconds)),
        ("Temps consommé", format_duration(stats.dictation_seconds)),
        ("Temps gagné", format_duration(stats.saved_seconds)),
        ("Raccourci", f"×{stats.saved_ratio:.1f} plus rapide" if stats.saved_ratio else "-"),
        ("Latence médiane", f"{stats.median_latency_ms} ms"),
        ("Latence p95", f"{stats.p95_latency_ms} ms"),
        ("Coût total", format_money(stats.cost)),
        ("Coût horaire", f"{stats.cost_per_hour:.3f} $/h" if stats.audio_seconds else "-"),
        ("Jours actifs", str(stats.active_days)),
        ("Série en cours", f"{stats.streak} j"),
    ]
