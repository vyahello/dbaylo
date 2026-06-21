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
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dbaylo import locale
from dbaylo.companion import grouping, proactive
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.db.models import (
    Condition,
    ConditionStatus,
    LabReport,
    LabResult,
    Reminder,
    ReportKind,
    ReportStatus,
)
from dbaylo.labs.charts import render_trend_chart
from dbaylo.labs.labnames import normalize_lab
from dbaylo.labs.pipeline import load_series_points
from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport
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
    if filt.lab:
        # Canonicalize both sides so a filter typed in either spelling (Синево / Сінево /
        # Synevo) still matches the stored report.
        needle = (normalize_lab(filt.lab) or "").casefold()
        if needle not in (normalize_lab(report.lab) or "").casefold():
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
    limit: int | None = DEFAULT_LIMIT,
) -> list[LabReport]:
    """Confirmed reports, most recent first, optionally filtered. With ``limit`` it returns up to
    ``limit + 1`` (caller can tell there are more); ``limit=None`` returns all (paginated UI)."""
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
    return reports if limit is None else reports[: limit + 1]


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


# The expert reading is marked PENDING (summary == "") right before the slow LLM call and set to
# the real text after — so an empty summary means "interrupted by a restart", which is distinct
# from NULL (never analysed / the user deleted the розбір). Used to auto-recover on startup.
SUMMARY_PENDING = ""


async def find_interrupted_analyses(session: AsyncSession) -> list[LabReport]:
    """Confirmed reports whose analysis was started but never finished (summary == PENDING) —
    i.e. the process was restarted mid-interpretation. Offered for one-tap completion on startup."""
    stmt = (
        select(LabReport)
        .where(LabReport.status == ReportStatus.CONFIRMED, LabReport.summary == SUMMARY_PENDING)
        .order_by(LabReport.id)
    )
    return list((await session.scalars(stmt)).all())


def reconstruct_report(report: LabReport, results: list[LabResult]) -> ExtractedReport:
    """Rebuild an ``ExtractedReport`` from stored rows so a confirmed report can be re-interpreted
    (e.g. after an analysis was interrupted by a restart). The lab's own out-of-range mark is
    ``flagged``; ``ref_text`` and qualitative ``value_text`` are not persisted, so the numeric
    flags drive the re-reading. A narrative report carries its findings text instead of rows."""
    if report.kind == ReportKind.NARRATIVE:
        return ExtractedReport(
            results=[],
            report_date=report.report_date,
            lab=normalize_lab(report.lab),
            conclusion=report.conclusion,
            report_type=report.report_type,
            narrative=report.narrative,
        )
    return ExtractedReport(
        results=[
            ExtractedAnalyte(
                analyte=r.analyte,
                value=r.value,
                unit=r.unit,
                ref_low=r.ref_low,
                ref_high=r.ref_high,
                out_of_range=r.flagged,
                section=r.section,
            )
            for r in results
        ],
        report_date=report.report_date,
        lab=normalize_lab(report.lab),
        conclusion=report.conclusion,
    )


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
    seen: list[str] = []
    for name in rows.all():
        canon = normalize_lab(name)
        if canon and canon not in seen:
            seen.append(canon)
    return tuple(seen)


# --- Rendering (deterministic) --------------------------------------------------

# Sentinel so the first section (even a `None` one) registers as a change.
_NO_SECTION: object = object()


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


def flagged_count(results: list[LabResult]) -> int:
    return sum(1 for r in results if r.flagged)


def flagged_keys(results: list[LabResult]) -> set[str]:
    """Normalized analyte keys of the out-of-range rows (for the flagged-only dynamics view)."""
    return {normalize_analyte(r.analyte) for r in results if r.flagged}


@dataclass(frozen=True)
class TrendChartItem:
    """One pickable analyte in the charts picker: a display name, its normalized key, and
    whether it is out of range (flagged items are listed first)."""

    name: str
    key: str
    flagged: bool


