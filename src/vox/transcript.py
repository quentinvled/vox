"""Transcript multi-locuteurs : modele, fusion des tranches et exports.

Un import produit plusieurs tranches (et parfois plusieurs pistes). Chaque
tranche revient avec ses propres etiquettes de locuteurs : la fusion doit donc
1. remettre les temps en absolu, 2. jeter les segments en double dans les
recouvrements, 3. raccorder les etiquettes entre tranches grace aux zones de
recouvrement, puis 4. exporter le resultat (texte, markdown, SRT, VTT).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

# Au-dela, on considere que deux segments qui se recouvrent sont la meme prise
# de parole (evidence pour raccorder « locuteur 0 » d'une tranche a l'autre).
MATCH_MIN_OVERLAP = 0.15
MATCH_MIN_SCORE = 0.25
TEXT_MATCH_RATIO = 0.55

_WORD_RE = re.compile(r"[^\wàâäéèêëîïôöùûüç'’-]+", re.IGNORECASE)
# Etiquette de locuteur portant le numero de tranche : « t0:c2:s1 ».
_LABEL_RE = re.compile(r"^t(\d+):c(\d+):")


# ----------------------------------------------------------------------
@dataclass
class Segment:
    start: float
    end: float
    speaker: str
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def middle(self) -> float:
        return (self.start + self.end) / 2.0


@dataclass
class Speaker:
    id: str
    name: str = ""


@dataclass
class ChunkResult:
    """Segments d'une tranche, en temps relatif a `window_start`."""

    index: int
    track: int
    window_start: float
    core_start: float
    core_end: float
    window_end: float = 0.0
    segments: list[Segment] = field(default_factory=list)
    track_label: str = ""
    fixed_speaker: bool = False
    language: str = ""
    model: str = ""
    cost: float = 0.0
    seconds: float = 0.0
    error: str = ""


