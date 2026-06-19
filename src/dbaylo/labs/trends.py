"""Deterministic trend engine — the "knows your labs better than you" core.

Pure functions over plain ``LabPoint`` values. No LLM, no DB, no network. Given a
per-analyte time series of confirmed results, it computes, entirely in code:

* a per-value in-range flag (LOW / NORMAL / HIGH / UNKNOWN), and
* a direction of movement *relative to the lab's own reference range*.

Direction is always range-relative, never a clinical verdict (rail #4). The
coarse IMPROVING/WORSENING polarity below is **internal only** — surface text
(in ``locale``) speaks of "наближається до норми" / "вийшов за межі" etc., never
"покращується / погіршується".
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from enum import Enum, auto

from dbaylo.db.models import ResultFlag


@dataclass(frozen=True)
class LabPoint:
    """One confirmed measurement in a series (engine input — DB-agnostic)."""

    analyte: str
    taken_on: date
    value: float | None
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None


class TrendDirection(Enum):
    """Range-relative movement classification (the internal representation)."""

    INSUFFICIENT_DATA = auto()
    UNKNOWN_RANGE = auto()
    STABLE_IN_RANGE = auto()
    STABLE_OUT_OF_RANGE = auto()
    RETURNED_TO_RANGE = auto()
    LEFT_RANGE = auto()
    APPROACHING_RANGE = auto()
    MOVING_AWAY = auto()


class Polarity(Enum):
    """Coarse internal polarity. INTERNAL ONLY — never shown to the user."""

    IMPROVING = auto()
    WORSENING = auto()
    NEUTRAL = auto()
    UNKNOWN = auto()


_POLARITY: dict[TrendDirection, Polarity] = {
    TrendDirection.RETURNED_TO_RANGE: Polarity.IMPROVING,
    TrendDirection.APPROACHING_RANGE: Polarity.IMPROVING,
    TrendDirection.LEFT_RANGE: Polarity.WORSENING,
    TrendDirection.MOVING_AWAY: Polarity.WORSENING,
    TrendDirection.STABLE_IN_RANGE: Polarity.NEUTRAL,
    TrendDirection.STABLE_OUT_OF_RANGE: Polarity.NEUTRAL,
    TrendDirection.UNKNOWN_RANGE: Polarity.UNKNOWN,
    TrendDirection.INSUFFICIENT_DATA: Polarity.UNKNOWN,
}


def polarity(direction: TrendDirection) -> Polarity:
    """Internal-only coarse polarity for a direction. Not for surface text."""
    return _POLARITY[direction]


@dataclass(frozen=True)
class TrendSummary:
    """The computed verdict for one analyte's series."""

    analyte: str
    key: str
    direction: TrendDirection
    n_points: int
    latest: LabPoint | None
    latest_flag: ResultFlag
    previous: LabPoint | None
    delta: float | None
    first_date: date | None
    last_date: date | None
    unit: str | None


# --- Analyte name normalization + alias map -------------------------------------
#
# KNOWN LIMITATION: series are grouped by a normalized analyte name. Different labs
# print the same analyte differently ("Глюкоза" vs "Глюкоза крові (натще)"), which
# would fragment a series. This small alias map canonicalizes the common cases and
# is meant to be extended as new spellings are encountered.

ANALYTE_ALIASES: dict[str, str] = {
    "глюкоза крові": "глюкоза",
    "глюкоза (натще)": "глюкоза",
    "глюкоза натще": "глюкоза",
    "креатинін крові": "креатинін",
    "холестерин загальний": "загальний холестерин",
    "гемоглобін (hgb)": "гемоглобін",
    "hb": "гемоглобін",
    "сечовина крові": "сечовина",
}

_WS_RE = re.compile(r"\s+")


def normalize_analyte(name: str) -> str:
    """Normalize an analyte name to a grouping key (casefold + collapse spaces + alias)."""
    base = _WS_RE.sub(" ", name).strip().casefold()
    return ANALYTE_ALIASES.get(base, base)


# --- Range helpers --------------------------------------------------------------


def compute_flag(value: float | None, ref_low: float | None, ref_high: float | None) -> ResultFlag:
    """Classify a single value against its reference range."""
    if value is None or (ref_low is None and ref_high is None):
        return ResultFlag.UNKNOWN
    if ref_low is not None and value < ref_low:
        return ResultFlag.LOW
    if ref_high is not None and value > ref_high:
        return ResultFlag.HIGH
    return ResultFlag.NORMAL


# A qualitative reference lists its acceptable values; split on the usual separators.
_QUAL_SPLIT = re.compile(r"\s*(?:,|;|/|\bабо\b|\bчи\b)\s*", re.IGNORECASE)
_PARENS_RE = re.compile(r"\([^)]*\)")


