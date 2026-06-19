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

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

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
    '  "lab": string | null,\n'
    '  "results": [\n'
    "    {\n"
    '      "analyte": string,            // name exactly as printed (Ukrainian)\n'
    '      "value": number | null,       // dot decimal; convert "3,5" -> 3.5\n'
    '      "value_text": string | null,  // qualitative result e.g. "не виявлено"\n'
    '      "unit": string | null,\n'
    '      "ref_low": number | null,\n'
    '      "ref_high": number | null,\n'
    '      "ref_text": string | null     // range as printed if not simple low-high\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "Preserve analyte names exactly as printed. If a field is missing or illegible "
    "use null — never guess or invent values. Do not diagnose, interpret, or comment."
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
    prompt = f"Витягни результати аналізів із файлу: {path}. Поверни лише JSON."

    try:
        result = await runner(
            prompt,
            append_system_prompt=EXTRACTION_PERSONA,
            model=model,
            allowed_tools=["Read"],
            add_dirs=[parent],
            cwd=parent,
        )
    except ClaudeUnavailable as exc:
        return ExtractionFailed(f"claude unavailable: {exc}")

    if not result.ok:
        return ExtractionFailed(result.error or "extraction call failed")

    report = parse_extraction(result.text)
    if report is None or not report.results:
        return ExtractionFailed("could not parse any readable rows")
    return report


async def extract_with_escalation(
    file_path: str | Path,
    *,
    models: Sequence[str] = _DEFAULT_MODELS,
    runner: Runner = run_claude,
) -> ExtractionOutcome:
    """Try each model in order; return the first readable report, else the last failure."""
    outcome: ExtractionOutcome = ExtractionFailed("no models tried")
    for model in models:
        outcome = await extract(file_path, model=model, runner=runner)
        if isinstance(outcome, ExtractedReport):
            return outcome
    return outcome


# --- Defensive parsing ----------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_extraction(text: str) -> ExtractedReport | None:
    """Parse model output into an ExtractedReport, tolerating common messiness.

    Returns ``None`` only when nothing usable can be recovered.
    """
    data = _load_json_loosely(text)
    if not isinstance(data, dict):
        return None

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return None

    analytes: list[ExtractedAnalyte] = []
    for item in raw_results:
        analyte = _coerce_analyte(item)
        if analyte is not None:
            analytes.append(analyte)

    return ExtractedReport(
        results=analytes,
        report_date=_coerce_date(data.get("report_date")),
        lab=_coerce_str(data.get("lab")),
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

    return ExtractedAnalyte(
        analyte=name,
        value=value,
        value_text=value_text,
        unit=_coerce_str(item.get("unit")),
        ref_low=_coerce_float(item.get("ref_low")),
        ref_high=_coerce_float(item.get("ref_high")),
        ref_text=_coerce_str(item.get("ref_text")),
    )


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
