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
from dataclasses import dataclass, replace
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
    # The lab's OWN out-of-range mark for this measurement — the reliable verdict even when the
    # numeric reference was not captured (many rows have flagged=True but no ref_low/ref_high).
    flagged: bool = False
    # The printed panel the row sits under ("Загальний аналіз сечі", "Спермограма", …). Carried so
    # the same analyte NAME in different specimens (Еритроцити in blood vs urine vs semen) never
    # collapses into one nonsensical series — see ``series_key``.
    section: str | None = None
    # The qualitative result text ("не виявлені", "виявлено", "негатив") for rows with no numeric
    # value — so a qualitative analyte can still show a (textual) timeline, not just numeric charts.
    value_text: str | None = None


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
    # Spermogram — the same parameter is named differently by Сінево vs Медцентр Св. Параскеви, so
    # the three reports fragmented into one-point series and never trended together. Keys are the
    # already-normalized form (apostrophes unified, leading enumerators like "1. "/"а) " stripped).
    "об'єм в мл": "об'єм",
    "живі (%)": "живі",
    "живі сперматозоїди": "живі",
    "мертві (%)": "мертві",
    "мертві сперматозоїди": "мертві",
    "реакція (ph)": "ph",
    "рн": "ph",
    "прогресивна рухливість (%) (a+b)": "рухливість прогресивна",
    "рухливість прогресивна (а+в)": "рухливість прогресивна",
    "кількість сперматозоїдів в еякуляті": "сперматозоїди в еякуляті",
    "загальна кількість сперматозоїдів у еякуляті": "сперматозоїди в еякуляті",
    "кількість сперматозоїдів в 1 мл": "концентрація сперматозоїдів",
    "загальна концентрація сперматозоїдів": "концентрація сперматозоїдів",
    "з нормальною морфологією (%)": "нормальні форми сперматозоїдів",
    "патологія голівки": "патологія голови",
    "нерухливих (%) (c)": "нерухомі сперматозоїди",
    "нерухомі сперматозоїди (d)": "нерухомі сперматозоїди",
}

_WS_RE = re.compile(r"\s+")
# Different apostrophes (Об'єм / Обʼєм / Об`єм) must collapse to one so an alias key matches.
_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "`": "'", "´": "'"})
# A leading list marker ("1. ", "2) ", "а) ", "б) ") is layout, not identity — drop it.
_ENUM_RE = re.compile(r"^(?:\d+|[а-яіїєґ])[.)]\s+")


def normalize_analyte(name: str) -> str:
    """Normalize an analyte name to a grouping key: collapse spaces, unify apostrophes, drop a
    leading list enumerator, casefold, then apply the alias map. The enumerator/apostrophe steps let
    one lab's '1. З нормальною морфологією' group with another's plain spelling."""
    base = _WS_RE.sub(" ", name).strip().translate(_APOSTROPHES).casefold()
    base = _ENUM_RE.sub("", base)
    return ANALYTE_ALIASES.get(base, base)


# --- Specimen (body fluid) discriminator ----------------------------------------
#
# The SAME analyte name means a different thing in a different specimen: "Еритроцити" in blood is
# the RBC count, in urine it is sediment, in a spermogram it is yet another reading — with different
# units and ranges. Grouping a trend series by name ALONE merges these into one nonsensical chart.
# So the series key carries a coarse specimen bucket too, decided from the printed panel (reliable)
# with a small name fallback. A urine / semen row in practice always carries a section, so a row
# WITHOUT a recognizable section is treated as blood — keeping a section-less single-analyte report
# (e.g. a standalone ДІЛА Натрій) in the same series as its panel-printed twin.

_SEMEN, _URINE, _BLOOD = "semen", "urine", "blood"

# Section keyword -> specimen (checked first; "сперм"/"сеч" before the generic "кров").
_SPECIMEN_SECTION: tuple[tuple[str, str], ...] = (
    ("спермограм", _SEMEN),
    ("еякулят", _SEMEN),
    ("сперм", _SEMEN),
    ("сеч", _URINE),
    ("кров", _BLOOD),
    ("гематолог", _BLOOD),
    ("біохім", _BLOOD),
    ("гормон", _BLOOD),
    ("тиреоїд", _BLOOD),
    ("загальний аналіз", _BLOOD),
)

