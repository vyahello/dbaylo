"""Humanization layer — turns already-computed trends into a Ukrainian summary.

Strictly separate from the deterministic engine: it never computes a trend, it
only describes the numbers it is handed. Every produced string passes through
``assert_safe_output`` (Ukrainian guards: no dose directive, no "skip the
doctor"); if the model output is unsafe or the call fails, it falls back to a
deterministic Ukrainian template built from the same computed summaries. The
disclaimer is always appended.
"""

from __future__ import annotations

from dbaylo import locale
from dbaylo.config import get_settings
from dbaylo.labs.extraction import Runner
from dbaylo.labs.schema import ExtractedReport
from dbaylo.labs.trends import TrendSummary, is_out_of_range
from dbaylo.llm import ClaudeUnavailable, run_claude
from dbaylo.triage.safety import DISCLAIMER, assert_safe_output

# Internal (English) persona telling the model how to write the Ukrainian summary.
HUMANIZE_PERSONA = (
    "You are Дбайло, a warm, honest health companion — a caring friend, not a doctor. "
    "Reply EXCLUSIVELY in natural, correct Ukrainian. You are given already-computed lab "
    "trends; describe ONLY those numbers and the movement noted. Do NOT diagnose, do NOT "
    "interpret beyond what is given, and NEVER give a dose, a drug, or a treatment. "
    "Speak of changes relative to the reference range (e.g. 'наближається до норми'), never "
    "as 'покращується/погіршується'. Suggest discussing anything notable with a doctor. "
    "Be brief: 2–4 short sentences. No markdown."
)


def _movement_phrase(summary: TrendSummary) -> str:
    return locale.TREND_PHRASES.get(summary.direction.name, "")


def _format_value(summary: TrendSummary) -> str:
    if summary.latest is None or summary.latest.value is None:
        return "—"
    text = f"{summary.latest.value:g}"
    return f"{text} {summary.unit}".strip() if summary.unit else text


def _model_table(summaries: list[TrendSummary]) -> str:
    """Compact, neutral description of the computed trends fed to the model."""
    lines = [
        "Computed lab trends (describe these to the user; add nothing else):",
    ]
    for s in summaries:
        date_txt = s.last_date.isoformat() if s.last_date else "?"
        lines.append(
            f"- {s.analyte}: {_format_value(s)} ({date_txt}); "
            f"рух: {_movement_phrase(s)}; вимірів: {s.n_points}"
        )
    return "\n".join(lines)


def deterministic_summary(summaries: list[TrendSummary]) -> str:
    """A safe-by-construction Ukrainian summary, used as the fallback."""
    lines = [locale.LAB_SUMMARY_HEADER]
    for s in summaries:
        emoji = locale.FLAG_EMOJI.get(s.latest_flag.value, "")
        lines.append(f"• {s.analyte}: {_format_value(s)} {emoji} — {_movement_phrase(s)}.")
    lines.append("")
    lines.append(locale.LAB_SUMMARY_ASK_DOCTOR)
    return "\n".join(lines).strip()


def _finalize(body: str) -> str:
    """Attach the disclaimer; the body is assumed already safety-checked."""
    return f"{body}\n\n{DISCLAIMER}"


# --- Stage 5: expert interpretation + recommendations ---------------------------

# A fuller persona than HUMANIZE_PERSONA: it gives a real per-analyte reading and
# practical guidance, but stays inside the rails. The phrasing rules keep it past the
# deterministic guard (no forbidden reassurance / dose / restrictive-diet numbers), so
# legitimate output is rarely bounced to the fallback.
INTERPRET_PERSONA = (
    "You are Дбайло, a careful, honest health companion. You are NOT a doctor, but you DO "
    "give an expert-level reading of the user's OWN medical report and practical guidance. "
    "You may receive EITHER a table of lab results with the lab's own marks, OR a NARRATIVE "
    "document (МРТ/КТ/УЗД/висновок/виписка) with findings text and a conclusion — read "
    "whichever you are given and interpret it the same careful way.\n"
    "Reply EXCLUSIVELY in natural, correct Ukrainian, in FOUR sections in this order. Start each "
    "section with its header on its OWN line, copied EXACTLY as written here, with nothing else on "
    "that line — no numbering, no colon, no extra words:\n"
    f"  · '{locale.INTERPRET_SECTION_OVERALL}': two or three lines in DATA terms — the big picture "
    "and, plainly, whether it looks broadly reassuring or warrants attention. If the report mixes "
    "panels (e.g. blood vs urine — they are grouped in the input), note each briefly. If nothing "
    "is marked ATTENTION, say the results are within range; reflect any printed conclusion.\n"
    f"  · '{locale.INTERPRET_SECTION_ATTENTION}': ONLY the rows marked ATTENTION (keep panels "
    "apart — a name in two panels, e.g. Глюкоза/Лейкоцити, is two different things). For each: "
    "what it MAY indicate ('може свідчити про…', cautious, never a definite diagnosis); HOW "
    "concerning it is (likely minor / worth watching / worth prompt attention); and what it can "
    "lead to if left unaddressed. Group related flags (e.g. білірубін + АЛТ → печінка; "
    "холестерин + ЛПНЩ → ліпіди) so the user sees the picture, not 14 isolated facts.\n"
    f"  · '{locale.INTERPRET_SECTION_HELP}': concrete, practical lifestyle & nutrition guidance "
    "tailored to the flagged items — name SPECIFIC foods to favour and to limit, plus sleep, "
    "movement, hydration, alcohol, stress. QUALITATIVE only: NO calorie/macro/fasting numbers and "
    "NO medication/supplement dose.\n"
    f"  · '{locale.INTERPRET_SECTION_DOCTOR}': whether and how soon to see a doctor (and the "
    "specialty if obvious — e.g. гастроентеролог/терапевт), and what to ask or recheck.\n"
    "Use '• ' at the start of each bullet line. "
    "Be concrete and genuinely useful, but careful. NEVER: a definitive diagnosis; a medication, "
    "supplement, or any dose; calorie/macro/fasting numbers; fabricated studies, sources, or "
    "statistics. NEVER tell the user not to worry or that they can skip a doctor, and do not use "
    "the phrases 'все добре', 'усе добре', 'ти здоровий', 'ти здорова', 'не хвилюйся', "
    "'нічого страшного' — describe the data instead. Do NOT add your own disclaimer or any "
    "'я не лікар' / 'це не медичний висновок' line — that is appended automatically. PLAIN TEXT "
    "ONLY — no markdown at all (no **, *, _, #, ---, backticks, < or > tags); the headers are "
    "styled for you. Telegram shows your text verbatim."
)


