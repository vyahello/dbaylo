"""Deterministic health analyzer — the "knows your whole picture" core. NO LLM, NO diagnosis.

It scans ALL confirmed lab data for a user and decides, purely in code and only in DATA terms
(rail #4 — never a clinical verdict):

* **Current** findings — analytes whose LATEST measurement is out of range (the lab's own flag, or a
  numeric value outside its reference). These are what's actually off RIGHT NOW.
* **Resolved** findings — analytes that WERE out of range historically but the latest is back in
  range. Remembered, not emphasised — the owner's "if a problem was there but passed, remember it".

Plus the manually tracked concerns. From this it builds a grounded context string the companion, the
symptom intake and the proactive check-in answer / ask FROM — so Дбайло engages like an assistant
who actually knows the person, grounded in real data instead of guessing.

Pure-ish: a DB read fed to the deterministic trend engine. No LLM here (the analyzer must never
"diagnose" — it only states what the numbers say); phrasing happens downstream, always guarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import concerns
from dbaylo.db.models import ResultFlag
from dbaylo.labs.pipeline import load_series_points
from dbaylo.labs.trends import (
    LabPoint,
    TrendSummary,
    build_series,
    compute_trend,
    is_out_of_range,
)

_FLAG_TEXT = {
    ResultFlag.HIGH: "above its reference (HIGH)",
    ResultFlag.LOW: "below its reference (LOW)",
}


@dataclass(frozen=True)
class HealthFinding:
    """One analyte's current status, in data terms (never a diagnosis)."""

    name: str
    value: str  # "169 г/л" or the qualitative text
    ref: str  # "≤ 160" / "3.9–6.1" / "—"
    flag_text: str  # "above its reference (HIGH)" / "flagged by the lab"
    direction: str  # the range-relative TrendDirection name (e.g. "LEFT_RANGE")
    last_date: date | None
    n_points: int
    # A small UI-facing severity tag (never a diagnosis): "high" / "low" / "watch" / "flag". Lets
    # the Ukrainian renderer pick a marker + phrase without re-deriving the numbers; default "flag".
    kind: str = "flag"


@dataclass(frozen=True)
class HealthPicture:
    """The deterministic read of the user's whole lab history."""

    current: list[HealthFinding] = field(default_factory=list)
    watch: list[HealthFinding] = field(default_factory=list)  # in range but trending toward a bound
    resolved: list[HealthFinding] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)


# How close to a reference bound (a fraction of the range width, or of the bound) a still-in-range
# value must be — while trending toward it — to count as an early-warning "watch".
_WATCH_MARGIN = 0.15


def _series_bounds(numeric: list[LabPoint]) -> tuple[float | None, float | None]:
    """The reference to judge the series by: the most recent point that carries a numeric bound."""
    for point in reversed(numeric):
        if point.ref_low is not None or point.ref_high is not None:
            return point.ref_low, point.ref_high
    return None, None


def _watch_direction(numeric: list[LabPoint]) -> str | None:
    """If the LATEST value is IN range but moving toward — and near — a bound, describe that
    early-warning trend; else ``None``. Deterministic, never a verdict."""
    if len(numeric) < 2:
        return None
    latest, previous = numeric[-1], numeric[-2]
    low, high = _series_bounds(numeric)
    value, prev = latest.value, previous.value
    if value is None or prev is None:
        return None
    if (low is not None and value < low) or (high is not None and value > high):
        return None  # already out of range -> handled as "current", not a watch
    width = (high - low) if (low is not None and high is not None) else None
    if high is not None and value > prev:  # rising toward the upper bound
        margin = _WATCH_MARGIN * (width if width else abs(high))
        if value >= high - margin:
            return "approaching its UPPER limit (still in range, but trending up toward it)"
    if low is not None and value < prev:  # falling toward the lower bound
        margin = _WATCH_MARGIN * (width if width else abs(low))
        if value <= low + margin:
            return "approaching its LOWER limit (still in range, but trending down toward it)"
    return None