async def list_report_trends(
    session: AsyncSession, *, user_id: int, report_id: int
) -> list[TrendChartItem]:
    """The report's analytes that actually have a trend worth a chart (measured on >=2 distinct
    dates), flagged-first then alphabetical — the data behind the charts PICKER, so we never
    dump dozens of images. Deterministic, no LLM."""
    report = await get_report(session, report_id=report_id, user_id=user_id)
    if report is None:
        return []
    series = build_series(await load_series_points(session, user_id))
    items: dict[str, TrendChartItem] = {}
    for r in ordered_results(report):
        key = normalize_analyte(r.analyte)
        points = series.get(key)
        if not points or len({p.taken_on for p in points}) < 2:  # a same-day re-upload is no trend
            continue
        prev = items.get(key)
        items[key] = TrendChartItem(
            name=prev.name if prev else r.analyte,
            key=key,
            flagged=bool(r.flagged) or (prev.flagged if prev else False),
        )
    return sorted(items.values(), key=lambda it: (not it.flagged, it.name.casefold()))


# --- Cross-lab dynamics browser: indicators grouped by clinical category --------


@dataclass(frozen=True)
class IndicatorItem:
    """One analyte in the dynamics browser: its category, whether it has a multi-date trend,
    and whether its most recent value was out of range."""

    name: str
    key: str
    category: str
    has_trend: bool
    last_flagged: bool


async def aggregate_indicators(session: AsyncSession, *, user_id: int) -> list[IndicatorItem]:
    """Every analyte the user has across ALL confirmed tabular reports/labs, with its category,
    trend availability (>=2 distinct dates), and last-out-of-range flag. Deterministic, no LLM."""
    stmt = (
        select(
            LabResult.analyte,
            LabResult.section,
            LabResult.value,
            LabResult.flagged,
            LabReport.report_date,
        )
        .join(LabReport, LabResult.report_id == LabReport.id)
        .where(
            LabReport.user_id == user_id,
            LabReport.status == ReportStatus.CONFIRMED,
            LabReport.kind == ReportKind.TABULAR,
            LabReport.report_date.is_not(None),
        )
    )
    rows = (await session.execute(stmt)).all()
    by_key: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        by_key[normalize_analyte(r.analyte)].append(r)
    items: list[IndicatorItem] = []
    for key, group in by_key.items():
        group.sort(key=lambda r: r.report_date)
        latest = group[-1]
        dated: set[date] = {r.report_date for r in group if r.value is not None}
        if len(dated) < 2:
            # No chartable numeric trend (a qualitative analyte like urine crystals has 0 numeric
            # values; a one-off has 1). The dynamics browser is for trends, so skip it — tapping it
            # would only say "замало даних". Such results are still seen per-report in /history.
            continue
        items.append(
            IndicatorItem(
                name=latest.analyte,
                key=key,
                category=grouping.categorize(latest.section, latest.analyte),
                has_trend=True,
                last_flagged=bool(latest.flagged),
            )
        )
    return items


def category_counts(items: list[IndicatorItem], narrative_count: int) -> list[tuple[str, int]]:
    """Non-empty categories (in display order) with their counts; the imaging category carries
    the count of narrative documents (МРТ/УЗД)."""
    counts: Counter[str] = Counter(it.category for it in items)
    if narrative_count:
        counts[grouping.IMAGING] = narrative_count
    return [(c, counts[c]) for c in grouping.CATEGORY_ORDER if counts.get(c)]


def indicators_in(items: list[IndicatorItem], category: str) -> list[IndicatorItem]:
    """Indicators of one category, flagged first, then those with a trend, then alphabetical."""
    chosen = [it for it in items if it.category == category]
    return sorted(
        chosen, key=lambda it: (not it.last_flagged, not it.has_trend, it.name.casefold())
    )


async def list_narratives(session: AsyncSession, *, user_id: int) -> list[LabReport]:
    """The user's confirmed narrative documents (МРТ/УЗД/висновок), most recent first."""
    stmt = (
        select(LabReport)
        .where(
            LabReport.user_id == user_id,
            LabReport.status == ReportStatus.CONFIRMED,
            LabReport.kind == ReportKind.NARRATIVE,
        )
        .order_by(LabReport.report_date.is_(None), LabReport.report_date.desc())
    )
    return list((await session.scalars(stmt)).all())


# Narrative study types can be long ("КТ … з внутрішньовенним контрастуванням"); truncate them on
# a LIST BUTTON so the row stays tidy. The full type is shown on the card when the report is opened.
_LABEL_TYPE_MAX = 34


