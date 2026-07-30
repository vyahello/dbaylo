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
from dbaylo.companion import concerns, consult_memory, goals, health, history, notecache
from dbaylo.db.models import User
from dbaylo.labs.agerefs import age_on, describe_age
from dbaylo.labs.humanize import _interpret_table, note_cache_key, strip_markup
from dbaylo.labs.labnames import normalize_lab
from dbaylo.labs.pipeline import load_series_points
from dbaylo.labs.trends import (
    LabPoint,
    build_series,
    compute_trend,
    direction_phrase,
    is_out_of_range,
    specimen,
)

_SEX_EN = {"m": "male", "f": "female"}

KIND_INDICATOR = "indicator"
KIND_REPORT = "report"
KIND_SECTION = "section"
KIND_GENERAL = "general"  # a whole-picture consultation (entered from general chat's affordances)

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
    session: AsyncSession, user_id: int, subject: Subject, *, today: date
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
    lines.append(
        f"Range-relative trend across these points: {direction_phrase(summary.direction)}."
    )
    if summary.last_date is not None:
        ago = describe_age(summary.last_date, today=today)
        lines.append(
            f"Most recent measurement: {_value_str(pts[-1])} on "
            f"{summary.last_date.isoformat()} ({ago})."
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


async def _general_context(session: AsyncSession, user_id: int, today: date) -> tuple[str, str]:
    """A WHOLE-PICTURE consultation (entered from general chat's affordances) — grounded in the
    deterministic indicator picture (the patient profile + memory are added by ``build_context``).
    Never ``None``: with nothing currently off it is still a valid general talk about wellbeing."""
    findings = await health.findings_context(session, user_id, today=today)
    body = findings or "No indicator is currently out of range on record."
    context = f"Subject: the user's OVERALL health picture (a general consultation).\n{body}"
    return context, locale.CONSULT_GENERAL_LABEL


def _first_name(raw: str | None) -> str | None:
    """The given name to address the user by — the first whitespace token of the stored name
    (Telegram 'First Last' -> 'First'; a lab-adopted name is already just the given name),
    trimmed and capitalised. ``None`` when there is no usable name."""
    if not raw:
        return None
    token = raw.strip().split()[0] if raw.strip() else ""
    token = token.strip("«»\"'.,;:()")
    if not token or any(ch.isdigit() for ch in token):
        return None
    return token[:1].upper() + token[1:]


def _address_line(name: str) -> str:
    """The instruction (fed to EVERY voice via the profile) on HOW to address the user by name —
    warm and friend-like but still the professional expert; correct Ukrainian vocative, varied
    forms, used sparingly. The name value is per-user data, so it lives in the grounded context (not
    the static persona), which every voice already receives."""
    return (
        f"- The user's name is {name}. Address them by name naturally and warmly, in correct "
        "Ukrainian VOCATIVE case (e.g. Володимир→Володимире; the affectionate short Володю/Вова "
        "when the tone is light) — vary full and short friendly forms as a real friend would. But "
        "use it SPARINGLY (not every message, never twice in a message), and never let the warmth "
        "undercut your calm, professional, expert tone."
    )


# Imaging / narrative documents (МРТ · КТ · УЗД · висновок) carry findings that exist NOWHERE ELSE
# in the grounding: they have no analyte rows, so the trend engine, the indicator inventory and the
# findings block are all blind to them. Without their TEXT the companion could name only their dates
# and had to answer "I don't have that report in front of me" when asked to compare across exams
# (e.g. how many kidney stones in January vs July). Bounded so a long imaging history cannot crowd
# out the rest of the context.
_DOCS_MAX = 6  # most recent narrative documents carried in full
_DOC_CHARS = 1100  # per-document findings budget (a two-kidney ultrasound fits comfortably)


def _clip_body(text: str, limit: int = _DOC_CHARS) -> str:
    """Trim a findings body to ``limit`` chars on a WORD boundary, so a long study does not eat the
    whole context and a mid-word cut never looks like a measurement ('7 m…')."""
    body = " ".join(text.split())  # collapse the OCR's hard wraps into flowing text
    if len(body) <= limit:
        return body
    cut = body[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip() + "…"


async def _documents_block(session: AsyncSession, user_id: int, today: date) -> str:
    """The user's imaging / narrative documents as grounded lines — study type, date, the doctor's
    printed CONCLUSION and the findings text — so free conversation can quote and COMPARE them
    (stone counts, sizes, organ measurements) instead of only knowing that such an exam exists.
    ``""`` when the user has none. Deterministic; the text is the doctor's own, not our verdict."""
    docs = await history.list_narratives(session, user_id=user_id)
    if not docs:
        return ""
    lines = [
        "IMAGING / NARRATIVE DOCUMENTS (МРТ · КТ · УЗД · висновок) — the doctor's OWN printed "
        "text, "
        "verbatim. These carry details found NOWHERE else in this context (they have no analyte "
        "rows): stone counts and sizes, organ measurements, described findings. When the user asks "
        "about them — including COMPARING two exams ('how many stones in January vs July') — read "
        "and quote THIS text and answer from it; do NOT say you lack the report. Never invent a "
        "detail that is not written here, and if a document does not state something, say so."
    ]
    for report in docs[:_DOCS_MAX]:
        when = report.report_date.isoformat() if report.report_date else "date unknown"
        ago = f" ({describe_age(report.report_date, today=today)})" if report.report_date else ""
        what = report.report_type or locale.LAB_DOC_GENERIC
        lab = normalize_lab(report.lab)
        lines.append(f"- {when}{ago} — {what}{f' [{lab}]' if lab else ''}:")
        if report.conclusion:
            lines.append(f"    CONCLUSION: {_clip_body(report.conclusion)}")
        if report.narrative:
            lines.append(f"    FINDINGS: {_clip_body(report.narrative)}")
    if len(docs) > _DOCS_MAX:
        # Never let a silent truncation read as "that is everything".
        lines.append(
            f"({len(docs) - _DOCS_MAX} older document(s) exist beyond these — say so if relevant.)"
        )
    return "\n".join(lines)


async def patient_profile(session: AsyncSession, user_id: int, today: date) -> str:
    """A compact, grounded profile of THIS patient — so Дбайло acts like an assistant who knows the
    person: age/sex, the concerns they track, and their recent reports WITH DATES (so the model can
    judge how old a key exam is). Deterministic, read-only. Returns ``""`` when there is nothing to
    ground in (no age/sex, no tracked concerns, no reports) — the caller then answers generally.
    Shared by the consult and the general companion / symptom intake."""
    reports = await history.list_confirmed(session, user_id=user_id, limit=8)
    conditions = await concerns.list_active(session, user_id=user_id)
    goal_texts = await goals.active_goal_texts(session, user_id=user_id)
    user = await session.get(User, user_id)
    name = _first_name(user.name if user is not None else None)
    age = sex = None
    for r in reports:  # newest first — take the first report that printed each
        if age is None and r.birth_date is not None:
            age = age_on(r.birth_date, today)
        if sex is None and r.sex:
            sex = r.sex
    nothing = age is None and sex is None and not conditions and not goal_texts and not reports
    if name is None and nothing:
        return ""  # nothing to personalise from -> a general (non-grounded) reply
    lines = [f"PATIENT PROFILE (personalise to THIS patient; today is {today.isoformat()}):"]
    if name is not None:  # so every voice can address the user by name (first line, most prominent)
        lines.append(_address_line(name))
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
    if goal_texts:
        # The user's ACTIVE goals — Дбайло should support them: ask how they're going, encourage
        # progress, weave them in. Never prescribe restrictive numbers toward a goal (the rails).
        lines.append(
            "- Goals the user is actively working toward (support these — ask how it's going and "
            "cheer real progress, but never prescribe doses, calories, or crash targets): "
            + "; ".join(goal_texts)
            + "."
        )
    if reports:
        lines.append("- Recent reports (most recent first):")
        for r in reports[:8]:
            d = r.report_date.isoformat() if r.report_date else "?"
            ago = f" ({describe_age(r.report_date, today=today)})" if r.report_date else ""
            what = r.report_type or normalize_lab(r.lab) or "аналіз"
            n_flag = sum(1 for x in r.results if x.flagged)
            flag = f" — {n_flag} поза нормою" if n_flag else ""
            lines.append(f"  · {d}{ago}: {what}{flag}")
    lines.append(
        "Use these dates to judge how recent each exam is, and tailor your questions and advice to "
        "this person."
    )
    # The imaging/narrative documents' actual TEXT — without it the model knows a УЗД exists but not
    # what it found, and cannot compare two exams (the reported gap).
    documents = await _documents_block(session, user_id, today)
    if documents:
        lines.append("")
        lines.append(documents)
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
        built = await _indicator_context(session, user_id, subject, today=today)
    elif subject.kind == KIND_REPORT:
        built = await _report_context(session, user_id, subject.report_id)
    elif subject.kind == KIND_SECTION:
        built = await _section_context(session, user_id, subject.report_id, subject.section_idx)
    elif subject.kind == KIND_GENERAL:
        built = await _general_context(session, user_id, today)
    else:
        return None
    if built is None:
        return None
    context, label = built
    profile = await patient_profile(session, user_id, today)
    memory = await consult_memory.recall_block(session, user_id=user_id, exclude=recall_exclude)
    parts = [part for part in (profile, memory, context) if part]
    return "\n\n".join(parts), label
