"""Lab extraction via the `claude` binary — and a defensive parser.

The model is told to read a Ukrainian lab form and return JSON of a fixed shape.
Output is constrained by the prompt (not a schema flag) and validated here: any
malformed, partial, or fenced output is tolerated, and an unrecoverable response
becomes :class:`ExtractionFailed` rather than an exception — the bot never
crashes on bad model output, it falls back to asking the user.

Default model is ``sonnet`` (vision-capable); extraction escalates to ``opus``
for forms that fail. ``haiku`` is intentionally never used for messy scans.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dbaylo.config import get_settings
from dbaylo.labs.labnames import normalize_lab
from dbaylo.labs.pdf_split import is_multipage_pdf, split_into_chunks
from dbaylo.labs.refparse import parse_ref_range
from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport
from dbaylo.llm import ClaudeResult, ClaudeUnavailable, run_claude

# Runner type so tests can inject a fake and never spawn a subprocess.
Runner = Callable[..., Awaitable[ClaudeResult]]

# Internal (English) instruction appended to Claude's system prompt. Not user-facing.
EXTRACTION_PERSONA = (
    "You are a precise extraction function for Ukrainian laboratory result forms. "
    "Read the lab report file you are given and extract every analyte row. "
    "Return JSON ONLY — no prose, no markdown, no code fences — matching this shape:\n"
    "{\n"
    '  "report_date": "YYYY-MM-DD" | null,\n'
    '  "lab": string | null,             // the lab BRAND / network (logo or letterhead),\n'
    "                                    // e.g. 'Синево', 'ДІЛА', 'Інвітро', plus the city if\n"
    "                                    // shown — 'Синево, Львів'. Not a bare 'Лабораторія X'\n"
    '  "kind": "tabular" | "narrative",  // "tabular" = an analyte results table;\n'
    '                                    // "narrative" = a descriptive medical document\n'
    "                                    // (МРТ/КТ/УЗД/висновок/виписка/опис) with no table\n"
    '  "report_type": string | null,     // for narrative: the study/document type, e.g.\n'
    "                                    // 'МРТ головного мозку', 'УЗД органів малого тазу'\n"
    '  "narrative": string | null,       // for narrative: the KEY FINDINGS body as printed\n'
    "                                    // (the descriptive part), faithfully, no invention\n"
    '  "conclusion": string | null,      // the report\'s OVERALL conclusion line if it\n'
    "                                    // prints one (e.g. 'Нормозооспермія', or the МРТ\n"
    "                                    // 'Висновок'); NOT an analyte row\n"
    '  "results": [                      // analyte rows for a TABULAR report; [] for narrative\n'
    "    {\n"
    '      "section": string | null,     // the PANEL the row is printed under, so a combined\n'
    "                                    // report keeps its groups apart and a name in two\n"
    "                                    // panels is never confused. Use the heading as printed,\n"
    "                                    // else infer one of: 'Загальний аналіз крові',\n"
    "                                    // 'Біохімічний аналіз крові', 'Загальний аналіз сечі',\n"
    "                                    // 'Мікроскопія осаду сечі' (blood vs urine MUST differ)\n"
    '      "analyte": string,            // name exactly as printed (Ukrainian)\n'
    '      "value": number | null,       // dot decimal; convert "3,5" -> 3.5\n'
    '      "value_text": string | null,  // qualitative result e.g. "не виявлено"\n'
    '      "unit": string | null,\n'
    '      "ref_low": number | null,     // ALWAYS fill numeric bounds when the range is\n'
    '      "ref_high": number | null,    // numeric: "3.9-6.1"->low=3.9,high=6.1; "< 5.2" or\n'
    "                                    // 'до 5.2'->high=5.2; '> 0.9' or 'від 0.9'->low=0.9\n"
    '      "ref_text": string | null,    // the range VERBATIM as printed; for a NON-numeric\n'
    "                                    // reference ('негативно', 'не виявлено') use this only\n"
    '      "out_of_range": boolean | null // TRUE if the LAB ITSELF marks this row as\n'
    "                                    // outside the reference / in an attention zone\n"
    "                                    // (boxed, highlighted, bold, asterisk, colour),\n"
    "                                    // OR the value is plainly outside the printed\n"
    "                                    // reference; FALSE if clearly within reference;\n"
    "                                    // null if there is no reference to judge by\n"
    "    }\n"
    "  ]\n"
    "}\n"
    "Preserve analyte names exactly as printed. If a field is missing or illegible "
    "use null — never guess or invent values. Report ONLY what the document shows (incl. its "
    "own out-of-range marks); do not diagnose, interpret, or comment. A document with no "
    "analyte table is 'narrative' — capture its report_type, narrative findings, and conclusion. "
    "An imaging / descriptive study (МРТ/КТ/УЗД/рентген/висновок/опис/виписка) is ALWAYS "
    "kind='narrative' with results=[] — put its sentences in 'narrative', NEVER turn them, the "
    "patient details, or the device specs into result rows. Use 'results' ONLY for an actual "
    "analyte table with measured values."
)

_DEFAULT_MODELS: tuple[str, ...] = ("sonnet", "opus")


@dataclass(frozen=True)
class ExtractionFailed:
    """A non-fatal extraction failure; carries a reason for logging/UX."""

    reason: str


ExtractionOutcome = ExtractedReport | ExtractionFailed


async def extract(
    file_path: str | Path,
    *,
    model: str | None = None,
    runner: Runner = run_claude,
) -> ExtractionOutcome:
    """Run a single extraction pass over ``file_path``."""
    path = Path(file_path)
    if not path.is_file():
        return ExtractionFailed(f"file not found: {path}")

    parent = str(path.parent)
    prompt = f"Прочитай медичний документ із файлу: {path}. Поверни лише JSON."

    try:
        result = await runner(
            prompt,
            append_system_prompt=EXTRACTION_PERSONA,
            model=model,
            allowed_tools=["Read"],
            add_dirs=[parent],
            cwd=parent,
            timeout_s=get_settings().claude_extract_timeout_s,
        )
    except ClaudeUnavailable as exc:
        return ExtractionFailed(f"claude unavailable: {exc}")

    if not result.ok:
        return ExtractionFailed(result.error or "extraction call failed")

    report = parse_extraction(result.text)
    if report is None or not report.is_usable:
        return ExtractionFailed("could not read a table or a narrative from the document")
    return report


async def extract_with_escalation(
    file_path: str | Path,
    *,
    models: Sequence[str] = _DEFAULT_MODELS,
    runner: Runner = run_claude,
) -> ExtractionOutcome:
    """Try each model in order; return the first readable report, else the last failure.

    Escalating to the (slower) next model helps when a pass returned unreadable output —
    but NOT after a timeout: the slower model would only time out too and double the wait.
    So a timeout stops the escalation and fails fast.
    """
    outcome: ExtractionOutcome = ExtractionFailed("no models tried")
    for model in models:
        outcome = await extract(file_path, model=model, runner=runner)
        if isinstance(outcome, ExtractedReport):
            return outcome
        if isinstance(outcome, ExtractionFailed) and "timeout" in outcome.reason:
            return outcome
    return outcome


# --- Paged extraction (split a multi-page PDF, read pages concurrently, merge) ---


def merge_reports(reports: Sequence[ExtractedReport]) -> ExtractedReport:
    """Merge per-page reports into one: rows in page order (exact duplicates dropped),
    metadata from the first page that prints it, narratives concatenated.

    Pure function — the page splits are independent, so this is where a coherent whole is
    reassembled. Keeping it pure makes the merge fully unit-testable.
    """
    results: list[ExtractedAnalyte] = []
    seen: set[tuple[str | None, str, float | None, str | None, str | None]] = set()
    report_date: date | None = None
    lab: str | None = None
    report_type: str | None = None
    conclusion: str | None = None
    narratives: list[str] = []
    for report in reports:
        for analyte in report.results:
            key = (
                analyte.section,
                analyte.analyte.strip().casefold(),
                analyte.value,
                analyte.value_text,
                analyte.unit,
            )
            if key in seen:
                continue
            seen.add(key)
            results.append(analyte)
        report_date = report_date or report.report_date
        # Chunks can disagree on the lab (one page shows the brand "Синево", another only the
        # facility line "Лабораторія Львів"): keep the most complete name, not just the first.
        if report.lab and (lab is None or len(report.lab) > len(lab)):
            lab = report.lab
        report_type = report_type or report.report_type
        conclusion = conclusion or report.conclusion
        if report.narrative:
            narratives.append(report.narrative)
    return ExtractedReport(
        results=results,
        report_date=report_date,
        lab=normalize_lab(lab),
        report_type=report_type,
        conclusion=conclusion,
        narrative="\n\n".join(narratives) or None,
    )


async def extract_paged(
    file_path: str | Path,
    *,
    models: Sequence[str] = _DEFAULT_MODELS,
    runner: Runner = run_claude,
    concurrency: int | None = None,
) -> ExtractionOutcome:
    """Split a PDF into ``concurrency`` contiguous chunks, extract them in parallel, and merge.

    Chunking by the concurrency budget (not one-per-page) is deliberate: each ``claude`` call has
    a heavy fixed start-up cost, so a few multi-page calls run at once beat dozens of tiny serial
    ones. ``CLAUDE_EXTRACT_CONCURRENCY`` caps it because each process is memory-hungry. A chunk
    failure is tolerated — as long as some chunk is readable its rows are kept; only an all-chunks
    failure surfaces as :class:`ExtractionFailed`.
    """
    limit = concurrency if concurrency is not None else get_settings().claude_extract_concurrency
    with split_into_chunks(file_path, max(1, limit)) as chunks:
        outcomes = await asyncio.gather(
            *(extract_with_escalation(str(chunk), models=models, runner=runner) for chunk in chunks)
        )

    reports = [o for o in outcomes if isinstance(o, ExtractedReport)]
    if not reports:
        first_failure = next((o for o in outcomes if isinstance(o, ExtractionFailed)), None)
        return first_failure or ExtractionFailed("no chunk could be read")
    merged = merge_reports(reports)
    if not merged.is_usable:
        return ExtractionFailed("could not read a table or a narrative from the document")
    return merged


async def extract_document(
    file_path: str | Path,
    *,
    models: Sequence[str] = _DEFAULT_MODELS,
    runner: Runner = run_claude,
) -> ExtractionOutcome:
    """The entry point the bot uses: page a multi-page PDF, else a single extraction pass.

    Routing here (not in the handler) keeps the bot oblivious to how a document is read.
    """
    if is_multipage_pdf(file_path):
        return await extract_paged(file_path, models=models, runner=runner)
    return await extract_with_escalation(file_path, models=models, runner=runner)


# --- Defensive parsing ----------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _has_real_rows(analytes: list[ExtractedAnalyte]) -> bool:
    """True if any row is a real measurement (a number, a reference, or a qualitative result) —
    not just stray text the extractor lifted from a non-table document (patient / device info)."""
    return any(
        a.value is not None or a.ref_low is not None or a.ref_high is not None or a.value_text
        for a in analytes
    )


def parse_extraction(text: str) -> ExtractedReport | None:
    """Parse model output into an ExtractedReport, tolerating common messiness.

    Returns ``None`` only when nothing usable can be recovered.
    """
    data = _load_json_loosely(text)
    if not isinstance(data, dict):
        return None

    # A narrative document legitimately has no "results"; treat a missing/odd value as
    # empty and let the caller decide usability (results OR a narrative body).
    raw_results = data.get("results")
    raw_results = raw_results if isinstance(raw_results, list) else []

    analytes: list[ExtractedAnalyte] = []
    for item in raw_results:
        analyte = _coerce_analyte(item)
        if analyte is not None:
            analytes.append(analyte)

    kind = (_coerce_str(data.get("kind")) or "").strip().casefold()
    report_type = _coerce_str(data.get("report_type"))
    narrative = _coerce_str(data.get("narrative"))

    # Narrative detection, robust to LLM variance (the bug behind the МРТ "send the table again"):
    # an imaging/descriptive document is narrative when the model SAYS so (kind), OR it carries a
    # findings body / study type and NONE of its rows is a real measurement. A stray patient/device
    # "row" the extractor lifts from a non-table doc has no value/ref, so it can no longer discard a
    # captured narrative into an empty 'tabular' report.
    is_narrative = kind == "narrative" or (
        bool(narrative or report_type) and not _has_real_rows(analytes)
    )
    if is_narrative:
        analytes = []  # a narrative document carries no analyte rows
    else:
        narrative = report_type = None  # a tabular document carries no findings body / study type

    return ExtractedReport(
        results=analytes,
        report_date=_coerce_date(data.get("report_date")),
        lab=normalize_lab(_coerce_str(data.get("lab"))),
        conclusion=_coerce_str(data.get("conclusion")),
        report_type=report_type,
        narrative=narrative,
    )


def _load_json_loosely(text: str) -> object:
    """json.loads with code-fence stripping and a brace-substring fallback."""
    if not text or not text.strip():
        return None
    candidate = text.strip()

    fenced = _FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Last resort: grab the outermost {...} and try again.
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _coerce_analyte(item: object) -> ExtractedAnalyte | None:
    if not isinstance(item, dict):
        return None
    name = _coerce_str(item.get("analyte"))
    if not name:
        return None

    value = _coerce_float(item.get("value"))
    value_text = _coerce_str(item.get("value_text"))
    # If the model put a non-numeric value in `value`, keep it as text.
    if value is None and value_text is None:
        raw_value = item.get("value")
        if isinstance(raw_value, str) and raw_value.strip():
            value_text = raw_value.strip()

    ref_text = _coerce_str(item.get("ref_text"))
    ref_low = _coerce_float(item.get("ref_low"))
    ref_high = _coerce_float(item.get("ref_high"))
    if ref_low is None and ref_high is None and ref_text:
        # The model left a one-sided/odd range as free text ("< 5.2", "до 50") — recover the
        # numeric bound so the trend chart can still draw the norm band.
        ref_low, ref_high = parse_ref_range(ref_text)

    return ExtractedAnalyte(
        analyte=name,
        value=value,
        value_text=value_text,
        unit=_coerce_str(item.get("unit")),
        ref_low=ref_low,
        ref_high=ref_high,
        ref_text=ref_text,
        out_of_range=_coerce_bool(item.get("out_of_range")),
        section=_coerce_str(item.get("section")),
    )


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_bool(value: object) -> bool | None:
    """Tolerant bool: real bools, or the strings the model sometimes emits."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().casefold()
        if token in ("true", "yes", "1", "так"):
            return True
        if token in ("false", "no", "0", "ні"):
            return False
    return None


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):  # bool is an int subclass — exclude it
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _coerce_date(value: object) -> date | None:
    text = _coerce_str(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
