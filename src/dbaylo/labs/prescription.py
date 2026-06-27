"""Prescription extraction via the `claude` binary — read a рецепт / лист призначень.

Mirrors :mod:`dbaylo.labs.extraction`: the model is told to read a Ukrainian prescription
and return JSON of a fixed shape (drug name · dose · times · frequency), and the output is
parsed defensively — malformed / fenced / partial output degrades to
:class:`~dbaylo.labs.extraction.ExtractionFailed`, never an exception.

Rail #1: this is RECORD-KEEPING of what a clinician prescribed. We capture the dose so it can
be stored on :class:`~dbaylo.db.models.Medication` (record), but the bot never advises a dose
and the reminder text never carries one. The extractor only reports what the page shows — it
never invents a drug, dose, time, or frequency.

Lives in ``labs/`` (not ``bot/``/``companion/``) so importing ``run_claude`` here is fine: the
safety choke-point test scans only the bot-facing packages, exactly as for lab extraction.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from dbaylo.config import get_settings
from dbaylo.labs.extraction import ExtractionFailed, Runner
from dbaylo.llm import ClaudeUnavailable, run_claude

# Internal (English) instruction appended to Claude's system prompt. Not user-facing.
PRESCRIPTION_PERSONA = (
    "You are a precise extraction function for a Ukrainian medical PRESCRIPTION "
    "(рецепт / лист призначень / a doctor's medication list). Read the file you are given and "
    "extract every prescribed medication. Return JSON ONLY — no prose, no markdown, no code "
    "fences — matching this shape:\n"
    "{\n"
    '  "course": string | null,    // SHORT Ukrainian label naming the prescription as a doctor\n'
    "                              // would file it, from WHAT the meds treat / the specialty (an\n"
    "                              // alpha-blocker + sleep aid from a urologist -> a urological\n"
    "                              // course label). Best clinical guess from the drug list; null\n"
    "                              // ONLY if truly unclear. A filing label, not a diagnosis.\n"
    '  "medications": [\n'
    "    {\n"
    '      "name": string,         // drug name exactly as printed (Ukrainian/Latin)\n'
    '      "dose": string | null,  // dose PER INTAKE as printed: "500 мг", "1 таблетка",\n'
    '                              // "10 крапель"; null if not printed\n'
    '      "times": ["HH:MM", ...],// explicit 24h clock times if printed ("08:00"), else []\n'
    '      "frequency": string|null,// the printed frequency when there are NO clock times,\n'
    '                              // e.g. "двічі на день", "3 рази на добу", "вранці"\n'
    '      "duration": string|null // how LONG to take it, verbatim: "3 міс.", "1 міс.",\n'
    '                              // "10 днів", "2 тижні", "до 27.07"; null if none given\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "Report ONLY what the document shows for medication FIELDS — NEVER invent a drug, dose, time, "
    "frequency or duration; if a field is missing or illegible use null (or [] for times). The "
    "'course' label is the ONE exception: your best clinical naming of the set, allowed because it "
    "only organizes the user's own prescription. This is record-keeping — do not advise on a dose, "
    "diagnose the user, or comment. If the document is NOT a prescription / medication list, give "
    '{"course": null, "medications": []}.'
)

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class ExtractedMedication:
    """One prescribed medication as read from the page (never invented)."""

    name: str
    dose: str | None  # per-intake dose, stored as record (rail #1); never in reminder text
    times: tuple[str, ...]  # validated "HH:MM" strings, in printed order, de-duplicated
    frequency: str | None  # printed frequency when no explicit clock times were given
    duration: str | None = None  # how long to take it, verbatim ("3 міс.", "10 днів"); None if none


@dataclass(frozen=True)
class ExtractedPrescription:
    """A whole read prescription: its meds + a clinician-style course label (the one inferred)."""

    medications: list[ExtractedMedication]
    course: str | None = None


async def extract_prescription(
    file_path: str | Path, *, model: str | None = None, runner: Runner = run_claude
) -> ExtractedPrescription | ExtractionFailed:
    """Run a single extraction pass over a prescription image/PDF. Returns the medications it could
    read (possibly empty if the page is not a prescription) or :class:`ExtractionFailed`."""
    path = Path(file_path)
    if not path.is_file():
        return ExtractionFailed(f"file not found: {path}")

    parent = str(path.parent)
    prompt = f"Прочитай рецепт / лист призначень із файлу: {path}. Поверни лише JSON."
    try:
        result = await runner(
            prompt,
            append_system_prompt=PRESCRIPTION_PERSONA,
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
    parsed = parse_prescription(result.text)
    if parsed is None:
        return ExtractionFailed("could not read a prescription from the document")
    return parsed


# --- Defensive parsing ----------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_prescription(text: str) -> ExtractedPrescription | None:
    """Parse model output into a prescription (meds + course label), tolerating fences / partial
    output. ``None`` only when nothing JSON-like can be recovered (an empty med list is a valid
    "no medications found")."""
    data = _load_json_loosely(text)
    if not isinstance(data, dict):
        return None
    raw = data.get("medications")
    if not isinstance(raw, list):
        if raw is None:
            return ExtractedPrescription(medications=[], course=_coerce_str(data.get("course")))
        return None
    out: list[ExtractedMedication] = []
    for item in raw:
        med = _coerce_medication(item)
        if med is not None:
            out.append(med)
    return ExtractedPrescription(medications=out, course=_coerce_str(data.get("course")))


def _coerce_medication(item: object) -> ExtractedMedication | None:
    if not isinstance(item, dict):
        return None
    name = _coerce_str(item.get("name"))
    if not name:
        return None
    times: list[str] = []
    raw_times = item.get("times")
    if isinstance(raw_times, list):
        for value in raw_times:
            token = _coerce_str(value)
            if token and _TIME_RE.match(token) and token not in times:
                times.append(_pad_time(token))
    return ExtractedMedication(
        name=name,
        dose=_coerce_str(item.get("dose")),
        times=tuple(times),
        frequency=_coerce_str(item.get("frequency")),
        duration=_coerce_str(item.get("duration")),
    )


def _pad_time(token: str) -> str:
    """Normalize "8:00" -> "08:00" so display + parsing are uniform."""
    hh, _, mm = token.partition(":")
    return f"{int(hh):02d}:{mm}"


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
