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

_ADULT = 18  # the age at which the lab's "Дорослі" row applies
_SEX_F = ("жінк", "жіноч")
_SEX_M = ("чолов",)
# An age word inside a VALUE means the regex over-captured a nested group header — reject the row.
_AGE_WORD_RE = re.compile(r"рок|діт|дит|доросл|старше", re.IGNORECASE)

# One row of an age table: an age condition then ": <value>". The condition may be numeric
# (<N / N-M / >N / "до N" / "старше N") or a word group ("Дорослі", "Діти").
_AGE_ROW_RE = re.compile(
    r"(?P<age>"
    r"доросл\w*|діт\w*|дит\w*"  # word groups: "Дорослі" / "Діти"
    r"|(?:<|>|≤|≥|до|понад|більше|менше|старше)?\s*\d+(?:\s*[-–—]\s*\d+)?"  # numeric, opt. prefix
    r")\s*(?:рок\w*|р\.?)?\s*"
    r"[:\-–—]\s*(?P<val>[^;|\n,]+?(?:\d[^;|\n,]*)?)"
    r"(?=$|[;|\n]|\s{2,}|,\s*(?:<|>|≤|≥|до|понад|менше|старше|діт|доросл)?\s*\d|,\s*доросл)",
    re.IGNORECASE,
)


def _age_matches(condition: str, age: int) -> bool:
    cond = condition.casefold().replace("–", "-").replace("—", "-")
    if "доросл" in cond:  # "Дорослі" — adults
        return age >= _ADULT
    if "діт" in cond or "дит" in cond:  # "Діти" — children
        return age < _ADULT
    if m := re.search(r"(\d+)\s*-\s*(\d+)", cond):  # "40-50" -> [40, 50)
        return int(m.group(1)) <= age < int(m.group(2))
    if m := re.search(r"(?:<|≤|до|менше)\s*(\d+)", cond):  # "<40" / "до 40" -> age < 40
        return age < int(m.group(1))
    if m := re.search(
        r"(?:>|≥|понад|більше|старше)\s*(\d+)", cond
    ):  # ">70" / "старше 60" -> age > N
        return age > int(m.group(1))
    if m := re.fullmatch(r"\s*(\d+)\s*", cond):  # a bare "40" — treat as ">= 40" lower edge
        return age >= int(m.group(1))
    return False


def _sex_of(text: str) -> str | None:
    t = text.casefold()
    if any(s in t for s in _SEX_F):
        return "f"
    if any(s in t for s in _SEX_M):
        return "m"
    return None


def resolve_age_reference(
    ref_text: str | None, age: int | None, sex: str | None = None
) -> tuple[float | None, float | None] | None:
    """The ``(low, high)`` for this patient from an age- (and optionally sex-) stratified table in
    ``ref_text``, or ``None`` when it is NOT such a table (the caller keeps its normal handling).
    Needs >=2 rows so a plain "< 1.4" is never mistaken for a table. A SEX-split value ('Жінки …;
    Чоловіки …') is used only when the patient's sex is known and matches — never guessed."""
    if not ref_text or age is None:
        return None
    rows = [
        (m.group("age").strip(), m.group("val").strip()) for m in _AGE_ROW_RE.finditer(ref_text)
    ]
    # Keep rows whose value is a clean numeric bound. Reject a value that still carries an AGE word
    # ("Дорослі: 18-20 років: ≤1.1") — that is a nested group header the regex over-captured; the
    # real sub-rows ("20-60 років: ≤0.9") are matched separately, so dropping this avoids a WRONG
    # band (18-20) when the right one (≤0.9) exists.
    rows = [
        (cond, val)
        for cond, val in rows
        if parse_ref_range(val) != (None, None) and not _AGE_WORD_RE.search(val)
    ]
    if len(rows) < 2:
        return None
    for cond, val in rows:
        if not _age_matches(cond, age):
            continue
        val_sex = _sex_of(val)
        if val_sex is not None and val_sex != sex:
            continue  # a sex-specific value for the other (or unknown) sex — don't guess
        return parse_ref_range(val)
    return None


def age_on(birth_date: date | None, on: date | None) -> int | None:
    """Whole years from ``birth_date`` to ``on`` (the report date), or None if either is missing."""
    if birth_date is None or on is None:
        return None
    years = on.year - birth_date.year - ((on.month, on.day) < (birth_date.month, birth_date.day))
    return years if years >= 0 else None
