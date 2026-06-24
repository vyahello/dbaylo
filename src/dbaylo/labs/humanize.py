"""Humanization layer — turns already-computed trends into a Ukrainian summary.

Strictly separate from the deterministic engine: it never computes a trend, it
only describes the numbers it is handed. Every produced string passes through
``assert_safe_output`` (Ukrainian guards: no dose directive, no "skip the
doctor"); if the model output is unsafe or the call fails, it falls back to a
deterministic Ukrainian template built from the same computed summaries. The
disclaimer is always appended.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass

from dbaylo import locale
from dbaylo.config import get_settings
from dbaylo.labs.extraction import Runner
from dbaylo.labs.schema import ExtractedReport
from dbaylo.labs.trends import TrendSummary, is_out_of_range, normalize_analyte
from dbaylo.llm import NATURAL_VOICE, ClaudeUnavailable, run_claude
from dbaylo.triage.safety import DISCLAIMER, assert_safe_output

# Internal (English) persona telling the model how to write the Ukrainian summary.
HUMANIZE_PERSONA = (
    "You are Дбайло, a warm, honest health companion — a caring friend, not a doctor. "
    "Reply EXCLUSIVELY in natural, correct Ukrainian. You are given already-computed lab "
    "trends; describe ONLY those numbers and the movement noted. Do NOT diagnose, do NOT "
    "interpret beyond what is given, and NEVER give a dose, a drug, or a treatment. "
    "Speak of changes relative to the reference range (e.g. 'наближається до норми'), never "
    "as 'покращується/погіршується'. Suggest discussing anything notable with a doctor. "
    "Be brief: 2–4 short sentences. No markdown.\n" + NATURAL_VOICE
)


def strip_markup(text: str) -> str:
    """Drop the light *bold*/_italic_ markers, leaving clean prose. Used by the safety guard
    (so a forbidden phrase can't hide behind a marker) and by the plain ``/history`` rendering;
    the Telegram renderer (``bot.formatting``) is what turns the markers into real bold/italic."""
    return text.replace("*", "").replace("_", "")


# A model sometimes appends its OWN 'я не лікар…' line despite being told not to; we always append
# the canonical DISCLAIMER, so a model-added one is a visible duplicate. These stems catch the usual
# self-disclaimer phrasings (checked on marker-stripped text).
_SELF_DISCLAIMER_RE = re.compile(
    r"(я\s+не\s+лікар|не\s+замінює\s+(?:консультац|візит|огляд|фахів)"
    r"|це\s+не\s+(?:медичн|діагноз|заміна)|інформаційн\w*\s+(?:підтримк|характер|мет)"
    r"|не\s+призначаю\s+лікуванн|звернись\s+до\s+лікаря.*консультац)",
    re.IGNORECASE,
)


def strip_self_disclaimer(text: str) -> str:
    """Drop a TRAILING paragraph the model added that just restates the disclaimer ('я не лікар…',
    'це не медичний висновок', 'не замінює консультацію'). The canonical ``DISCLAIMER`` is appended
    by us, so a model-added one only duplicates it. Never strips everything — falls back to the
    original if the whole body matched."""
    paras = re.split(r"\n\s*\n", text.strip())
    while len(paras) > 1 and _SELF_DISCLAIMER_RE.search(strip_markup(paras[-1])):
        paras.pop()
    return "\n\n".join(paras).strip() or text.strip()


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


# --- Per-marker educational note (trend-chart caption) ---------------------------

# A tiny educational caption for ONE lab marker, shown under its trend chart. It is general
# knowledge about the marker — independent of the user's values — so it is generated once and cached
# in-process (regenerated after a restart). Guard-checked; "" on any failure so the caller falls
# back to the deterministic dynamics line alone.
INDICATOR_NOTE_PERSONA = (
    "You are an experienced physician and laboratory-medicine specialist writing a SHORT expert "
    "note about ONE Ukrainian lab marker, shown under its trend chart for an educated layperson. "
    "You are given the marker name AND its sample type (blood / urine / semen). Reply EXCLUSIVELY "
    "in natural Ukrainian, 2–3 short sentences (up to ~320 characters): (1) what the marker "
    "reflects clinically FOR THAT SAMPLE — the organ, system or physiological process behind it; "
    "(2) what an ELEVATED level can broadly point to, AND separately what a REDUCED level can — "
    "concrete clinical directions a specialist would name, never a vague 'будь-яке відхилення'. "
    "Write with the substance and precision of a specialist, but plainly, so a non-doctor "
    "understands. Answer for the given sample specifically — NEVER hedge 'сечі або крові, залежно "
    "від контексту'. Hard rules: speak in GENERAL terms, do NOT diagnose THE READER or judge their "
    "result, use NO numbers, NO doses, NO drug names, NO diets or fasting, NO fabricated "
    "statistics, and NEVER say the reader is healthy or sick. It is general education a doctor "
    "interprets in context. If you do not recognize the marker, reply with NOTHING. No markdown.\n"
    + NATURAL_VOICE
)

# Ukrainian sample-type context handed to the note generator so it never guesses urine vs blood.
_SPECIMEN_UK: dict[str, str] = {
    "blood": "кров",
    "urine": "сеча",
    "semen": "спермограма (еякулят)",
}

_indicator_note_cache: dict[str, str] = {}

# Bump when INDICATOR_NOTE_PERSONA changes — it becomes part of the persisted cache key, so old
# notes are ignored and regenerated with the new wording. v2: expert, directional (high vs low).
INDICATOR_NOTE_VERSION = "2"


def note_cache_key(specimen: str | None, analyte: str) -> str:
    """Stable key for an indicator note: a note depends ONLY on (persona version, specimen,
    normalized analyte) — never on measured values — so it is global and never stale by new data."""
    return f"{INDICATOR_NOTE_VERSION}\x1f{specimen or ''}\x1f{normalize_analyte(analyte)}"


async def describe_indicator(
    analyte: str,
    *,
    specimen: str | None = None,
    runner: Runner = run_claude,
    model: str | None = None,
) -> str:
    """A short, guard-checked educational note for a marker's trend-chart caption. The ``specimen``
    (blood / urine / semen, from the series key) is passed so the note is specific instead of
    hedging 'urine or blood'. Cached in-process per (specimen, normalized name); "" on any failure /
    guard-trip / unrecognized marker. Only a good note is cached, so a transient failure is retried
    rather than poisoning the cache."""
    key = f"{specimen or ''}\x1f{normalize_analyte(analyte)}"
    cached = _indicator_note_cache.get(key)
    if cached is not None:
        return cached
    prompt = f"Показник: {analyte}"
    sample = _SPECIMEN_UK.get(specimen or "")
    if sample:
        prompt += f"\nТип зразка: {sample} — описуй саме для цього зразка"
    try:
        result = await runner(
            prompt,
            append_system_prompt=INDICATOR_NOTE_PERSONA,
            model=model,
            timeout_s=get_settings().claude_interpret_timeout_s,
        )
    except ClaudeUnavailable:
        return ""
    if not result.ok or not result.text.strip():
        return ""
    body = strip_markup(result.text.strip())
    try:
        assert_safe_output(body)
    except ValueError:
        return ""
    _indicator_note_cache[key] = body
    return body


# --- Stage 5: expert interpretation + recommendations ---------------------------
# The reading is generated section-by-section (see the per-section personas below), so a single
# whole-document persona is no longer used — the four focused calls are more reliable and richer.


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
            "ATTENTION"
            if is_out_of_range(a.value, a.ref_low, a.ref_high, a.out_of_range, a.value_text)
            else "ok"
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


# --- Stage 5 (parallel): one focused call per section, run concurrently --------------
# A full reading of an 85-row panel in a single call generates thousands of tokens serially
# (~5–6 min). Splitting it into the four sections — each a smaller, focused call — and running
# them concurrently roughly halves the wait, and a hiccup in one section no longer costs the
# whole reading (that section falls back to a deterministic line; the rest stay LLM).

# Shared base persona; each section appends its own task. It produces ONLY that section's body
# (no header — we add the canonical one) so the four assemble into the same shape as before.
_SECTION_BASE_PERSONA = (
    "You are Дбайло, a careful, honest health companion — NOT a doctor, but you give an "
    "expert-level reading of the user's OWN lab report. You are given a table of results with the "
    "lab's own ATTENTION marks, grouped by panel. Reply EXCLUSIVELY in natural, correct Ukrainian, "
    "and output ONLY the body for the ONE section described at the end — no header line, no other "
    "section, no preamble, no sign-off. Write in warm, PLAIN language an ordinary person "
    "understands: short sentences; explain any term in parentheses; use '• ' for bullet lines. "
    "FORMATTING (light, a few per section): wrap a key term in single *asterisks* for bold (an "
    "analyte with its value, e.g. *АЛТ 63 Од/л*; a small sub-heading; the verdict) and a gentle "
    "caveat in _underscores_ for italic. No other markup (no **double**, #, ---, backticks, < >). "
    "Keep panels apart — a name in two panels (Глюкоза/Лейкоцити) is two different things. "
    "NEVER: a definitive diagnosis; a medication, supplement, or any dose; calorie/macro/fasting "
    "numbers; fabricated studies/sources/statistics. NEVER tell the user not to worry or that they "
    "can skip a doctor; do not use 'все добре', 'усе добре', 'ти здоровий', 'ти здорова', "
    "'не хвилюйся', 'нічого страшного'. Do NOT add a disclaimer or 'я не лікар' line.\n"
    "FOCUS: discuss ONLY the analytes marked ATTENTION (out of range). Do NOT describe or list "
    "in-range analytes one by one — refer to normal results only as a brief aggregate (e.g. "
    "'решта показників — у межах норми'), and only where it helps the reader.\n"
    + NATURAL_VOICE
    + "\nTHE SECTION TO WRITE — "
)


@dataclass(frozen=True)
class _Section:
    header: str
    instruction: str
    fallback: Callable[[ExtractedReport], str]


def _overall_fallback(report: ExtractedReport) -> str:
    if report.conclusion:
        return report.conclusion
    return (
        locale.LAB_INTERPRET_OVERALL_ATTENTION
        if report.flagged_results()
        else locale.LAB_INTERPRET_ALL_NORMAL
    )


def _attention_fallback(report: ExtractedReport) -> str:
    flagged = report.flagged_results()
    if not flagged:
        return locale.LAB_INTERPRET_ALL_NORMAL
    return "\n".join(
        locale.LAB_INTERPRET_FLAGGED_ITEM.format(analyte=a.analyte, value=a.display_value())
        for a in flagged
    )


_SECTION_SPECS: tuple[_Section, ...] = (
    _Section(
        locale.INTERPRET_SECTION_OVERALL,
        "the big picture in two or three lines, focused on what WARRANTS ATTENTION — which "
        "systems/panels the flagged values point to, and plainly how serious it looks. Mention "
        "normal results ONLY as a brief aggregate ('решта показників — у межах норми'), never "
        "analyte by analyte. If NOTHING is marked ATTENTION, say briefly the results are within "
        "range and reflect any printed conclusion.",
        _overall_fallback,
    ),
    _Section(
        locale.INTERPRET_SECTION_ATTENTION,
        "ONLY the rows marked ATTENTION, grouped under a short *bold sub-heading* per system (e.g. "
        "*Печінка та жовч*: білірубін + АЛТ; *Ліпіди*: холестерин + ЛПНЩ). For each: what it MAY "
        "indicate ('може свідчити про…', cautious, never a definite diagnosis); HOW concerning it "
        "is (minor / worth watching / worth prompt attention); and what it can lead to if left "
        "unaddressed. If NO row is marked ATTENTION, write one short line that the values are "
        "within range.",
        _attention_fallback,
    ),
    _Section(
        locale.INTERPRET_SECTION_HELP,
        "practical lifestyle & nutrition guidance for the flagged items, under the same *bold "
        "sub-headings* — name SPECIFIC foods to favour and to limit, plus sleep, movement, "
        "hydration, alcohol, stress. QUALITATIVE only: NO calorie/macro/fasting numbers and NO "
        "medication/supplement dose. If nothing is flagged, give a brief general healthy-living "
        "orientation.",
        lambda _report: locale.LAB_INTERPRET_HELP_GENERIC,
    ),
    _Section(
        locale.INTERPRET_SECTION_DOCTOR,
        "whether and how soon to see a doctor (and the specialty if obvious — e.g. "
        "гастроентеролог/терапевт), and what to ask or recheck. If nothing is flagged, a brief "
        "routine note.",
        lambda _report: locale.LAB_INTERPRET_ASK_DOCTOR,
    ),
)


# --- Narrative / imaging documents (МРТ / КТ / УЗД / висновок): the SAME premium four-section
# reading, but each section is grounded in the document's printed findings + conclusion (no analyte
# table). Kept separate from the tabular specs so neither path's wording dilutes the other.
_NARRATIVE_SECTION_BASE_PERSONA = (
    "You are Дбайло, a careful, honest health companion — NOT a doctor, but you give an "
    "expert-level reading of the user's OWN imaging / medical document (МРТ/КТ/УЗД/висновок/"
    "виписка). You are given the document type, its FINDINGS as printed, and the lab's printed "
    "CONCLUSION. Reply EXCLUSIVELY in natural, correct Ukrainian, and output ONLY the body for the "
    "ONE section described at the end — no header line, no other section, no sign-off.\n"
    "Write in warm, PLAIN language an ordinary person understands: short sentences; explain any "
    "medical term in parentheses; use '• ' for bullet lines. FORMATTING (light, a few per "
    "section): wrap a key finding in single *asterisks* for bold (e.g. *камінь 8 мм у лівій "
    "нирці*; a small sub-heading; the verdict) and a gentle caveat in _underscores_ for italic. No "
    "other markup (no **double**, #, ---, backticks, < >). NEVER: a definitive diagnosis; a "
    "medication, supplement, or any dose; calorie/macro/fasting numbers; fabricated studies/"
    "sources/statistics. NEVER tell the user not to worry or that they can skip a doctor; do not "
    "use 'все добре', 'усе добре', 'ти здоровий', 'ти здорова', 'не хвилюйся', 'нічого страшного'. "
    "Do NOT add a disclaimer or 'я не лікар' line.\n"
    "FOCUS: ground EVERY statement in the printed findings and conclusion — NEVER invent a "
    "finding, a measurement, a size, or a structure not in the document. Use the printed sizes and "
    "locations EXACTLY. Discuss the notable / abnormal findings; mention normal structures only "
    "briefly, where it genuinely reassures.\n" + NATURAL_VOICE + "\nTHE SECTION TO WRITE — "
)


def _narrative_overall_fallback(report: ExtractedReport) -> str:
    return report.conclusion or report.narrative or locale.LAB_INTERPRET_OVERALL_ATTENTION


def _narrative_attention_fallback(report: ExtractedReport) -> str:
    return report.conclusion or report.narrative or locale.LAB_INTERPRET_ALL_NORMAL


_NARRATIVE_SECTION_SPECS: tuple[_Section, ...] = (
    _Section(
        locale.INTERPRET_SECTION_OVERALL,
        "the big picture of the imaging findings in two or three lines — what the study shows and, "
        "plainly, how serious it looks; reflect the printed conclusion in plain words. Name the "
        "key abnormal findings briefly (e.g. 'камені в обох нирках') without going deep. If the "
        "document is essentially normal, say so plainly and reflect the conclusion.",
        _narrative_overall_fallback,
    ),
    _Section(
        locale.INTERPRET_SECTION_ATTENTION,
        "the NOTABLE / abnormal findings, grouped under a short *bold sub-heading* per organ or "
        "structure (e.g. *Ліва нирка*, *Права нирка*, *Сечовий міхур*). For each: what it MAY "
        "indicate ('може свідчити про…', cautious, never a definite diagnosis); HOW concerning it "
        "is (minor / worth watching / worth prompt attention); and what it can lead to if left "
        "unaddressed. Use the printed sizes and locations EXACTLY as given. If the document is "
        "essentially normal, write one short reassuring-in-data-terms line.",
        _narrative_attention_fallback,
    ),
    _Section(
        locale.INTERPRET_SECTION_HELP,
        "practical lifestyle & nutrition guidance relevant to the findings, under the same *bold "
        "sub-headings* — name SPECIFIC foods and habits to favour and to limit for the system "
        "involved, plus hydration, movement, etc. QUALITATIVE only: NO calorie/macro/fasting "
        "numbers and NO medication/supplement dose. If the document is normal, a brief general "
        "healthy-living orientation.",
        lambda _report: locale.LAB_INTERPRET_HELP_GENERIC,
    ),
    _Section(
        locale.INTERPRET_SECTION_DOCTOR,
        "whether and how soon to see a doctor and WHICH specialist — use the document's OWN "
        "recommendation if it prints one (e.g. уролог for нирки/сечовий міхур, невролог for "
        "головний мозок, гастроентеролог for органи травлення); say what to ask, recheck, or "
        "bring, and be concrete about timing.",
        lambda _report: locale.LAB_INTERPRET_ASK_DOCTOR,
    ),
)


async def _run_guarded(
    table: str, persona: str, *, runner: Runner, model: str | None
) -> str | None:
    """One interpretation call with a single retry; ``None`` if it can't produce safe text.

    Retries a transient failure (a one-off ``ok=False``) or a generation that trips the safety
    guard; does NOT retry a real timeout (that would only time out again).
    """
    for _attempt in range(2):
        try:
            result = await runner(
                table,
                append_system_prompt=persona,
                model=model,
                timeout_s=get_settings().claude_interpret_timeout_s,
            )
        except ClaudeUnavailable:
            return None
        if not result.ok or not result.text.strip():
            if result.error == "timeout":
                return None
            continue
        body = result.text.strip()
        try:
            # Guard the VISIBLE text: strip *bold*/_italic_ markers so a forbidden phrase can't
            # slip past by hiding a marker inside it (e.g. "все *добре*").
            assert_safe_output(strip_markup(body))
        except ValueError:
            continue
        return body
    return None


async def _interpret_section(
    table: str,
    spec: _Section,
    report: ExtractedReport,
    *,
    runner: Runner,
    model: str | None,
    sem: asyncio.Semaphore,
    base_persona: str,
) -> tuple[str, bool]:
    """Return ``(header + body, from_llm)`` for one section; a failed section uses its
    deterministic fallback so the rest of the reading still stands."""
    async with sem:  # cap concurrent `claude` processes (memory-bound)
        body = await _run_guarded(
            table, base_persona + spec.instruction, runner=runner, model=model
        )
    if body is None:
        return f"{spec.header}\n{spec.fallback(report)}", False
    return f"{spec.header}\n{body}", True


async def _interpret_parallel(
    report: ExtractedReport,
    summaries: list[TrendSummary],
    fallback: str,
    *,
    runner: Runner,
    model: str | None,
    specs: tuple[_Section, ...] = _SECTION_SPECS,
    base_persona: str = _SECTION_BASE_PERSONA,
) -> str:
    table = _interpret_table(report, summaries)
    sem = asyncio.Semaphore(max(1, get_settings().claude_interpret_concurrency))
    sections = await asyncio.gather(
        *(
            _interpret_section(
                table, spec, report, runner=runner, model=model, sem=sem, base_persona=base_persona
            )
            for spec in specs
        )
    )
    if not any(from_llm for _, from_llm in sections):
        # Every section failed — the LLM is effectively down; one clean fallback beats four stubs.
        return _finalize(fallback)
    return _finalize("\n\n".join(text for text, _ in sections))


async def interpret(
    report: ExtractedReport,
    summaries: list[TrendSummary],
    *,
    runner: Runner = run_claude,
    model: str | None = None,
) -> str:
    """An expert-level Ukrainian reading of a confirmed report, guaranteed safe.

    BOTH a tabular report AND a narrative/imaging document (МРТ/КТ/УЗД/висновок) get the same
    premium four-section reading (Загалом / Звернути увагу / Що допоможе / Коли до лікаря),
    generated as concurrent focused calls — a narrative just uses imaging-tailored section prompts
    grounded in its printed findings + conclusion. Every section passes ``assert_safe_output`` (with
    a deterministic fallback) and the disclaimer is appended.
    """
    fallback = assert_safe_output(deterministic_interpretation(report))
    if report.is_narrative:
        return await _interpret_parallel(
            report,
            summaries,
            fallback,
            runner=runner,
            model=model,
            specs=_NARRATIVE_SECTION_SPECS,
            base_persona=_NARRATIVE_SECTION_BASE_PERSONA,
        )
    return await _interpret_parallel(report, summaries, fallback, runner=runner, model=model)


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
