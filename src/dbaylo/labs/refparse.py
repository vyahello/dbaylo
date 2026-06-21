"""Derive numeric reference bounds from a printed reference string.

Labs print reference ranges in many shapes — "3.9 - 6.1", "< 5.2", "до 50", "> 0.9",
"≤ 0.5" — and the extractor sometimes captures the one-sided / odd ones as free text
(``ref_text``) instead of ``ref_low`` / ``ref_high``. Without numeric bounds the trend chart
can draw no norm band, so ~40% of rows showed no reference. This pure parser recovers the
bounds at extraction time, so the band shows wherever the range is numeric at all.

Pure: no LLM/DB/network. A genuinely non-numeric reference ("негативно", "не виявлено")
yields ``(None, None)`` — we never guess a number.
"""

from __future__ import annotations

import re

_NUM = r"\d+(?:\.\d+)?"
_RANGE_RE = re.compile(rf"({_NUM})\s*-\s*({_NUM})")
_UPPER_RE = re.compile(rf"(?:<=|≤|<|до)\s*({_NUM})")
_LOWER_RE = re.compile(rf"(?:>=|≥|>|від|більше)\s*({_NUM})")


def parse_ref_range(ref_text: str | None) -> tuple[float | None, float | None]:
    """Return ``(low, high)`` derived from a printed reference, or ``(None, None)``.

    Handles two-sided ("3.9-6.1"), upper-only ("< 5.2", "≤ 5", "до 50"), and lower-only
    ("> 0.9", "≥ 1", "від 4"). A non-numeric reference returns ``(None, None)``.
    """
    if not ref_text:
        return None, None
    text = ref_text.strip().casefold().replace(",", ".").replace("–", "-").replace("—", "-")
    if m := _RANGE_RE.search(text):  # two-sided "X - Y" (most specific — check first)
        return float(m.group(1)), float(m.group(2))
    if m := _UPPER_RE.search(text):  # "< X" / "≤ X" / "до X" -> upper bound only
        return None, float(m.group(1))
    if m := _LOWER_RE.search(text):  # "> X" / "≥ X" / "від X" -> lower bound only
        return float(m.group(1)), None
    return None, None