def short_type(report_type: str | None) -> str:
    """A narrative study type, shortened (…) to fit a one-line list button."""
    text = report_type or locale.LAB_DOC_GENERIC
    return text if len(text) <= _LABEL_TYPE_MAX else text[: _LABEL_TYPE_MAX - 1].rstrip() + "…"


def report_button_label(report: LabReport, results: list[LabResult]) -> str:
    """The one-line button label for a report in the master list."""
    date_txt = report.report_date.isoformat() if report.report_date else locale.HIST_NO_DATE
    lab_txt = normalize_lab(report.lab) or locale.LAB_LAB_UNKNOWN
    if report.kind == ReportKind.NARRATIVE:
        rtype = short_type(report.report_type)  # truncate the long study name on the button
        if report.lab:  # an imaging study often has no lab brand — then its type IS its identity
            return locale.HIST_BTN_REPORT_DOC.format(date=date_txt, lab=lab_txt, report_type=rtype)
        return locale.HIST_BTN_REPORT_DOC_NOLAB.format(date=date_txt, report_type=rtype)
    n_flagged = flagged_count(results)
    flags = locale.HIST_FLAGS_SUFFIX.format(n=n_flagged) if n_flagged else ""
    return locale.HIST_BTN_REPORT.format(
        date=date_txt, lab=lab_txt, count=len(results), flags=flags
    )


def render_card(report: LabReport, results: list[LabResult]) -> str:
    """The per-report 'card' shown when a report is opened from the list."""
    date_txt = report.report_date.isoformat() if report.report_date else locale.HIST_NO_DATE
    lab_txt = normalize_lab(report.lab) or locale.LAB_LAB_UNKNOWN
    if report.kind == ReportKind.NARRATIVE:
        rtype = report.report_type or locale.LAB_DOC_GENERIC
        if report.lab:
            return locale.HIST_CARD_DOC.format(date=date_txt, lab=lab_txt, report_type=rtype)
        return locale.HIST_CARD_DOC_NOLAB.format(date=date_txt, report_type=rtype)
    n_flagged = flagged_count(results)
    status = locale.HIST_CARD_FLAGGED.format(n=n_flagged) if n_flagged else locale.HIST_CARD_NORMAL
    return locale.HIST_CARD.format(date=date_txt, lab=lab_txt, count=len(results), status=status)


def _ps(text: str) -> str:
    """Append the plain-text P.S. disclaimer block (consistent across every health view)."""
    return f"{text}\n\n{locale.HIST_PS_BLOCK}"


# Collapsing in-range rows into an aggregate helps only when there are many; a handful is shown
# by name (a single-analyte report should never hide its one result behind "Усі N в нормі").
_INLINE_NORMAL_MAX = 5


def _result_line(r: LabResult, marker: str = "") -> str:
    value = f"{r.value:g}" if r.value is not None else "—"
    if r.unit:
        value = f"{value} {r.unit}"
    ref = _ref_text(r.ref_low, r.ref_high)
    return f"• {r.analyte} — {value} ({locale.LAB_NORM_LABEL} {ref}) {marker}".rstrip()


def _grouped_result_lines(results: list[LabResult], marker: str) -> list[str]:
    out: list[str] = []
    prev_section: object = _NO_SECTION
    for r in results:
        if r.section != prev_section:
            prev_section = r.section
            if r.section:
                out.append(locale.LAB_SECTION_HEADER.format(section=r.section))
        out.append(_result_line(r, marker))
    return out


