"""Deterministic, grounded context for a contextual consultation ('Запитати Дбайло').

When the user asks about a specific subject (one indicator's trend, or a whole report's reading),
the consult LLM must answer FROM THE ACTUAL DATA — not invent it. This module assembles that data
from the DB into a compact, structured context the model is told to ground its answer in (it replies
in Ukrainian). Pure retrieval / formatting — NO LLM, NO escalation; it only reads the DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.companion import concerns, consult_memory, history, notecache
from dbaylo.labs.agerefs import age_on
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

_SEX_EN = {"m": "male", "f": "female"}

KIND_INDICATOR = "indicator"
KIND_REPORT = "report"
KIND_SECTION = "section"

# The four reading sections, in the SAME order as bot.formatting.SECTION_KEYS — so a section index
# from the analysis drill-down maps to its Ukrainian name here without importing the bot layer.
_SECTION_NAMES: tuple[str, ...] = (
    locale.INTERPRET_SECTION_OVERALL,
    locale.INTERPRET_SECTION_ATTENTION,
    locale.INTERPRET_SECTION_HELP,
    locale.INTERPRET_SECTION_DOCTOR,
)


def section_label(index: int) -> str:
    """The Ukrainian name of a reading section by its index, or '' when out of range."""
    return _SECTION_NAMES[index] if 0 <= index < len(_SECTION_NAMES) else ""


@dataclass(frozen=True)
class Subject:
    """The anchor of a consultation — small and JSON-serializable, so it lives in FSM state and
    survives a restart. The grounded context is re-derived from the DB each turn (never stored)."""

    kind: str
    report_id: int
    analyte_key: str = ""  # indicator: the series key (cross-report)
    analyte_name: str = ""  # indicator: display name
    section_idx: int = -1  # section: index into the four reading sections

    def to_dict(self) -> dict[str, str | int]:
        return {
            "kind": self.kind,
            "report_id": self.report_id,
            "key": self.analyte_key,
            "name": self.analyte_name,
            "sec": self.section_idx,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Subject:
        raw_id = data.get("report_id", 0)
        raw_sec = data.get("sec", -1)
        return cls(
            kind=str(data.get("kind", "")),
            report_id=raw_id if isinstance(raw_id, int) else 0,
            analyte_key=str(data.get("key", "")),
            analyte_name=str(data.get("name", "")),
            section_idx=raw_sec if isinstance(raw_sec, int) else -1,
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


async def _section_context(
    session: AsyncSession, user_id: int, report_id: int, section_idx: int
) -> tuple[str, str] | None:
    """A report's full grounded context, focused on ONE reading section (e.g. 'Що допоможе') — the
    same data, but the consultation is centred on that aspect. Label is the section's name."""
    built = await _report_context(session, user_id, report_id)
    if built is None:
        return None
    report_ctx, _report_label = built
    name = section_label(section_idx)
    if not name:
        return report_ctx, _report_label  # unknown section -> treat as a whole-report consult
    focus = (
        f"The user is reading the '{name}' part of this report's reading and wants to dig into "
        "THAT aspect specifically — centre the consultation there (still using all the data above)."
    )
    return f"{report_ctx}\n\n{focus}", name


async def patient_profile(session: AsyncSession, user_id: int, today: date) -> str:
    """A compact, grounded profile of THIS patient — so Дбайло acts like an assistant who knows the
    person: age/sex, the concerns they track, and their recent reports WITH DATES (so the model can
    judge how old a key exam is). Deterministic, read-only. Returns ``""`` when there is nothing to
    ground in (no age/sex, no tracked concerns, no reports) — the caller then answers generally.
    Shared by the consult and the general companion / symptom intake."""
    reports = await history.list_confirmed(session, user_id=user_id, limit=8)
    conditions = await concerns.list_active(session, user_id=user_id)
    age = sex = None
    for r in reports:  # newest first — take the first report that printed each
        if age is None and r.birth_date is not None:
            age = age_on(r.birth_date, today)
        if sex is None and r.sex:
            sex = r.sex
    if age is None and sex is None and not conditions and not reports:
        return ""  # nothing to personalise from -> a general (non-grounded) reply
    lines = [f"PATIENT PROFILE (personalise to THIS patient; today is {today.isoformat()}):"]
    who = []
    if age is not None:
        who.append(f"~{age} years old")
    if sex:
        who.append(_SEX_EN.get(sex, sex))
    if who:
        lines.append(f"- {', '.join(who)}.")
    names = "; ".join(c.name for c in conditions if c.name)
    if names:
        lines.append(f"- Health concerns the user is currently tracking: {names}.")
    if reports:
        lines.append("- Recent reports (most recent first):")
        for r in reports[:8]:
            d = r.report_date.isoformat() if r.report_date else "?"
            what = r.report_type or normalize_lab(r.lab) or "аналіз"
            n_flag = sum(1 for x in r.results if x.flagged)
            flag = f" — {n_flag} поза нормою" if n_flag else ""
            lines.append(f"  · {d}: {what}{flag}")
    lines.append(
        "Use these dates to judge how recent each exam is, and tailor your questions and advice to "
        "this person."
    )
    return "\n".join(lines)


async def build_context(
    session: AsyncSession,
    user_id: int,
    subject: Subject,
    *,
    today: date,
    recall_exclude: frozenset[str] = frozenset(),
) -> tuple[str, str] | None:
    """Build the (grounded English context, Ukrainian subject label) for a subject, or ``None`` when
    it no longer resolves (the report was deleted). The patient profile + a cross-session MEMORY of
    prior consultations are prepended, so the consult always knows the person's broader state and
    what was discussed before. ``recall_exclude`` drops memory turns already in the live FSM
    transcript (no duplication mid-conversation). Re-derived from the DB on every turn."""
    if subject.kind == KIND_INDICATOR:
        built = await _indicator_context(session, user_id, subject)
    elif subject.kind == KIND_REPORT:
        built = await _report_context(session, user_id, subject.report_id)
    elif subject.kind == KIND_SECTION:
        built = await _section_context(session, user_id, subject.report_id, subject.section_idx)
    else:
        return None
    if built is None:
        return None
    context, label = built
    profile = await patient_profile(session, user_id, today)
    memory = await consult_memory.recall_block(session, user_id=user_id, exclude=recall_exclude)
    parts = [part for part in (profile, memory, context) if part]
    return "\n\n".join(parts), label
