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
    ConsultMemory,
    LabReport,
    LabResult,
    Reminder,
    ReportKind,
    ReportStatus,
    ResultFlag,
)
from dbaylo.labs.charts import render_trend_chart
from dbaylo.labs.labnames import normalize_lab
from dbaylo.labs.pipeline import load_series_points
from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport
from dbaylo.labs.trends import (
    LabPoint,
    TrendSummary,
    build_series,
    compute_trend,
    find_series,
    is_negative_qualitative,
    series_key,
    specimen,
)
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
    ``flagged``; ``value_text`` / ``ref_text`` are now persisted, so qualitative results and the
    printed reference survive. A narrative report carries its findings text instead of rows."""
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
                value_text=r.value_text,
                unit=r.unit,
                ref_low=r.ref_low,
                ref_high=r.ref_high,
                ref_text=r.ref_text,
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
    """Series keys of the out-of-range rows (for the flagged-only dynamics view)."""
    return {series_key(r.section, r.analyte) for r in results if r.flagged}


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
        key = series_key(r.section, r.analyte)
        points = series.get(key)
        # Need NUMERIC values on >=2 distinct dates — a qualitative analyte (urine bacteria, all
        # value=None) has nothing to chart, so it must not appear in the picker (an empty graph).
        if not points or len({p.taken_on for p in points if p.value is not None}) < 2:
            continue
        prev = items.get(key)
        items[key] = TrendChartItem(
            name=prev.name if prev else r.analyte,
            key=key,
            flagged=bool(r.flagged) or (prev.flagged if prev else False),
        )
    return sorted(items.values(), key=lambda it: (not it.flagged, it.name.casefold()))


def _trend_value(summary: TrendSummary) -> str:
    latest = summary.latest
    if latest is None or latest.value is None:
        return "—"
    return f"{latest.value:g} {summary.unit}".strip() if summary.unit else f"{latest.value:g}"


def _strip_section_prefix(analyte: str, section: str | None) -> str:
    """Drop a redundant leading '<section>: ' from an analyte name (urine-microscopy rows store the
    panel inside the name) so a compact list isn't 'Мікроскопія осаду сечі: Кристали …' on every
    line. The panel is shown once as a group header instead."""
    if section and analyte.casefold().startswith(f"{section.casefold()}: "):
        return analyte[len(section) + 2 :].strip()
    return analyte


def report_flagged_map(report: LabReport | None) -> dict[str, str]:
    """{series_key: display name} of a report's out-of-range (⚠️) indicators, deduped by key — so
    the picker can show a count and tell which flagged indicators have a dynamics button vs not.
    Deterministic, no LLM."""
    out: dict[str, str] = {}
    if report is None:
        return out
    for r in ordered_results(report):
        if r.flagged:
            out.setdefault(
                series_key(r.section, r.analyte), _strip_section_prefix(r.analyte, r.section)
            )
    return out


def _period_suffix(summary: TrendSummary) -> str:
    """' за 2021–2026' (or ' за 2026') — the span the measurements cover, so the count never looks
    inconsistent with the few dates the x-axis labels for readability. Empty if no dates."""
    if summary.first_date is None or summary.last_date is None:
        return ""
    start, end = summary.first_date.year, summary.last_date.year
    span = str(start) if start == end else f"{start}–{end}"
    return locale.CHART_PERIOD_SUFFIX.format(span=span)


def chart_dynamics_caption(summary: TrendSummary) -> str:
    """The deterministic chart caption: latest value + range-relative movement + count + the period
    it spans. NO analyte name (it is already the chart's title) — the caller may append a note."""
    movement = locale.TREND_PHRASES.get(summary.direction.name, "")
    return locale.CHART_DYNAMICS_LINE.format(
        value=_trend_value(summary),
        movement=movement,
        n=summary.n_points,
        period=_period_suffix(summary),
    )


async def render_dynamics_report(
    session: AsyncSession, *, user_id: int, report_id: int
) -> str | None:
    """ONE scannable text report of a report's trending analytes (problems first), each with its
    latest value + range-relative movement — replaces dumping one chart image per analyte (a flood
    at 85 indicators). Deterministic, no LLM. ``None`` when the report has no chartable trend."""
    report = await get_report(session, report_id=report_id, user_id=user_id)
    if report is None:
        return None
    series = build_series(await load_series_points(session, user_id))
    flagged_rows: list[tuple[str, str, str]] = []
    ok_rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for r in ordered_results(report):
        key = series_key(r.section, r.analyte)
        if key in seen:
            continue
        pts = series.get(key)
        if not pts or len({p.taken_on for p in pts if p.value is not None}) < 2:
            continue
        seen.add(key)
        summary = compute_trend(pts)
        movement = locale.TREND_PHRASES.get(summary.direction.name, "")
        name = _strip_section_prefix(summary.analyte, r.section)
        row = (name, _trend_value(summary), movement)
        (flagged_rows if (r.flagged or pts[-1].flagged) else ok_rows).append(row)
    if not flagged_rows and not ok_rows:
        return None
    total = len(flagged_rows) + len(ok_rows)
    lines = [_bold(locale.CHART_REPORT_HEADER.format(n=total))]

    def _block(header: str, rows: list[tuple[str, str, str]]) -> None:
        if not rows:
            return
        lines.extend(["", _bold(header)])
        lines.extend(
            locale.CHART_REPORT_ROW.format(analyte=a, value=v, movement=m) for a, v, m in rows
        )

    _block(locale.CHART_REPORT_FLAGGED_HEADER, flagged_rows)
    _block(locale.CHART_REPORT_OK_HEADER, ok_rows)
    lines.extend(["", locale.CHART_REPORT_HINT])
    return assert_safe_output(_ps("\n".join(lines)))


@dataclass(frozen=True)
class TrendChartData:
    """Deterministic per-analyte chart data for the PDF export: the series, its title, the clinical
    category (panel), the dynamics line, the specimen (so the note is sample-specific), and whether
    it is out of range."""

    title: str
    category: str  # display label, e.g. "🔬 Сеча"
    points: list[LabPoint]
    dynamics: str
    specimen: str | None
    flagged: bool


def _report_trend_charts_from(
    report: LabReport, series: dict[str, list[LabPoint]]
) -> list[TrendChartData]:
    """A report's trending analytes (flagged-first) from an ALREADY-loaded series map."""
    out: list[TrendChartData] = []
    seen: set[str] = set()
    for r in ordered_results(report):
        key = series_key(r.section, r.analyte)
        if key in seen:
            continue
        pts = series.get(key)
        if not pts or len({p.taken_on for p in pts if p.value is not None}) < 2:
            continue
        seen.add(key)
        summary = compute_trend(pts)
        cat_key = grouping.categorize(r.section, summary.analyte)
        out.append(
            TrendChartData(
                title=_strip_section_prefix(summary.analyte, r.section),
                category=locale.CATEGORY_NAMES.get(cat_key, cat_key),
                points=pts,
                dynamics=chart_dynamics_caption(summary),
                specimen=specimen(pts[-1].section, summary.analyte),
                flagged=bool(r.flagged) or pts[-1].flagged,
            )
        )
    out.sort(key=lambda d: (not d.flagged, d.title.casefold()))
    return out


async def report_trend_charts(
    session: AsyncSession, *, user_id: int, report_id: int
) -> list[TrendChartData]:
    """Every trending analyte of a report (flagged first), as chart data — for the one-PDF export.
    Deterministic, no LLM; the handler adds the educational note per analyte."""
    report = await get_report(session, report_id=report_id, user_id=user_id)
    if report is None:
        return []
    series = build_series(await load_series_points(session, user_id))
    return _report_trend_charts_from(report, series)


def _category_label(section: str | None, analyte: str) -> str:
    """The display name of an analyte's clinical category ('🔬 Сеча'), category key as fallback."""
    key = grouping.categorize(section, analyte)
    return locale.CATEGORY_NAMES.get(key, key)


def _numeric_dates(points: list[LabPoint]) -> set[date]:
    return {p.taken_on for p in points if p.value is not None}


def _qual_dates(points: list[LabPoint]) -> set[date]:
    return {p.taken_on for p in points if p.value is None and (p.value_text or "").strip()}


@dataclass(frozen=True)
class ReportBreakdown:
    """How a report's indicators split for the dynamics export — so the PDF cover can explain why
    only some indicators get a chart: total distinct indicators, those with a numeric trend (a real
    chart), those shown as a qualitative text timeline, and those measured only once so far. The
    ``categories`` are the (display-name, count) pairs of the *charted* numeric indicators."""

    total: int
    numeric: int
    qualitative: int
    single: int
    categories: list[tuple[str, int]]  # (category KEY, count) of the charted numeric indicators


def _report_breakdown_from(report: LabReport, series: dict[str, list[LabPoint]]) -> ReportBreakdown:
    """A report's chart/table/single split from an ALREADY-loaded series map (cover counts)."""
    seen: set[str] = set()
    numeric = qualitative = single = 0
    cat_counts: Counter[str] = Counter()
    for r in ordered_results(report):
        key = series_key(r.section, r.analyte)
        if key in seen:
            continue
        seen.add(key)
        pts = series.get(key) or []
        if len(_numeric_dates(pts)) >= 2:
            numeric += 1
            cat_counts[grouping.categorize(r.section, r.analyte)] += 1
        elif len(_qual_dates(pts)) >= 2:
            qualitative += 1
        else:
            single += 1
    ordered = [c for c in grouping.CATEGORY_ORDER if cat_counts.get(c)]
    ordered += [c for c in cat_counts if c not in ordered]
    return ReportBreakdown(
        total=len(seen),
        numeric=numeric,
        qualitative=qualitative,
        single=single,
        categories=[(c, cat_counts[c]) for c in ordered],
    )


async def report_indicator_breakdown(
    session: AsyncSession, *, user_id: int, report_id: int
) -> ReportBreakdown:
    """Count how a report's indicators split between numeric charts, qualitative timelines, and
    single measurements. Categories are returned as KEYS (the presentation layer maps them to a
    readable name). Deterministic, no LLM — feeds the honest 'N of M' cover line."""
    report = await get_report(session, report_id=report_id, user_id=user_id)
    if report is None:
        return ReportBreakdown(0, 0, 0, 0, [])
    series = build_series(await load_series_points(session, user_id))
    return _report_breakdown_from(report, series)


@dataclass(frozen=True)
class QualMeasurement:
    """One dated qualitative result in a timeline (e.g. 2026-06-23 → 'не виявлені')."""

    taken_on: date
    text: str
    flagged: bool


@dataclass(frozen=True)
class QualTrend:
    """A qualitative analyte's timeline across reports — an indicator with no numeric series but a
    recorded text result ('не виявлені', 'виявлено', 'негатив'). It can change over time, so it is
    shown as a TABLE timeline (never a numeric chart, which would be meaningless for it)."""

    title: str
    key: str  # series key, so the picker can re-fetch THIS timeline when tapped
    category: str
    specimen: str | None
    timeline: list[QualMeasurement]
    flagged: bool
    changed: bool  # the text result differs across dates (a real qualitative change)


def _report_qual_dynamics_from(
    report: LabReport, series: dict[str, list[LabPoint]]
) -> list[QualTrend]:
    """A report's qualitative timelines (>=2 dates, no numeric chart) from an ALREADY-loaded series
    map. Changed-first, then flagged, then alphabetical."""
    out: list[QualTrend] = []
    seen: set[str] = set()
    for r in ordered_results(report):
        key = series_key(r.section, r.analyte)
        if key in seen:
            continue
        pts = series.get(key)
        if not pts or len(_numeric_dates(pts)) >= 2:  # missing or already a numeric chart
            continue
        qpts = [p for p in pts if p.value is None and (p.value_text or "").strip()]
        if len({p.taken_on for p in qpts}) < 2:
            continue
        seen.add(key)
        by_date: dict[date, QualMeasurement] = {}
        for p in sorted(qpts, key=lambda x: x.taken_on):
            by_date[p.taken_on] = QualMeasurement(
                taken_on=p.taken_on, text=(p.value_text or "").strip(), flagged=bool(p.flagged)
            )
        timeline = list(by_date.values())
        distinct_text = {m.text.casefold() for m in timeline}
        out.append(
            QualTrend(
                title=_strip_section_prefix(r.analyte, r.section),
                key=key,
                category=_category_label(r.section, r.analyte),
                specimen=specimen(r.section, r.analyte),
                timeline=timeline,
                # Flag means "out of range in THIS report" (so the picker ⚠️ count matches the card /
                # banner) — NOT the latest measurement across all reports, which could differ. A
                # clearly negative result ('не виявлено') is never flagged, even if the lab's OCR'd
                # mark was inconsistently captured as out-of-range on an absence.
                flagged=bool(r.flagged) and not is_negative_qualitative(r.value_text),
                changed=len(distinct_text) > 1,
            )
        )
    out.sort(key=lambda q: (not q.changed, not q.flagged, q.title.casefold()))
    return out


async def report_qualitative_dynamics(
    session: AsyncSession, *, user_id: int, report_id: int
) -> list[QualTrend]:
    """The report's qualitative indicators that were recorded on >=2 distinct dates (so they have a
    timeline worth showing) but have no numeric chart — e.g. urine bacteria 'не виявлені' that could
    become 'виявлено'. Changed-first, then flagged, then alphabetical. Deterministic, no LLM."""
    report = await get_report(session, report_id=report_id, user_id=user_id)
    if report is None:
        return []
    series = build_series(await load_series_points(session, user_id))
    return _report_qual_dynamics_from(report, series)


async def report_dynamics_bundle(
    session: AsyncSession, *, user_id: int, report_id: int
) -> tuple[list[TrendChartData], list[QualTrend], ReportBreakdown]:
    """Charts + qualitative timelines + cover breakdown for ONE report's PDF, loading the report and
    its series ONCE (these were three separate loads on every export). Deterministic, no LLM."""
    report = await get_report(session, report_id=report_id, user_id=user_id)
    if report is None:
        return ([], [], ReportBreakdown(0, 0, 0, 0, []))
    series = build_series(await load_series_points(session, user_id))
    return (
        _report_trend_charts_from(report, series),
        _report_qual_dynamics_from(report, series),
        _report_breakdown_from(report, series),
    )


# --- Cross-lab "all indicators" dynamics (for the by-category PDF export) --------


def _category_rank(section: str | None, analyte: str) -> int:
    """Position of an analyte's category in the display order — for grouping the all-indicators PDF
    by category (Кров → Сеча → Біохімія → …)."""
    key = grouping.categorize(section, analyte)
    order = {c: i for i, c in enumerate(grouping.CATEGORY_ORDER)}
    return order.get(key, len(order))


def _trend_charts_from_series(
    series: dict[str, list[LabPoint]], category: str | None
) -> list[TrendChartData]:
    """Build the numeric-trend chart data from an ALREADY-loaded series map (so the per-category PDF
    can load the user's series once for charts + tables + breakdown). Grouped by category then
    flagged-first; a ``category`` keeps only that category."""
    out: list[tuple[int, TrendChartData]] = []
    for pts in series.values():
        if len(_numeric_dates(pts)) < 2:
            continue
        latest = pts[-1]
        summary = compute_trend(pts)
        cat_key = grouping.categorize(latest.section, summary.analyte)
        if category is not None and cat_key != category:
            continue
        flagged = bool(latest.flagged) or summary.latest_flag in (ResultFlag.LOW, ResultFlag.HIGH)
        out.append(
            (
                _category_rank(latest.section, summary.analyte),
                TrendChartData(
                    title=_strip_section_prefix(summary.analyte, latest.section),
                    category=locale.CATEGORY_NAMES.get(cat_key, cat_key),
                    points=pts,
                    dynamics=chart_dynamics_caption(summary),
                    specimen=specimen(latest.section, summary.analyte),
                    flagged=flagged,
                ),
            )
        )
    out.sort(key=lambda t: (t[0], not t[1].flagged, t[1].title.casefold()))
    return [d for _, d in out]


def _qual_dynamics_from_series(
    series: dict[str, list[LabPoint]], category: str | None
) -> list[QualTrend]:
    """Build the qualitative-timeline table data from an ALREADY-loaded series map (the table half
    of the per-category PDF), grouped by category then changed/flagged-first."""
    out: list[tuple[int, QualTrend]] = []
    for key, pts in series.items():
        if len(_numeric_dates(pts)) >= 2:  # already a numeric chart
            continue
        qpts = [p for p in pts if p.value is None and (p.value_text or "").strip()]
        if len({p.taken_on for p in qpts}) < 2:
            continue
        latest = pts[-1]
        if category is not None and grouping.categorize(latest.section, latest.analyte) != category:
            continue
        by_date: dict[date, QualMeasurement] = {}
        for p in sorted(qpts, key=lambda x: x.taken_on):
            by_date[p.taken_on] = QualMeasurement(
                taken_on=p.taken_on, text=(p.value_text or "").strip(), flagged=bool(p.flagged)
            )
        timeline = list(by_date.values())
        distinct_text = {m.text.casefold() for m in timeline}
        out.append(
            (
                _category_rank(latest.section, latest.analyte),
                QualTrend(
                    title=_strip_section_prefix(latest.analyte, latest.section),
                    key=key,
                    category=_category_label(latest.section, latest.analyte),
                    specimen=specimen(latest.section, latest.analyte),
                    timeline=timeline,
                    flagged=timeline[-1].flagged,
                    changed=len(distinct_text) > 1,
                ),
            )
        )
    out.sort(key=lambda t: (t[0], not t[1].changed, not t[1].flagged, t[1].title.casefold()))
    return [q for _, q in out]


def _all_breakdown_from_series(
    series: dict[str, list[LabPoint]], category: str | None
) -> ReportBreakdown:
    """Cover counts (charts vs tables, by category) from an ALREADY-loaded series map."""
    numeric = qualitative = 0
    cat_counts: Counter[str] = Counter()
    for pts in series.values():
        cat_key = grouping.categorize(pts[-1].section, pts[-1].analyte)
        if category is not None and cat_key != category:
            continue
        if len(_numeric_dates(pts)) >= 2:
            numeric += 1
            cat_counts[cat_key] += 1
        elif len(_qual_dates(pts)) >= 2:
            qualitative += 1
            cat_counts[cat_key] += 1
    ordered = [c for c in grouping.CATEGORY_ORDER if cat_counts.get(c)]
    return ReportBreakdown(
        total=numeric + qualitative,
        numeric=numeric,
        qualitative=qualitative,
        single=0,
        categories=[(c, cat_counts[c]) for c in ordered],
    )


async def all_trend_charts(
    session: AsyncSession, *, user_id: int, category: str | None = None
) -> list[TrendChartData]:
    """EVERY numeric-trend analyte across ALL reports, grouped by category then flagged-first — the
    chart data behind the per-category 'PDF' export. When ``category`` is given, only that
    category's analytes are returned (the single-category PDF). Deterministic, no LLM."""
    series = build_series(await load_series_points(session, user_id))
    return _trend_charts_from_series(series, category)


async def all_qualitative_dynamics(
    session: AsyncSession, *, user_id: int, category: str | None = None
) -> list[QualTrend]:
    """EVERY qualitative-timeline analyte across ALL reports, grouped by category then
    changed/flagged-first — the table data for the PDF export. When ``category`` is given, only that
    category's analytes are returned (the single-category PDF). Deterministic, no LLM."""
    series = build_series(await load_series_points(session, user_id))
    return _qual_dynamics_from_series(series, category)


async def all_indicator_breakdown(
    session: AsyncSession, *, user_id: int, category: str | None = None
) -> ReportBreakdown:
    """Counts for the PDF cover: how many indicators are charts vs tables, by category. When
    ``category`` is given, the counts cover only that category (the single-category PDF)."""
    series = build_series(await load_series_points(session, user_id))
    return _all_breakdown_from_series(series, category)


async def all_dynamics_bundle(
    session: AsyncSession, *, user_id: int, category: str | None = None
) -> tuple[list[TrendChartData], list[QualTrend], ReportBreakdown]:
    """Charts + qualitative timelines + cover breakdown for the per-category PDF, loading the user's
    series ONCE (these were three separate full loads on every export). Deterministic, no LLM."""
    series = build_series(await load_series_points(session, user_id))
    return (
        _trend_charts_from_series(series, category),
        _qual_dynamics_from_series(series, category),
        _all_breakdown_from_series(series, category),
    )


@dataclass(frozen=True)
class PickItem:
    """One pickable indicator in the dynamics picker: a numeric one opens a CHART, a qualitative one
    opens a TABLE timeline. Both are images, so they browse in the same carousel."""

    name: str
    key: str
    flagged: bool
    qualitative: bool


async def list_report_pickables(
    session: AsyncSession, *, user_id: int, report_id: int
) -> list[PickItem]:
    """Everything in a report worth opening in dynamics: numeric trends (charts) AND qualitative
    timelines (tables). Flagged-first overall — so the ⚠️ ones (often qualitative, like a spermogram
    'Лейкоцити') are at the top and actually tappable, not just named. Deterministic, no LLM."""
    numeric = await list_report_trends(session, user_id=user_id, report_id=report_id)
    quals = await report_qualitative_dynamics(session, user_id=user_id, report_id=report_id)
    items = [
        PickItem(name=t.name, key=t.key, flagged=t.flagged, qualitative=False) for t in numeric
    ]
    items += [PickItem(name=q.title, key=q.key, flagged=q.flagged, qualitative=True) for q in quals]
    items.sort(key=lambda it: (not it.flagged, it.qualitative, it.name.casefold()))
    return items


async def qual_trend_by_key(
    session: AsyncSession, *, user_id: int, report_id: int, key: str
) -> QualTrend | None:
    """The single qualitative timeline for ``key`` in this report (re-fetched when its picker button
    is tapped), or None if it is no longer qualitative-with-a-timeline."""
    quals = await report_qualitative_dynamics(session, user_id=user_id, report_id=report_id)
    return next((q for q in quals if q.key == key), None)


def qual_dynamics_caption(qual: QualTrend) -> str:
    """The caption under a qualitative TABLE image: latest text result + changed/stable + count and
    the period it spans (parallels chart_dynamics_caption for numeric charts)."""
    latest = qual.timeline[-1].text if qual.timeline else "—"
    movement = locale.CHART_PDF_QUAL_CHANGED if qual.changed else locale.CHART_QUAL_STABLE
    dates = [m.taken_on for m in qual.timeline]
    period = ""
    if dates:
        start, end = dates[0].year, dates[-1].year
        period = locale.CHART_PERIOD_SUFFIX.format(
            span=str(start) if start == end else f"{start}–{end}"
        )
    return locale.CHART_QUAL_DYNAMICS_LINE.format(
        value=latest, movement=movement, n=len(qual.timeline), period=period
    )


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
        by_key[series_key(r.section, r.analyte)].append(r)
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


# Narrative study types can be long ("КТ … з внутрішньовенним контрастуванням"). A Telegram button
# wraps to ~two lines, so we allow more than one line's worth before cutting (on a word boundary) —
# most study names now show in full instead of an early "…"; only a truly long one is trimmed, with
# the complete type always on the opened card.
_LABEL_TYPE_MAX = 44


def short_type(report_type: str | None) -> str:
    """A narrative study type, shortened to fit a one-line list button — cut on a WORD boundary
    (never mid-word) with an ellipsis. The full type is shown on the opened card."""
    text = (report_type or locale.LAB_DOC_GENERIC).strip()
    if len(text) <= _LABEL_TYPE_MAX:
        return text
    head = text[:_LABEL_TYPE_MAX].rstrip()
    space = head.rfind(" ")
    if space >= _LABEL_TYPE_MAX * 0.6:  # prefer a clean word break when one is reasonably late
        head = head[:space].rstrip()
    return head + "…"


def report_kind_label(results: list[LabResult]) -> str:
    """A short, concrete 'what kind of analysis' tag for the list button — Кров / Сеча / Кров+Сеча /
    Гормони / Онкомаркери / Інфекції / Спермограма / … — so a glance tells you what it is, not just
    date + lab. The DOMINANT category leads; a second is shown only when it is a real share of the
    rows (not a single stray), and never a third — the button must stay short."""
    counts = Counter(grouping.categorize(r.section, r.analyte) for r in results)
    if not counts:
        return ""
    order = {c: i for i, c in enumerate(grouping.CATEGORY_ORDER)}
    ranked = sorted(counts, key=lambda c: (-counts[c], order.get(c, len(order))))
    chosen = [ranked[0]]
    # Show a second category only when it is a real mini-panel (>=3 rows), not one stray row — so
    # "Сеча+Гормони" / "Сеча+Онкомаркери" surface, but an incidental row does not. Never a third.
    if len(ranked) > 1 and counts[ranked[1]] >= 3:
        chosen.append(ranked[1])
    return "+".join(locale.CATEGORY_SHORT.get(c, c) for c in chosen)


def _short_lab(lab: str | None) -> str:
    """The lab brand WITHOUT the city ('Сінево, Львів' -> 'Сінево') — so the list button fits on a
    phone. The city is the same for every report and adds no information at a glance."""
    full = normalize_lab(lab) or locale.LAB_LAB_UNKNOWN
    return full.split(",")[0].strip() or full


def report_button_label(report: LabReport, results: list[LabResult]) -> str:
    """The one-line button label for a report in the master list."""
    date_txt = report.report_date.isoformat() if report.report_date else locale.HIST_NO_DATE
    lab_txt = _short_lab(report.lab)
    if report.kind == ReportKind.NARRATIVE:
        rtype = short_type(report.report_type)  # truncate the long study name on the button
        if report.lab:  # an imaging study often has no lab brand — then its type IS its identity
            return locale.HIST_BTN_REPORT_DOC.format(date=date_txt, lab=lab_txt, report_type=rtype)
        return locale.HIST_BTN_REPORT_DOC_NOLAB.format(date=date_txt, report_type=rtype)
    n_flagged = flagged_count(results)
    flags = locale.HIST_FLAGS_SUFFIX.format(n=n_flagged) if n_flagged else ""
    kind = report_kind_label(results)
    kind_part = f"{kind} · " if kind else ""
    return locale.HIST_BTN_REPORT.format(
        date=date_txt, kind=kind_part, lab=lab_txt, count=len(results), flags=flags
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
    """Append the bare disclaimer. The send layer (``render_interpretation_html``) turns the
    trailing disclaimer into the consistent italic *P.S.* block, so every health view ends alike."""
    return f"{text}\n\n{DISCLAIMER}"


def _bold(text: str) -> str:
    """Wrap a fixed structural label in the *bold* marker the send layer converts to ``<b>``.
    Applied only to fixed labels — never to lab free-text — so a forbidden phrase can't hide
    behind a marker from the guard's view."""
    return f"*{text}*"


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _findings_lines(narrative: str) -> list[str]:
    """Break a wall of narrative findings into one sentence per line, so an МРТ/КТ reads as a
    scannable list instead of one dense paragraph."""
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(narrative.strip()) if p.strip()]
    return parts or [narrative.strip()]


def _results_title(report: LabReport) -> str:
    """The bold one-line header for a results view. A narrative/imaging document leads with its
    TYPE and omits an unknown lab (the bare 'невідома' there was just noise)."""
    date_txt = report.report_date.isoformat() if report.report_date else locale.HIST_NO_DATE
    lab = normalize_lab(report.lab)
    if report.kind == ReportKind.NARRATIVE:
        parts = [report.report_type or locale.LAB_DOC_GENERIC, date_txt]
        if lab:
            parts.append(lab)
        return _bold(locale.HIST_TITLE_DOC.format(parts=" · ".join(parts)))
    return _bold(locale.HIST_TITLE_LAB.format(date=date_txt, lab=lab or locale.LAB_LAB_UNKNOWN))


# Collapsing in-range rows into an aggregate helps only when there are many; a handful is shown
# by name (a single-analyte report should never hide its one result behind "Усі N в нормі").
_INLINE_NORMAL_MAX = 5


def _value_display(r: LabResult) -> str:
    """The printed value of a row: the number, else the qualitative word, else — for a row the
    lab flagged but whose value was not captured — an honest 'lab-marked' note. A ⚠️ must never
    be left with a bare '—' (the user can't see WHY it is flagged); legacy rows whose boxed
    qualitative result wasn't extracted fall back to this until re-extraction recovers the word."""
    if r.value is not None:
        return f"{r.value:g}{f' {r.unit}' if r.unit else ''}"
    if r.value_text:
        return f"{r.value_text}{f' {r.unit}' if r.unit else ''}"
    return locale.LAB_VALUE_MARKED if r.flagged else "—"


def _result_line(r: LabResult, marker: str = "") -> str:
    ref = _ref_text(r.ref_low, r.ref_high) if (r.ref_low or r.ref_high) else (r.ref_text or "—")
    return f"• {r.analyte} — {_value_display(r)} ({locale.LAB_NORM_LABEL} {ref}) {marker}".rstrip()


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
    lines = [_results_title(report)]
    if report.kind == ReportKind.NARRATIVE:
        if report.conclusion:
            lines += ["", f"{_bold(locale.LAB_CONCLUSION_LABEL)}: {report.conclusion}"]
        elif report.narrative:
            lines += [""] + _findings_lines(report.narrative)
        return assert_safe_output(_ps("\n".join(lines)))
    if report.conclusion:
        lines += ["", f"{_bold(locale.LAB_CONCLUSION_LABEL)}: {report.conclusion}"]
    flagged = [r for r in results if r.flagged]
    normal = [r for r in results if not r.flagged]
    collapse = len(normal) > _INLINE_NORMAL_MAX
    if flagged:
        lines += ["", _bold(locale.HIST_PROBLEMS_HEADER.format(n=len(flagged)))]
        lines += _grouped_result_lines(flagged, locale.FLAG_ATTENTION)
    if normal and collapse:
        lines += ["", locale.HIST_PROBLEMS_NORMAL_AGG.format(n=len(normal))]
    elif normal:
        lines += ["", _bold(locale.HIST_PROBLEMS_NORMAL_HEADER)]
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
    lines = [_results_title(report)]
    if report.kind == ReportKind.NARRATIVE:
        if report.narrative:
            lines += [""] + _findings_lines(report.narrative)
        if report.conclusion:
            lines += ["", f"{_bold(locale.LAB_CONCLUSION_LABEL)}: {report.conclusion}"]
    else:
        if report.conclusion:
            lines += ["", f"{_bold(locale.LAB_CONCLUSION_LABEL)}: {report.conclusion}"]
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
            ref = (
                _ref_text(r.ref_low, r.ref_high)
                if (r.ref_low or r.ref_high)
                else (r.ref_text or "—")
            )
            lines.append(
                f"{i}. {r.analyte} — {_value_display(r)} "
                f"({locale.LAB_NORM_LABEL} {ref}) {emoji}".rstrip()
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
    analyte: str = ""  # the canonical analyte name, so the caller can fetch its educational note
    specimen: str | None = None  # blood / urine / semen — so the note is sample-specific


async def trend_for_analyte(session: AsyncSession, *, user_id: int, analyte: str) -> TrendView:
    points = await load_series_points(session, user_id)
    series = build_series(points)
    pts = find_series(series, analyte)
    if not pts:
        return TrendView(found=False, text=f"{locale.TREND_NOT_FOUND}\n\n{DISCLAIMER}", chart=None)

    summary = compute_trend(pts)
    spec = specimen(pts[-1].section, summary.analyte)
    if summary.n_points >= 2:
        # Chart caption leads with the movement (the name is the chart title); the handler may add a
        # short educational note. No disclaimer on a data chart caption.
        chart = render_trend_chart(pts, title=summary.analyte)
        return TrendView(
            found=True,
            text=assert_safe_output(chart_dynamics_caption(summary)),
            chart=chart,
            analyte=summary.analyte,
            specimen=spec,
        )
    # Text-only (insufficient data): no chart title, so keep the name, and the disclaimer like every
    # other plain health reply.
    movement = locale.TREND_PHRASES.get(summary.direction.name, "")
    line = locale.TREND_LINE.format(
        analyte=summary.analyte, value=_trend_value(summary), movement=movement, n=summary.n_points
    )
    return TrendView(
        found=True,
        text=f"{assert_safe_output(line)}\n{locale.TREND_INSUFFICIENT}\n\n{DISCLAIMER}",
        chart=None,
        analyte=summary.analyte,
        specimen=spec,
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
    repeat-lab reminder. Consultation memory about the report is decoupled but kept (the
    conversation is still remembered). The nightly backup is the safety net."""
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
    # Decouple — but keep — any consultation memory about this report: the conversation we had is
    # still remembered, it just no longer points at a deleted report.
    await session.execute(
        update(ConsultMemory).where(ConsultMemory.report_id == report_id).values(report_id=None)
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