def render_problems(report: LabReport, results: list[LabResult]) -> str:
    """Focused results view: the lab conclusion + the out-of-range rows (grouped by panel). The
    in-range rows are listed by name when there are only a few, or collapsed into an aggregate
    when there are many — never an 85-row dump."""
    date_txt = report.report_date.isoformat() if report.report_date else locale.HIST_NO_DATE
    lab_txt = normalize_lab(report.lab) or locale.LAB_LAB_UNKNOWN
    lines = [locale.HIST_RESULTS_HEADER.format(date=date_txt, lab=lab_txt)]
    if report.kind == ReportKind.NARRATIVE:
        if report.report_type:
            lines.append(f"{locale.LAB_TYPE_LABEL}: {report.report_type}")
        if report.conclusion:
            lines += ["", f"{locale.LAB_CONCLUSION_LABEL}: {report.conclusion}"]
        elif report.narrative:
            lines += ["", report.narrative]
        return assert_safe_output(_ps("\n".join(lines)))
    if report.conclusion:
        lines.append(f"{locale.LAB_CONCLUSION_LABEL}: {report.conclusion}")
    flagged = [r for r in results if r.flagged]
    normal = [r for r in results if not r.flagged]
    collapse = len(normal) > _INLINE_NORMAL_MAX
    if flagged:
        lines += ["", locale.HIST_PROBLEMS_HEADER.format(n=len(flagged))]
        lines += _grouped_result_lines(flagged, locale.FLAG_ATTENTION)
    if normal and collapse:
        lines += ["", locale.HIST_PROBLEMS_NORMAL_AGG.format(n=len(normal))]
    elif normal:
        lines += ["", locale.HIST_PROBLEMS_NORMAL_HEADER]
        lines += _grouped_result_lines(normal, "")
    elif not flagged:  # no rows at all (defensive)
        lines += ["", locale.HIST_NO_PROBLEMS]
    return assert_safe_output(_ps("\n".join(lines)))


def render_report_line(report: LabReport, results: list[LabResult]) -> str:
    date_txt = report.report_date.isoformat() if report.report_date else locale.HIST_NO_DATE
    lab_txt = normalize_lab(report.lab) or locale.LAB_LAB_UNKNOWN
    if report.kind == ReportKind.NARRATIVE:
        line = locale.HIST_REPORT_LINE_DOC.format(
            date=date_txt, lab=lab_txt, report_type=report.report_type or locale.LAB_DOC_GENERIC
        ).rstrip()
    else:
        line = locale.HIST_REPORT_LINE.format(
            date=date_txt, lab=lab_txt, count=len(results), flags=report_flags(results)
        ).rstrip()
    uploaded = report.created_at.date().isoformat() if report.created_at else "?"
    return f"{line}\n{locale.HIST_REPORT_UPLOADED.format(uploaded=uploaded)}"


def render_report_results(report: LabReport, results: list[LabResult]) -> str:
    date_txt = report.report_date.isoformat() if report.report_date else locale.HIST_NO_DATE
    lab_txt = normalize_lab(report.lab) or locale.LAB_LAB_UNKNOWN
    lines = [locale.HIST_RESULTS_HEADER.format(date=date_txt, lab=lab_txt)]
    if report.kind == ReportKind.NARRATIVE:
        if report.report_type:
            lines.append(f"{locale.LAB_TYPE_LABEL}: {report.report_type}")
        if report.narrative:
            lines += ["", report.narrative]
        if report.conclusion:
            lines += ["", f"{locale.LAB_CONCLUSION_LABEL}: {report.conclusion}"]
    else:
        if report.conclusion:
            lines.append(f"{locale.LAB_CONCLUSION_LABEL}: {report.conclusion}")
        lines.append("")
        prev_section: object = _NO_SECTION
        for i, r in enumerate(results, 1):
            if r.section != prev_section:
                prev_section = r.section
                if r.section:
                    if lines[-1] != "":
                        lines.append("")
                    lines.append(locale.LAB_SECTION_HEADER.format(section=r.section))
            emoji = locale.FLAG_ATTENTION if r.flagged else locale.FLAG_EMOJI["normal"]
            value = f"{r.value:g}" if r.value is not None else "—"
            if r.unit:
                value = f"{value} {r.unit}"
            ref = _ref_text(r.ref_low, r.ref_high)
            lines.append(
                f"{i}. {r.analyte} — {value} ({locale.LAB_NORM_LABEL} {ref}) {emoji}".rstrip()
            )
    # The expert reading is a SEPARATE view now (🔬 Розбір) — the full table is just the data.
    return assert_safe_output(_ps("\n".join(lines)))


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
    text = assert_safe_output(text)
    if chart is not None:
        return TrendView(
            found=True, text=text, chart=chart
        )  # caption: no disclaimer on a data chart
    # Text-only (insufficient data) keeps the disclaimer, like every other plain health reply.
    return TrendView(
        found=True, text=f"{text}\n{locale.TREND_INSUFFICIENT}\n\n{DISCLAIMER}", chart=None
    )


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