# Analyte-name fallback when the row has no recognizable section — only the strong semen signals;
# everything else falls through to blood (urine/semen rows carry a section in practice).
_SPECIMEN_ANALYTE_SEMEN: tuple[str, ...] = ("сперматозоїд", "еякулят", "спермі")

_KEY_SEP = "\x1f"  # unit separator: never appears in an analyte name


def specimen(section: str | None, analyte: str) -> str:
    """Coarse body-fluid bucket (blood / urine / semen) for a row, from its panel then its name.
    Internal only — used solely to keep same-named analytes in different specimens apart."""
    s = (section or "").casefold()
    for keyword, spec in _SPECIMEN_SECTION:
        if keyword in s:
            return spec
    a = analyte.casefold()
    if any(keyword in a for keyword in _SPECIMEN_ANALYTE_SEMEN):
        return _SEMEN
    return _BLOOD


def series_key(section: str | None, analyte: str) -> str:
    """The trend-series grouping key: specimen + normalized analyte name. Two rows share a series
    IFF they are the same analyte measured in the same specimen — so blood/urine/semen 'Еритроцити'
    are three separate series, never one merged chart."""
    return f"{specimen(section, analyte)}{_KEY_SEP}{normalize_analyte(analyte)}"


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


def is_out_of_range(
    value: float | None,
    ref_low: float | None,
    ref_high: float | None,
    out_of_range: bool | None,
) -> bool:
    """Whether a row deserves an attention marker (⚠️).

    The lab's own indicator wins when it says so; otherwise we escalate up — a value
    numerically outside its (lab-printed) reference is flagged even if the indicator
    was not captured. A value the lab did not flag and that is in range is not flagged.
    """
    if out_of_range:
        return True
    return compute_flag(value, ref_low, ref_high) in (ResultFlag.LOW, ResultFlag.HIGH)


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
        grouped[series_key(point.section, point.analyte)].append(point)
    for series in grouped.values():
        series.sort(key=lambda p: p.taken_on)
    return dict(grouped)


def find_series(series: dict[str, list[LabPoint]], analyte: str) -> list[LabPoint] | None:
    """Best series for a BARE analyte name (no specimen) — for ``/trend <name>``, where the user
    types only a name. Matches on the analyte-name part of the composite key; if the same name
    exists in several specimens, returns the richest series (most measurements)."""
    norm = normalize_analyte(analyte)
    matches = [pts for k, pts in series.items() if k.rsplit(_KEY_SEP, 1)[-1] == norm]
    return max(matches, key=len) if matches else None


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


def _series_ref(numeric: list[LabPoint]) -> tuple[float | None, float | None]:
    """The reference to judge the whole series by: the MOST RECENT measurement that actually carries
    a numeric bound. Older reports often captured the reference even when the latest did not, so a
    trend is classified against a real norm — and the caption matches the chart band (which already
    uses this same point) — instead of falsely saying 'немає референсних меж'."""
    for p in reversed(numeric):
        if p.ref_low is not None or p.ref_high is not None:
            return p.ref_low, p.ref_high
    return None, None


def compute_trend(points: list[LabPoint]) -> TrendSummary:
    """Compute the trend summary for a single analyte's series."""
    display = points[-1].analyte if points else ""
    key = series_key(points[-1].section, points[-1].analyte) if points else ""

    numeric = sorted((p for p in points if p.value is not None), key=lambda p: p.taken_on)
    latest = numeric[-1] if numeric else None
    previous = numeric[-2] if len(numeric) >= 2 else None
    ref_low, ref_high = _series_ref(numeric)  # one reference for the whole series (see above)

    if len(numeric) < 2:
        direction = TrendDirection.INSUFFICIENT_DATA
        delta = None
    else:
        assert latest is not None and previous is not None  # for type-narrowing
        # Classify against the series reference (latest/previous may not each carry it themselves).
        eff_latest = replace(latest, ref_low=ref_low, ref_high=ref_high)
        eff_previous = replace(previous, ref_low=ref_low, ref_high=ref_high)
        direction = _classify(eff_latest, eff_previous)
        delta = latest.value - previous.value  # type: ignore[operator]

    latest_flag = (
        compute_flag(latest.value, ref_low, ref_high) if latest is not None else ResultFlag.UNKNOWN
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
