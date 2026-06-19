"""History & retrieval — deterministic (no LLM): list reports, render results, send
the original file, single-analyte trends, delete (with Tier 1.1 coupling cleanup),
and a deterministic NL→filter parser.

Listing/rendering/trends need no model call. The only natural-language seam (the NL
search) parses filters with regex/keywords here; it is gated by ``safety.gate`` in
the bot layer *before* this runs.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dbaylo import locale
from dbaylo.companion import proactive
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.db.models import (
    Condition,
    ConditionStatus,
    LabReport,
    LabResult,
    Reminder,
    ReportStatus,
)
from dbaylo.labs.charts import render_trend_chart
from dbaylo.labs.pipeline import load_series_points
from dbaylo.labs.trends import build_series, compute_trend, normalize_analyte
from dbaylo.triage.safety import DISCLAIMER, assert_safe_output

DEFAULT_LIMIT = 10
PENDING_GRACE = timedelta(hours=1)

# Lab-name keywords used only for DB-free routing/intent (the handler also matches the
# user's *actual* lab names). Lower-case.
LAB_KEYWORDS = (
    "synevo",
    "синево",
    "сінево",
    "dila",
    "діла",
    "есл",
    "ескулаб",
    "eurolab",
    "євролаб",
    "інвітро",
    "invitro",
    "медлаб",
)
_MONTH_STEMS = {
    "січ": 1,
    "лют": 2,
    "берез": 3,
    "квіт": 4,
    "трав": 5,
    "черв": 6,
    "лип": 7,
    "серп": 8,
    "вер": 9,
    "жовт": 10,
    "листоп": 11,
    "груд": 12,
}
_INTENT_RE = re.compile(r"\b(анал\w*|звіт\w*|результат\w*|динамік\w*|тренд\w*)\b", re.IGNORECASE)
_YMD_RE = re.compile(r"\b(20\d\d)-(\d{1,2})(?:-(\d{1,2}))?\b")
_YEAR_RE = re.compile(r"\b(20\d\d)\b")
_LATEST_RE = re.compile(r"останн", re.IGNORECASE)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


# --- Deterministic NL parsing (no LLM) ------------------------------------------


@dataclass(frozen=True)
class HistoryFilter:
    lab: str | None = None
    year: int | None = None
    month: int | None = None
    day: int | None = None
    latest: bool = False

    @property
    def has_filter(self) -> bool:
        return bool(self.lab or self.year or self.month or self.day or self.latest)


def is_history_intent(text: str) -> bool:
    lowered = text.casefold()
    return bool(_INTENT_RE.search(lowered)) or any(kw in lowered for kw in LAB_KEYWORDS)


def _has_concrete_token(text: str) -> bool:
    lowered = text.casefold()
    return bool(
        _LATEST_RE.search(lowered)
        or _YEAR_RE.search(lowered)
        or any(stem in lowered for stem in _MONTH_STEMS)
        or any(kw in lowered for kw in LAB_KEYWORDS)
    )


def is_history_query(text: str) -> bool:
    """Route to history NL only on intent + a concrete token (else -> companion)."""
    return is_history_intent(text) and _has_concrete_token(text)


def parse_history_query(text: str, *, known_labs: tuple[str, ...] = ()) -> HistoryFilter:
    """Extract lab / date / month / latest from text (deterministic). No LLM."""
    lowered = text.casefold()
    year = month = day = None
    if m := _YMD_RE.search(lowered):
        year, month = int(m.group(1)), int(m.group(2))
        day = int(m.group(3)) if m.group(3) else None
    else:
        if ym := _YEAR_RE.search(lowered):
            year = int(ym.group(1))
        for stem, num in _MONTH_STEMS.items():
            if stem in lowered:
                month = num
                break

    lab = next((name for name in known_labs if name and name.casefold() in lowered), None)
    if lab is None:
        lab = next((kw for kw in LAB_KEYWORDS if kw in lowered), None)

    latest = bool(_LATEST_RE.search(lowered))
    return HistoryFilter(lab=lab, year=year, month=month, day=day, latest=latest)


# --- Listing --------------------------------------------------------------------


def _matches(report: LabReport, filt: HistoryFilter) -> bool:
    if filt.lab and filt.lab.casefold() not in (report.lab or "").casefold():
        return False
    d = report.report_date
    if filt.year and (d is None or d.year != filt.year):
        return False
    if filt.month and (d is None or d.month != filt.month):
        return False
    return not (filt.day and (d is None or d.day != filt.day))


async def list_confirmed(
    session: AsyncSession,
    *,
    user_id: int,
    filt: HistoryFilter | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[LabReport]:
    """Confirmed reports, most recent first, optionally filtered. Returns up to
    ``limit + 1`` so the caller can tell there are more."""
    stmt = (
        select(LabReport)
        .where(LabReport.user_id == user_id, LabReport.status == ReportStatus.CONFIRMED)
        .options(selectinload(LabReport.results))
        .order_by(LabReport.report_date.desc().nullslast(), LabReport.created_at.desc())
    )
    reports = list((await session.scalars(stmt)).all())
    if filt is not None and filt.has_filter:
        reports = [r for r in reports if _matches(r, filt)]
        if filt.latest:
            reports = reports[:1]
    return reports[: limit + 1]


async def get_report(session: AsyncSession, *, report_id: int, user_id: int) -> LabReport | None:
    """One confirmed, owned report with its results eagerly loaded (for callbacks)."""
    report: LabReport | None = await session.scalar(
        select(LabReport)
        .where(
            LabReport.id == report_id,
            LabReport.user_id == user_id,
            LabReport.status == ReportStatus.CONFIRMED,
        )
        .options(selectinload(LabReport.results))
    )
    return report


def ordered_results(report: LabReport) -> list[LabResult]:
    """Results in a stable order so list indices match the trend-button callbacks."""
    return sorted(report.results, key=lambda r: r.id)


async def known_labs(session: AsyncSession, *, user_id: int) -> tuple[str, ...]:
    """Distinct non-empty lab names from the user's confirmed reports."""
    rows = await session.scalars(
        select(LabReport.lab)
        .where(LabReport.user_id == user_id, LabReport.status == ReportStatus.CONFIRMED)
        .distinct()
    )
    return tuple(name for name in rows.all() if name)