def _normalize_qual(text: str) -> str:
    return _WS_RE.sub(" ", _PARENS_RE.sub(" ", text)).strip().casefold()


def qualitative_match(value_text: str | None, ref_text: str | None) -> bool:
    """True when a qualitative value clearly matches its qualitative reference.

    Deliberately conservative: it only ever confirms "matches the reference" and never
    infers a direction. A value must equal the whole reference or one of its listed
    options — so a negation ("виявлені" vs "не виявлені") does NOT match, and an
    abnormal qualitative result is never called normal.
    """
    if not value_text or not ref_text:
        return False
    value = _normalize_qual(value_text)
    ref = _normalize_qual(ref_text)
    if not value or not ref:
        return False
    if value == ref:
        return True
    return value in [option for option in _QUAL_SPLIT.split(ref) if option]


def classify(
    value: float | None,
    value_text: str | None,
    ref_low: float | None,
    ref_high: float | None,
    ref_text: str | None,
) -> ResultFlag:
    """Flag a result: numeric range when it can decide, else a qualitative match.

    The numeric comparison wins; only when it yields UNKNOWN (a qualitative result, or
    no range) do we try to match the qualitative value against its reference. A match is
    NORMAL; anything else stays UNKNOWN. We never produce LOW/HIGH from free text.
    """
    numeric = compute_flag(value, ref_low, ref_high)
    if numeric is not ResultFlag.UNKNOWN:
        return numeric
    return ResultFlag.NORMAL if qualitative_match(value_text, ref_text) else ResultFlag.UNKNOWN


def _in_range(point: LabPoint) -> bool | None:
    """True/False if range membership is determinable, else None."""
    if point.value is None or (point.ref_low is None and point.ref_high is None):
        return None
    above_low = point.ref_low is None or point.value >= point.ref_low
    below_high = point.ref_high is None or point.value <= point.ref_high
    return above_low and below_high


def _distance_outside(point: LabPoint) -> float:
    """Distance to the nearest violated bound (0 if in range / undeterminable)."""
    if point.value is None:
        return 0.0
    if point.ref_low is not None and point.value < point.ref_low:
        return point.ref_low - point.value
    if point.ref_high is not None and point.value > point.ref_high:
        return point.value - point.ref_high
    return 0.0


# --- Series construction + trend computation ------------------------------------


def build_series(points: list[LabPoint]) -> dict[str, list[LabPoint]]:
    """Group points by normalized analyte key, each list sorted by date ascending."""
    grouped: dict[str, list[LabPoint]] = defaultdict(list)
    for point in points:
        grouped[normalize_analyte(point.analyte)].append(point)
    for series in grouped.values():
        series.sort(key=lambda p: p.taken_on)
    return dict(grouped)


def _classify(latest: LabPoint, previous: LabPoint) -> TrendDirection:
    lr, pr = _in_range(latest), _in_range(previous)
    if lr is None or pr is None:
        return TrendDirection.UNKNOWN_RANGE
    if lr and pr:
        return TrendDirection.STABLE_IN_RANGE
    if lr and not pr:
        return TrendDirection.RETURNED_TO_RANGE
    if not lr and pr:
        return TrendDirection.LEFT_RANGE
    # Both out of range — compare distance to the nearest bound.
    dl, dp = _distance_outside(latest), _distance_outside(previous)
    if dl < dp:
        return TrendDirection.APPROACHING_RANGE
    if dl > dp:
        return TrendDirection.MOVING_AWAY
    return TrendDirection.STABLE_OUT_OF_RANGE


def compute_trend(points: list[LabPoint]) -> TrendSummary:
    """Compute the trend summary for a single analyte's series."""
    display = points[-1].analyte if points else ""
    key = normalize_analyte(display) if display else ""

    numeric = sorted((p for p in points if p.value is not None), key=lambda p: p.taken_on)
    latest = numeric[-1] if numeric else None
    previous = numeric[-2] if len(numeric) >= 2 else None

    if len(numeric) < 2:
        direction = TrendDirection.INSUFFICIENT_DATA
        delta = None
    else:
        assert latest is not None and previous is not None  # for type-narrowing
        direction = _classify(latest, previous)
        delta = latest.value - previous.value  # type: ignore[operator]

    latest_flag = (
        compute_flag(latest.value, latest.ref_low, latest.ref_high)
        if latest is not None
        else ResultFlag.UNKNOWN
    )

    return TrendSummary(
        analyte=display,
        key=key,
        direction=direction,
        n_points=len(numeric),
        latest=latest,
        latest_flag=latest_flag,
        previous=previous,
        delta=delta,
        first_date=numeric[0].taken_on if numeric else None,
        last_date=latest.taken_on if latest else None,
        unit=latest.unit if latest else None,
    )