def _interpret_table(report: ExtractedReport, summaries: list[TrendSummary]) -> str:
    """The structured, neutral input handed to the model (values + lab flags + trends)."""
    if report.is_narrative:
        lines = [f"Document type: {report.report_type or 'медичний документ'}"]
        if report.narrative:
            lines.append(f"Findings (as printed):\n{report.narrative}")
        if report.conclusion:
            lines.append(f"Conclusion (as printed): {report.conclusion}")
        return "\n".join(lines)
    lines = []
    if report.conclusion:
        lines.append(f"Lab's overall conclusion: {report.conclusion}")
    lines.append("Results, grouped by panel (analyte | value | reference | lab mark):")
    prev_section: object = object()
    for a in report.results:
        if a.section != prev_section:
            prev_section = a.section
            # Header so the model keeps panels apart (e.g. blood vs urine Глюкоза/Лейкоцити).
            lines.append(f"# Panel: {a.section or 'без секції'}")
        mark = (
            "ATTENTION" if is_out_of_range(a.value, a.ref_low, a.ref_high, a.out_of_range) else "ok"
        )
        lines.append(f"- {a.analyte} | {a.display_value()} | {a.display_reference()} | {mark}")
    if summaries:
        lines.append("")
        lines.append(
            "Trends vs the user's own history (describe relative to range, not 'better/worse'):"
        )
        for s in summaries:
            lines.append(
                f"- {s.analyte}: {_format_value(s)}; рух: {_movement_phrase(s)}; "
                f"вимірів: {s.n_points}"
            )
    return "\n".join(lines)


def deterministic_interpretation(report: ExtractedReport) -> str:
    """Safe-by-construction fallback: the lab conclusion + the flagged rows + see a doctor."""
    lines: list[str] = []
    if report.is_narrative:
        if report.report_type:
            lines.append(f"📄 {report.report_type}")
        if report.conclusion:
            lines.append(f"{locale.LAB_CONCLUSION_LABEL}: {report.conclusion}")
        elif report.narrative:
            lines.append(report.narrative)
        lines += ["", locale.LAB_INTERPRET_ASK_DOCTOR]
        return "\n".join(lines).strip()
    if report.conclusion:
        lines.append(f"{locale.LAB_CONCLUSION_LABEL}: {report.conclusion}")
    flagged = report.flagged_results()
    if not flagged:
        lines.append(locale.LAB_INTERPRET_ALL_NORMAL)
    else:
        lines.append(locale.LAB_INTERPRET_FLAGGED_HEADER)
        for a in flagged:
            lines.append(
                locale.LAB_INTERPRET_FLAGGED_ITEM.format(analyte=a.analyte, value=a.display_value())
            )
    lines += ["", locale.LAB_INTERPRET_ASK_DOCTOR]
    return "\n".join(lines).strip()


async def interpret(
    report: ExtractedReport,
    summaries: list[TrendSummary],
    *,
    runner: Runner = run_claude,
    model: str | None = None,
) -> str:
    """An expert-level Ukrainian reading of a confirmed report, guaranteed safe.

    The model interprets the values (using the lab's own flags) + the deterministic trends
    and gives practical guidance; every output passes ``assert_safe_output`` and gets the
    disclaimer, with a deterministic fallback when the LLM is unavailable or trips the guard.
    """
    fallback = assert_safe_output(deterministic_interpretation(report))
    try:
        result = await runner(
            _interpret_table(report, summaries),
            append_system_prompt=INTERPRET_PERSONA,
            model=model,
            # A full reading of a big panel takes far longer than a chat turn; without its own
            # timeout the call hits the 180s default and silently degrades to the bare list.
            timeout_s=get_settings().claude_interpret_timeout_s,
        )
    except ClaudeUnavailable:
        return _finalize(fallback)
    if not result.ok or not result.text.strip():
        return _finalize(fallback)
    try:
        safe_body = assert_safe_output(result.text.strip())
    except ValueError:
        return _finalize(fallback)
    return _finalize(safe_body)


async def humanize(
    summaries: list[TrendSummary],
    *,
    runner: Runner = run_claude,
    model: str | None = None,
) -> str:
    """Return a Ukrainian summary of the computed trends, guaranteed safe.

    Tries the model first; on any failure or unsafe output, falls back to the
    deterministic template. Always ends with the disclaimer.
    """
    fallback = assert_safe_output(deterministic_summary(summaries))

    if not summaries:
        return _finalize(fallback)

    try:
        result = await runner(
            _model_table(summaries),
            append_system_prompt=HUMANIZE_PERSONA,
            model=model,
        )
    except ClaudeUnavailable:
        return _finalize(fallback)

    if not result.ok or not result.text.strip():
        return _finalize(fallback)

    try:
        safe_body = assert_safe_output(result.text.strip())
    except ValueError:
        # Model said something that trips the guard — never send it.
        return _finalize(fallback)

    return _finalize(safe_body)