@dataclass
class Transcript:
    segments: list[Segment] = field(default_factory=list)
    speakers: list[Speaker] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0
    model: str = ""
    cost: float = 0.0
    source: str = ""
    title: str = ""
    warnings: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    def get_speaker(self, speaker_id: str) -> Speaker | None:
        return next((item for item in self.speakers if item.id == speaker_id), None)

    def label_for(self, speaker_id: str) -> str:
        """Nom du locuteur, sinon « Locuteur N » (N = ordre d'apparition)."""
        for index, item in enumerate(self.speakers):
            if item.id == speaker_id:
                return item.name or f"Locuteur {index + 1}"
        return speaker_id or "Locuteur"

    def speaker_labels(self) -> list[str]:
        return [self.label_for(item.id) for item in self.speakers]

    def rename(self, speaker_id: str, name: str) -> None:
        target = self.get_speaker(speaker_id)
        if target is not None:
            target.name = name.strip()

    def merge_speakers(self, keep: str, drop: str) -> None:
        """Fusionne deux locuteurs (corrige un sur-decoupage)."""
        if keep == drop:
            return
        for segment in self.segments:
            if segment.speaker == drop:
                segment.speaker = keep
        self.speakers = [item for item in self.speakers if item.id != drop]

    def reassign(self, segment_index: int, speaker_id: str) -> None:
        if 0 <= segment_index < len(self.segments):
            self.segments[segment_index].speaker = speaker_id

    @property
    def text(self) -> str:
        return "\n".join(self.lines())

    def lines(self, *, with_time: bool = False) -> list[str]:
        output: list[str] = []
        for run_speaker, run in _runs(self.segments):
            prefix = self.label_for(run_speaker)
            if with_time:
                prefix = f"[{stamp(run[0].start, short=True)}] {prefix}"
            body = " ".join(segment.text for segment in run).strip()
            output.append(f"{prefix} : {body}")
        return output

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        payload = {
            "segments": [
                {"start": round(s.start, 3), "end": round(s.end, 3), "speaker": s.speaker, "text": s.text}
                for s in self.segments
            ],
            "speakers": [{"id": s.id, "name": s.name} for s in self.speakers],
            "language": self.language,
            "duration": round(self.duration, 3),
            "model": self.model,
            "cost": self.cost,
            "source": self.source,
            "title": self.title,
            "warnings": self.warnings,
        }
        if self.extras:
            payload["extras"] = self.extras
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> Transcript:
        return cls(
            segments=[
                Segment(
                    start=float(item.get("start") or 0.0),
                    end=float(item.get("end") or 0.0),
                    speaker=str(item.get("speaker") or ""),
                    text=str(item.get("text") or ""),
                )
                for item in data.get("segments") or []
            ],
            speakers=[
                Speaker(id=str(item.get("id") or ""), name=str(item.get("name") or ""))
                for item in data.get("speakers") or []
            ],
            language=str(data.get("language") or ""),
            duration=float(data.get("duration") or 0.0),
            model=str(data.get("model") or ""),
            cost=float(data.get("cost") or 0.0),
            source=str(data.get("source") or ""),
            title=str(data.get("title") or ""),
            warnings=[str(item) for item in data.get("warnings") or []],
            extras=dict(data.get("extras") or {}),
        )

    # ------------------------------------------------------------------
    def to_text(self, *, with_time: bool = False) -> str:
        return "\n".join(self.lines(with_time=with_time)) + "\n"

    def to_markdown(self, *, with_time: bool = True) -> str:
        blocks: list[str] = []
        title = self.title or "Transcript"
        blocks.append(f"# {title}")
        meta = [item for item in (self.model, _duration_label(self.duration)) if item]
        if meta:
            blocks.append("*" + " · ".join(meta) + "*")
        for run_speaker, run in _runs(self.segments):
            prefix = self.label_for(run_speaker)
            if with_time:
                prefix = f"[{stamp(run[0].start, short=True)}] {prefix}"
            body = " ".join(segment.text for segment in run).strip()
            blocks.append(f"**{prefix}**\n\n{body}")
        return "\n\n".join(blocks) + "\n"

    def to_srt(self) -> str:
        return self._cues(comma=True)

    def to_vtt(self) -> str:
        lines = ["WEBVTT", ""]
        for index, (speaker_id, run) in enumerate(_runs(self.segments), start=1):
            start = stamp(run[0].start)
            end = stamp(run[-1].end)
            body = " ".join(segment.text for segment in run).strip()
            lines.append(f"{index}")
            lines.append(f"{start} --> {end}")
            lines.append(f"<v {self.label_for(speaker_id)}>{body}")
            lines.append("")
        return "\n".join(lines)

    def _cues(self, *, comma: bool) -> str:
        lines: list[str] = []
        for index, (speaker_id, run) in enumerate(_runs(self.segments), start=1):
            start = stamp(run[0].start, comma=comma)
            end = stamp(run[-1].end, comma=comma)
            body = f"{self.label_for(speaker_id)} : " + " ".join(segment.text for segment in run).strip()
            lines.append(str(index))
            lines.append(f"{start} --> {end}")
            lines.append(body)
            lines.append("")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# Analyse des reponses de l'API