def _ref_text(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{low:g}–{high:g}"
    if high is not None:
        return f"≤ {high:g}"
    if low is not None:
        return f"≥ {low:g}"
    return "—"


def _value_text(point: LabPoint) -> str:
    if point.value is None:
        return point.value_text or "—"
    return f"{point.value:g} {point.unit}".strip() if point.unit else f"{point.value:g}"


_FLAG_KIND = {ResultFlag.HIGH: "high", ResultFlag.LOW: "low"}


def _finding(latest: LabPoint, summary: TrendSummary, n_points: int) -> HealthFinding:
    flag_text = _FLAG_TEXT.get(summary.latest_flag, "flagged by the lab")
    return HealthFinding(
        name=summary.analyte or latest.analyte,
        value=_value_text(latest),
        ref=_ref_text(latest.ref_low, latest.ref_high),
        flag_text=flag_text,
        direction=summary.direction.name,
        last_date=latest.taken_on,
        n_points=n_points,
        kind=_FLAG_KIND.get(summary.latest_flag, "flag"),
    )


def _is_oor(point: LabPoint) -> bool:
    return is_out_of_range(
        point.value, point.ref_low, point.ref_high, point.flagged, point.value_text
    )


async def analyze_health(session: AsyncSession, user_id: int, *, today: date) -> HealthPicture:
    """Deterministically read the user's whole lab history into current / resolved findings."""
    series = build_series(await load_series_points(session, user_id))
    current: list[HealthFinding] = []
    watch: list[HealthFinding] = []
    resolved: list[HealthFinding] = []
    for points in series.values():
        if not points:
            continue
        latest = points[-1]  # build_series sorts ascending by date
        finding = _finding(latest, compute_trend(points), len(points))
        if _is_oor(latest):
            current.append(finding)
            continue
        numeric = [p for p in points if p.value is not None]  # already date-ascending
        watch_text = _watch_direction(numeric)
        if watch_text is not None:  # in range but trending toward a bound — early warning
            watch.append(replace(finding, flag_text=watch_text, kind="watch"))
        elif any(_is_oor(p) for p in points):  # was off before, latest is back in range
            resolved.append(finding)
    by_date = lambda f: f.last_date or date.min  # noqa: E731
    current.sort(key=by_date, reverse=True)
    watch.sort(key=by_date, reverse=True)
    resolved.sort(key=by_date, reverse=True)
    conditions = await concerns.list_active(session, user_id=user_id)
    return HealthPicture(
        current=current,
        watch=watch,
        resolved=resolved,
        concerns=[c.name for c in conditions if c.name],
    )


def _norm(name: str) -> str:
    return name.casefold().strip()


def _already_known(finding_name: str, existing: list[str]) -> bool:
    """Whether ``finding_name`` matches a concern the user already tracks/dismissed. Matches on an
    exact name or the analyte CORE (the part before any '(ABBR)') appearing in the concern name — so
    a finding ``Гемоглобін (HGB)`` is covered by a stored ``Гемоглобін (HGB)`` (exact) and by the
    legacy ``Гемоглобін поза нормою`` (core substring), but an unrelated manual concern isn't."""
    n = _norm(finding_name)
    core = n.split("(", 1)[0].strip()
    for raw in existing:
        e = _norm(raw)
        if n == e or (core and core in e):
            return True
    return False


async def propose_problems(
    session: AsyncSession, user_id: int, *, today: date
) -> list[HealthFinding]:
    """What the AGENT would propose to track: currently out-of-range indicators first, then
    in-range-but-trending ones (watch), EXCLUDING anything the user already tracks or has dismissed.
    Deterministic, data-only — the user confirms; the agent never decides escalation."""
    picture = await analyze_health(session, user_id, today=today)
    existing = await concerns.names_active_or_dismissed(session, user_id=user_id)
    return [f for f in (*picture.current, *picture.watch) if not _already_known(f.name, existing)]


async def has_current_flags(session: AsyncSession, user_id: int, *, today: date) -> bool:
    """True iff any indicator is currently out of range AND not waved off — drives the proactive
    check-in. A finding the user dismissed ("Не турбує") no longer keeps the check-in alive."""
    picture = await analyze_health(session, user_id, today=today)
    if not picture.current:
        return False
    dismissed = await concerns.names_dismissed(session, user_id=user_id)
    return any(not _already_known(f.name, dismissed) for f in picture.current)


async def should_have_checkin(session: AsyncSession, user_id: int, *, today: date) -> bool:
    """Whether the user should get a daily check-in: they track an active concern, OR the data shows
    a currently out-of-range indicator (so Дбайло proactively checks in on a real problem even with
    no concern added manually). Lives here so both ``proactive`` and ``scheduler`` can use it (the
    scheduler must not import ``proactive`` — that would be a cycle)."""
    if await concerns.count_active(session, user_id=user_id) > 0:
        return True
    return await has_current_flags(session, user_id, today=today)


async def build_health_context(session: AsyncSession, user_id: int, *, today: date) -> str:
    """The grounded context the companion / intake / check-in draw on: the patient profile, then the
    deterministic CURRENT out-of-range indicators and the resolved-but-remembered ones. ``""`` when
    there is nothing to ground in, so the caller answers generally."""
    # Lazy import: consult_context -> history -> scheduler, and scheduler -> checkin -> health would
    # otherwise be a module-load cycle. At call time everything is already imported.
    from dbaylo.companion.consult_context import patient_profile

    parts = []
    profile = await patient_profile(session, user_id, today)
    if profile:
        parts.append(profile)
    picture = await analyze_health(session, user_id, today=today)
    if picture.current:
        lines = [
            "CURRENTLY out-of-range indicators (the LATEST measurement is outside its reference — "
            "a DATA fact, never a diagnosis; do not invent a cause):"
        ]
        for f in picture.current:
            lines.append(
                f"- {f.name}: {f.value} (ref {f.ref}) — {f.flag_text}; trend {f.direction}; "
                f"latest {f.last_date.isoformat() if f.last_date else '?'}."
            )
        parts.append("\n".join(lines))
    if picture.watch:
        lines = [
            "EARLY WARNING — still in range but trending toward a limit (worth WATCHING, not yet a "
            "problem; mention gently, never alarm or diagnose):"
        ]
        for f in picture.watch:
            lines.append(
                f"- {f.name}: {f.value} (ref {f.ref}) — {f.flag_text}; "
                f"latest {f.last_date.isoformat() if f.last_date else '?'}."
            )
        parts.append("\n".join(lines))
    if picture.resolved:
        lines = [
            "Was out of range before but the LATEST is back in range (REMEMBER it, but do not "
            "dwell on it unless asked):"
        ]
        for f in picture.resolved:
            lines.append(
                f"- {f.name}: now {f.value} (ref {f.ref}); "
                f"latest {f.last_date.isoformat() if f.last_date else '?'}."
            )
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
