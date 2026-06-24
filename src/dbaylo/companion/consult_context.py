"""Deterministic, grounded context for a contextual consultation ('Запитати Дбайло').

When the user asks about a specific subject (one indicator's trend, or a whole report's reading),
the consult LLM must answer FROM THE ACTUAL DATA — not invent it. This module assembles that data
from the DB into a compact, structured context the model is told to ground its answer in (it replies
in Ukrainian). Pure retrieval / formatting — NO LLM, NO escalation; it only reads the DB.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import history, notecache
from dbaylo.labs.humanize import _interpret_table, note_cache_key, strip_markup
from dbaylo.labs.labnames import normalize_lab
from dbaylo.labs.pipeline import load_series_points
from dbaylo.labs.trends import (
    LabPoint,
    build_series,
    compute_trend,
    is_out_of_range,
    specimen,
)

KIND_INDICATOR = "indicator"
KIND_REPORT = "report"


@dataclass(frozen=True)
class Subject:
    """The anchor of a consultation — small and JSON-serializable, so it lives in FSM state and
    survives a restart. The grounded context is re-derived from the DB each turn (never stored)."""

    kind: str
    report_id: int
    analyte_key: str = ""  # indicator: the series key (cross-report)
    analyte_name: str = ""  # indicator: display name

    def to_dict(self) -> dict[str, str | int]:
        return {
            "kind": self.kind,
            "report_id": self.report_id,
            "key": self.analyte_key,
            "name": self.analyte_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Subject:
        raw_id = data.get("report_id", 0)
        report_id = raw_id if isinstance(raw_id, int) else 0
        return cls(
            kind=str(data.get("kind", "")),
            report_id=report_id,
            analyte_key=str(data.get("key", "")),
            analyte_name=str(data.get("name", "")),
        )


def _ref_text(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{low:g}–{high:g}"
    if high is not None:
        return f"≤ {high:g}"
    if low is not None:
        return f"≥ {low:g}"
    return "—"


def _status(point: LabPoint) -> str:
    if point.value is None:
        return point.value_text or "—"
    out = point.flagged or is_out_of_range(point.value, point.ref_low, point.ref_high, None)
    return "OUT OF RANGE" if out else "in range"


def _value_str(point: LabPoint) -> str:
    if point.value is None:
        return point.value_text or "—"
    return f"{point.value:g} {point.unit}".strip() if point.unit else f"{point.value:g}"


async def _indicator_context(
    session: AsyncSession, user_id: int, subject: Subject
) -> tuple[str, str] | None:
    series = build_series(await load_series_points(session, user_id))
    pts = series.get(subject.analyte_key)
    if not pts:
        return None
    summary = compute_trend(pts)
    name = subject.analyte_name or summary.analyte
    spec = specimen(pts[-1].section, summary.analyte)
    lines = [
        f"Subject: a single lab indicator — '{name}'" + (f" (sample: {spec})." if spec else ".")
    ]
    lines.append("Measurements over time (date | value | reference | status):")
    for p in pts:
        ref = _ref_text(p.ref_low, p.ref_high)
        lines.append(f"- {p.taken_on.isoformat()} | {_value_str(p)} | {ref} | {_status(p)}")
    lines.append(f"Range-relative trend across these points: {summary.direction.name}.")
    if summary.last_date is not None:
        lines.append(
            f"Most recent measurement: {_value_str(pts[-1])} on {summary.last_date.isoformat()}."
        )
    # General (value-independent) note about this marker, if we have one cached — extra grounding.
    note = (await notecache.fetch_cached(session, [note_cache_key(spec, name)])).get(
        note_cache_key(spec, name)
    )
    if note:
        lines.append(f"General reference about this marker: {note}")
    return "\n".join(lines), name


async def _report_context(
    session: AsyncSession, user_id: int, report_id: int
) -> tuple[str, str] | None:
    report = await history.get_report(session, report_id=report_id, user_id=user_id)
    if report is None:
        return None
    date_txt = report.report_date.isoformat() if report.report_date else "дата невідома"
    lab = normalize_lab(report.lab) or "лабораторія невідома"
    results = history.ordered_results(report)
    reconstructed = history.reconstruct_report(report, results)
    lines = [f"Subject: a whole lab report from {date_txt} ({lab})."]
    lines.append(
        _interpret_table(reconstructed, [])
    )  # grounded panel table (analyte|value|ref|mark)
    if report.summary and report.summary != history.SUMMARY_PENDING:
        lines.append("Дбайло's saved expert reading of this report (for continuity):")
        lines.append(strip_markup(report.summary))
    return "\n".join(lines), f"{date_txt} · {lab}"


async def build_context(
    session: AsyncSession, user_id: int, subject: Subject
) -> tuple[str, str] | None:
    """Build the (grounded English context, Ukrainian subject label) for a subject, or ``None`` when
    it no longer resolves (the report was deleted). Re-derived from the DB on every turn."""
    if subject.kind == KIND_INDICATOR:
        return await _indicator_context(session, user_id, subject)
    if subject.kind == KIND_REPORT:
        return await _report_context(session, user_id, subject.report_id)
    return None