# ----------------------------------------------------------------------
def _speaker_id(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.isdigit():
        return f"s{int(text)}"
    return text


def segments_from_payload(payload: dict, *, track_label: str = "") -> list[Segment]:
    """Segments (ou mots) diarises d'une reponse STT, ou texte brut en repli."""
    raw_segments = payload.get("segments")
    if isinstance(raw_segments, list) and raw_segments:
        output: list[Segment] = []
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            output.append(
                Segment(
                    start=_as_float(item.get("start")),
                    end=_as_float(item.get("end")),
                    speaker=_speaker_id(item.get("speaker")),
                    text=text,
                )
            )
        if output:
            return output

    raw_words = payload.get("words")
    if isinstance(raw_words, list) and raw_words:
        grouped: list[Segment] = []
        for item in raw_words:
            if not isinstance(item, dict):
                continue
            word = str(item.get("word") or item.get("text") or "").strip()
            if not word:
                continue
            start = _as_float(item.get("start"))
            end = _as_float(item.get("end"))
            speaker = _speaker_id(item.get("speaker"))
            if grouped and grouped[-1].speaker == speaker and start - grouped[-1].end < 2.0:
                grouped[-1].text = f"{grouped[-1].text} {word}"
                grouped[-1].end = end
            else:
                grouped.append(Segment(start=start, end=end, speaker=speaker, text=word))
        if grouped:
            return grouped

    text = str(payload.get("text") or "").strip()
    if text:
        return [Segment(start=0.0, end=0.0, speaker="", text=text)]
    return []


def _as_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ----------------------------------------------------------------------
# Fusion des tranches
# ----------------------------------------------------------------------
def assemble(results: list[ChunkResult], *, duration: float = 0.0, title: str = "") -> Transcript:
    """Fusionne les tranches en un transcript coherent, sans doublons."""
    namespaced: list[tuple[Segment, str]] = []
    for result in results:
        for segment in result.segments:
            prefix = f"t{result.track}" if result.fixed_speaker else f"t{result.track}:c{result.index}"
            label = f"{prefix}:{segment.speaker or '-'}"
            namespaced.append(
                (
                    Segment(
                        start=max(0.0, result.window_start + segment.start),
                        end=max(0.0, result.window_start + segment.end),
                        speaker=label,
                        text=segment.text,
                    ),
                    label,
                )
            )

    merged = _union_find([label for _, label in namespaced])
    dropped = _link_overlaps(results, namespaced, merged)

    kept = [
        (segment, merged.find(label))
        for segment, label in namespaced
        if id(segment) not in dropped and _in_core(segment, results, label)
    ]
    kept.sort(key=lambda item: (item[0].start, item[0].end))

    order: list[str] = []
    for _, root in kept:
        if root not in order:
            order.append(root)

    speakers = [Speaker(id=root, name=_default_name(root, results)) for root in order]
    segments = [Segment(start=s.start, end=s.end, speaker=root, text=s.text) for s, root in kept]

    transcript = Transcript(
        segments=segments,
        speakers=speakers,
        duration=duration or (segments[-1].end if segments else 0.0),
        title=title,
    )
    transcript.language = next((item.language for item in results if item.language), "")
    models = [item.model for item in results if item.model]
    transcript.model = models[0] if models else ""
    transcript.cost = round(sum(item.cost for item in results), 8)
    transcript.warnings = [item.error for item in results if item.error]
    return transcript


def _default_name(root: str, results: list[ChunkResult]) -> str:
    """Piste non diarisee : le nom de la piste sert de nom de locuteur."""
    track = root.split(":", 1)[0]
    if not track.startswith("t"):
        return ""
    try:
        index = int(track[1:])
    except ValueError:
        return ""
    for result in results:
        if result.track == index and result.fixed_speaker and result.track_label:
            return result.track_label
    return ""


def _in_core(segment: Segment, results: list[ChunkResult], label: str) -> bool:
    """Le segment appartient-il a la fenetre « utile » de sa tranche ?"""
    if segment.duration < 0.05:
        # Modele sans horodatage : rien a dedoublonner, on garde tout.
        return True
    match = _LABEL_RE.match(label)
    if match is None:
        return True  # piste non diarisee : tout est garde
    track_index, chunk_index = int(match.group(1)), int(match.group(2))
    for result in results:
        if result.track == track_index and result.index == chunk_index:
            return result.core_start <= segment.middle <= result.core_end
    return True


def _link_overlaps(
    results: list[ChunkResult],
    namespaced: list[tuple[Segment, str]],
    merged: _DSU,
) -> set[int]:
    """Raccorde les etiquettes entre tranches voisines d'une meme piste.

    Renvoie les segments a jeter : lorsqu'une phrase traverse une coupe, elle
    apparait dans les deux tranches et seule la mieux couverte par le « coeur »
    de sa tranche est conservee.
    """
    dropped: set[int] = set()
    by_index = {result.index: result for result in results}
    for result in results:
        following = by_index.get(result.index + 1)
        if following is None or following.track != result.track or result.fixed_speaker:
            continue
        low = max(result.window_start, following.window_start)
        high = min(result.window_end, following.window_end)
        if high <= low:
            # Fenetres non renseignees : on retombe sur la zone de recouvrement
            # theorique autour de la coupe.
            low = following.core_start - 2.5
            high = following.core_start + 2.5
        left = [
            (segment, label)
            for segment, label in namespaced
            if label.startswith(f"t{result.track}:c{result.index}:")
            and segment.duration >= 0.05
            and low <= segment.middle <= high
        ]
        right = [
            (segment, label)
            for segment, label in namespaced
            if label.startswith(f"t{result.track}:c{following.index}:")
            and segment.duration >= 0.05
            and low <= segment.middle <= high
        ]
        pairs: list[tuple[float, Segment, str, Segment, str]] = []
        for left_segment, left_label in left:
            for right_segment, right_label in right:
                overlap = min(left_segment.end, right_segment.end) - max(
                    left_segment.start, right_segment.start
                )
                if overlap < MATCH_MIN_OVERLAP and abs(
                    left_segment.middle - right_segment.middle
                ) > 1.5:
                    continue
                similarity = _similarity(left_segment.text, right_segment.text)
                if similarity < TEXT_MATCH_RATIO and overlap < MATCH_MIN_OVERLAP:
                    continue
                score = max(overlap, 0.0) * (0.5 + similarity)
                if score >= MATCH_MIN_SCORE:
                    pairs.append((score, left_segment, left_label, right_segment, right_label))
        used_left: set[str] = set()
        used_right: set[str] = set()
        for _score, left_segment, left_label, right_segment, right_label in sorted(
            pairs, key=lambda item: item[0], reverse=True
        ):
            if left_label in used_left or right_label in used_right:
                continue
            merged.union(left_label, right_label)
            used_left.add(left_label)
            used_right.add(right_label)
            left_coverage = _core_coverage(left_segment, result)
            right_coverage = _core_coverage(right_segment, following)
            loser = left_segment if left_coverage < right_coverage else right_segment
            dropped.add(id(loser))
    return dropped


def _core_coverage(segment: Segment, result: ChunkResult) -> float:
    """Duree du segment qui tombe dans la fenetre utile de sa tranche."""
    start = max(segment.start, result.core_start)
    end = min(segment.end, result.core_end)
    return max(0.0, end - start)


class _DSU:
    """Union-find minimal pour fusionner les etiquettes de locuteurs."""

    def __init__(self, items: list[str]) -> None:
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, first: str, second: str) -> None:
        root_first, root_second = self.find(first), self.find(second)
        if root_first != root_second:
            self.parent[root_second] = root_first


