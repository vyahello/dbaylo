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
from dbaylo.labs.extraction import Runner
from dbaylo.labs.trends import TrendSummary
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