# --- Rendering (deterministic) --------------------------------------------------


def _ref_text(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{low:g}–{high:g}"
    if high is not None:
        return f"≤ {high:g}"
    if low is not None:
        return f"≥ {low:g}"
    return "—"


def report_flags(results: list[LabResult]) -> str:
    # ⚠️ if the lab marked anything for attention (or a value was out of range); the
    # ``flagged`` mark is the source of truth (computed at confirm with the lab's own
    # indicator). A report with nothing flagged shows no marker.
    return locale.FLAG_ATTENTION if any(r.flagged for r in results) else ""


def render_report_line(report: LabReport, results: list[LabResult]) -> str:
    date_txt = report.report_date.isoformat() if report.report_date else locale.HIST_NO_DATE
    line = locale.HIST_REPORT_LINE.format(
        date=date_txt,
        lab=report.lab or locale.LAB_LAB_UNKNOWN,
        count=len(results),
        flags=report_flags(results),
    ).rstrip()
    uploaded = report.created_at.date().isoformat() if report.created_at else "?"
    return f"{line}\n{locale.HIST_REPORT_UPLOADED.format(uploaded=uploaded)}"


def render_report_results(report: LabReport, results: list[LabResult]) -> str:
    date_txt = report.report_date.isoformat() if report.report_date else locale.HIST_NO_DATE
    lab_txt = report.lab or locale.LAB_LAB_UNKNOWN
    lines = [locale.HIST_RESULTS_HEADER.format(date=date_txt, lab=lab_txt)]
    if report.conclusion:
        lines.append(f"{locale.LAB_CONCLUSION_LABEL}: {report.conclusion}")
    lines.append("")
    for i, r in enumerate(results, 1):
        emoji = locale.FLAG_ATTENTION if r.flagged else locale.FLAG_EMOJI["normal"]
        value = f"{r.value:g}" if r.value is not None else "—"
        if r.unit:
            value = f"{value} {r.unit}"
        ref = _ref_text(r.ref_low, r.ref_high)
        lines.append(f"{i}. {r.analyte} — {value} ({locale.LAB_NORM_LABEL} {ref}) {emoji}".rstrip())
    if report.summary:  # the saved expert interpretation (already safe + has the disclaimer)
        lines += ["", report.summary]
    return assert_safe_output("\n".join(lines))


def report_file_path(report: LabReport) -> Path | None:
    if not report.source_file:
        return None
    path = Path(report.source_file)
    return path if path.is_file() else None


def _remove_file(report: LabReport) -> None:
    path = report_file_path(report)
    if path is not None:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


# --- Single-analyte trend (reuses the deterministic engine) ---------------------


@dataclass(frozen=True)
class TrendView:
    found: bool
    text: str
    chart: bytes | None


async def trend_for_analyte(session: AsyncSession, *, user_id: int, analyte: str) -> TrendView:
    points = await load_series_points(session, user_id)
    series = build_series(points)
    pts = series.get(normalize_analyte(analyte))
    if not pts:
        return TrendView(found=False, text=f"{locale.TREND_NOT_FOUND}\n\n{DISCLAIMER}", chart=None)

    summary = compute_trend(pts)
    latest = summary.latest
    value = "—"
    if latest is not None and latest.value is not None:
        value = f"{latest.value:g} {summary.unit}".strip()
    movement = locale.TREND_PHRASES.get(summary.direction.name, "")
    text = locale.TREND_LINE.format(
        analyte=summary.analyte, value=value, movement=movement, n=summary.n_points
    )
    chart = render_trend_chart(pts, title=summary.analyte) if summary.n_points >= 2 else None
    if chart is None:
        text = f"{text}\n{locale.TREND_INSUFFICIENT}"
    return TrendView(found=True, text=f"{assert_safe_output(text)}\n\n{DISCLAIMER}", chart=chart)


# --- Delete (with Tier 1.1 coupling cleanup) ------------------------------------


async def linked_active_concerns(session: AsyncSession, report_id: int) -> list[Condition]:
    rows = await session.scalars(
        select(Condition).where(
            Condition.report_id == report_id, Condition.status == ConditionStatus.ACTIVE
        )
    )
    return list(rows.all())


async def linked_active_reminders(session: AsyncSession, report_id: int) -> list[Reminder]:
    rows = await session.scalars(
        select(Reminder).where(Reminder.report_id == report_id, Reminder.active.is_(True))
    )
    return list(rows.all())


async def delete_report(
    session: AsyncSession, *, report: LabReport, scheduler: ReminderScheduler
) -> None:
    """Hard-delete the file + report + results, and clean up Tier 1.1 couplings:
    resolve a concern proposed from this report (so it stops pinging) and retire its
    repeat-lab reminder. The nightly backup is the safety net."""
    report_id = report.id
    user_id = report.user_id
    for condition in await linked_active_concerns(session, report_id):
        await proactive.resolve_problem(
            session, user_id=user_id, condition_id=condition.id, scheduler=scheduler
        )
    for reminder in await linked_active_reminders(session, report_id):
        await proactive.turn_off_reminder(session, reminder=reminder, scheduler=scheduler)
    # Drop the now-stale report link so deleting the report leaves nothing dangling.
    await session.execute(
        update(Condition).where(Condition.report_id == report_id).values(report_id=None)
    )
    await session.execute(
        update(Reminder).where(Reminder.report_id == report_id).values(report_id=None)
    )
    _remove_file(report)
    await session.delete(report)  # cascade removes LabResults
    await session.flush()


# --- Orphaned uploads (opt-in cleanup) ------------------------------------------


async def _orphans(session: AsyncSession, *, user_id: int, now: datetime) -> list[LabReport]:
    rows = await session.scalars(
        select(LabReport).where(
            LabReport.user_id == user_id,
            LabReport.status.in_([ReportStatus.PENDING, ReportStatus.DISCARDED]),
        )
    )
    threshold = _naive(now) - PENDING_GRACE
    out: list[LabReport] = []
    for r in rows.all():
        # DISCARDED uploads are always junk; PENDING ones only after a grace period
        # (a fresh upload may still be mid-confirmation).
        stale = r.created_at is None or _naive(r.created_at) <= threshold
        if r.status == ReportStatus.DISCARDED or stale:
            out.append(r)
    return out


async def count_orphans(session: AsyncSession, *, user_id: int, now: datetime) -> int:
    return len(await _orphans(session, user_id=user_id, now=now))


async def cleanup_orphans(session: AsyncSession, *, user_id: int, now: datetime) -> int:
    orphans = await _orphans(session, user_id=user_id, now=now)
    for report in orphans:
        _remove_file(report)
        await session.delete(report)
    await session.flush()
    return len(orphans)