def _union_find(items: list[str]) -> _DSU:
    return _DSU(items)


def _similarity(first: str, second: str) -> float:
    left = _normalise(first)
    right = _normalise(second)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def _normalise(text: str) -> str:
    return _WORD_RE.sub(" ", text.lower()).strip()


def _runs(segments: list[Segment]) -> list[tuple[str, list[Segment]]]:
    """Regroupe les segments consecutifs d'un meme locuteur."""
    output: list[tuple[str, list[Segment]]] = []
    for segment in segments:
        if output and output[-1][0] == segment.speaker:
            output[-1][1].append(segment)
        else:
            output.append((segment.speaker, [segment]))
    return output


# ----------------------------------------------------------------------
# Horodatage
# ----------------------------------------------------------------------
def stamp(seconds: float, *, comma: bool = False, short: bool = False) -> str:
    total = max(0.0, seconds)
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    remainder = total % 60
    separator = "," if comma else "."
    if short:
        if hours:
            return f"{hours}:{minutes:02d}:{int(remainder):02d}"
        return f"{minutes:02d}:{int(remainder):02d}"
    return f"{hours:02d}:{minutes:02d}:{int(remainder):02d}{separator}{int((remainder % 1) * 1000):03d}"


def _duration_label(seconds: float) -> str:
    total = round(seconds)
    if total < 60:
        return f"{total} s"
    return f"{total // 60} min {total % 60:02d}"


__all__ = [
    "MATCH_MIN_OVERLAP",
    "MATCH_MIN_SCORE",
    "ChunkResult",
    "Segment",
    "Speaker",
    "Transcript",
    "assemble",
    "segments_from_payload",
    "stamp",
]
