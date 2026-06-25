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

from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class HealthPicture:
    """The deterministic read of the user's whole lab history."""

    current: list[HealthFinding] = field(default_factory=list)
    resolved: list[HealthFinding] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)


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
    )


def _is_oor(point: LabPoint) -> bool:
    return is_out_of_range(
        point.value, point.ref_low, point.ref_high, point.flagged, point.value_text
    )


async def analyze_health(session: AsyncSession, user_id: int, *, today: date) -> HealthPicture:
    """Deterministically read the user's whole lab history into current / resolved findings."""
    series = build_series(await load_series_points(session, user_id))
    current: list[HealthFinding] = []
    resolved: list[HealthFinding] = []
    for points in series.values():
        if not points:
            continue
        latest = points[-1]  # build_series sorts ascending by date
        finding = _finding(latest, compute_trend(points), len(points))
        if _is_oor(latest):
            current.append(finding)
        elif any(_is_oor(p) for p in points):  # was off before, latest is back in range
            resolved.append(finding)
    current.sort(key=lambda f: f.last_date or date.min, reverse=True)
    resolved.sort(key=lambda f: f.last_date or date.min, reverse=True)
    conditions = await concerns.list_active(session, user_id=user_id)
    return HealthPicture(
        current=current,
        resolved=resolved,
        concerns=[c.name for c in conditions if c.name],
    )


async def has_current_flags(session: AsyncSession, user_id: int, *, today: date) -> bool:
    """True iff any indicator is currently out of range — drives the proactive check-in."""
    picture = await analyze_health(session, user_id, today=today)
    return bool(picture.current)


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
