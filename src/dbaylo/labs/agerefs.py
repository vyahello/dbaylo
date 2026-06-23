"""Resolve an AGE-STRATIFIED reference range to a single numeric band.

Some analytes (ПСА is the classic case) print their reference as a TABLE of age bands, e.g.::

    <40 років: <1.4 · 40-50: <2.0 · 50-60: <3.1 · 60-70: <4.1 · >70: <4.4

A single ``ref_low``/``ref_high`` cannot represent that, so without resolution the chart shows
"норму не вказано". Here we pick the row matching the patient's age and hand the value off to the
ordinary :func:`refparse.parse_ref_range`. We NEVER invent a threshold — we use the lab's OWN
printed table (captured into ``ref_text``); if the text is not an age table, or no row matches,
we return ``None`` and the caller falls back to "no reference".

Pure: no LLM/DB/network.
"""

from __future__ import annotations

import re
from datetime import date

from dbaylo.labs.refparse import parse_ref_range

# One row of an age table: an age condition (<N / N-M / >N / "до N" / "понад N") then ": <value>".
_AGE_ROW_RE = re.compile(
    r"(?P<age>(?:<|>|≤|≥|до|понад|більше|менше)?\s*\d+(?:\s*[-–—]\s*\d+)?)\s*(?:рок\w*|р\.?)?\s*"
    r"[:\-–—]\s*(?P<val>[^;|\n,]+?(?:\d[^;|\n,]*)?)(?=$|[;|\n]|\s{2,}|,\s*(?:<|>|≤|≥|до|понад|менше)?\s*\d)",
    re.IGNORECASE,
)


def _age_matches(condition: str, age: int) -> bool:
    cond = condition.casefold().replace("–", "-").replace("—", "-")
    if m := re.search(r"(\d+)\s*-\s*(\d+)", cond):  # "40-50" -> [40, 50)
        return int(m.group(1)) <= age < int(m.group(2))
    if m := re.search(r"(?:<|≤|до|менше)\s*(\d+)", cond):  # "<40" / "до 40" -> age < 40
        return age < int(m.group(1))
    if m := re.search(r"(?:>|≥|понад|більше)\s*(\d+)", cond):  # ">70" -> age > 70
        return age > int(m.group(1))
    if m := re.fullmatch(r"\s*(\d+)\s*", cond):  # a bare "40" — treat as ">= 40" lower edge
        return age >= int(m.group(1))
    return False


def resolve_age_reference(
    ref_text: str | None, age: int | None
) -> tuple[float | None, float | None] | None:
    """The ``(low, high)`` for ``age`` from an age-stratified ``ref_text`` table, or ``None`` when
    the text is NOT an age table (so the caller keeps its normal handling). Needs >=2 age rows so a
    plain "< 1.4" is never mistaken for a table."""
    if not ref_text or age is None:
        return None
    rows = [
        (m.group("age").strip(), m.group("val").strip()) for m in _AGE_ROW_RE.finditer(ref_text)
    ]
    # Keep only rows whose value parses to a real numeric bound (drops noise like a header).
    rows = [(cond, val) for cond, val in rows if parse_ref_range(val) != (None, None)]
    if len(rows) < 2:
        return None
    for cond, val in rows:
        if _age_matches(cond, age):
            return parse_ref_range(val)
    return None


def age_on(birth_date: date | None, on: date | None) -> int | None:
    """Whole years from ``birth_date`` to ``on`` (the report date), or None if either is missing."""
    if birth_date is None or on is None:
        return None
    years = on.year - birth_date.year - ((on.month, on.day) < (birth_date.month, birth_date.day))
    return years if years >= 0 else None
