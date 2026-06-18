"""Safety guards encoded in code, not just documented.

Two of the discovery's non-negotiable rails live here as executable checks:

* Rail #1 — "not a doctor, not a prescriber": every outcome carries a disclaimer,
  and bot-generated *output text* must never read as a dose directive.
* Rail #3 — "triage asymmetry, escalate up only": output text must never tell the
  user they can skip care.

Scope note (important): these scanners operate on **bot-generated output text**
(triage messages today; LLM output later) — *not* on database field names.
Storing what a doctor prescribed (``Medication.dose``, ``Medication.schedule``)
is record-keeping, not prescribing. The prohibition is on what Дбайло *says*,
never on what the user records.
"""

from __future__ import annotations

import re

DISCLAIMER = (
    "I'm Дбайло — a caring friend, not a doctor. I can't diagnose or prescribe. "
    "When in doubt, talk to a medical professional."
)

# Phrases that would amount to telling the user they can skip care. The engine
# can only escalate up, so these must never appear in any message it emits.
FORBIDDEN_REASSURANCES: tuple[str, ...] = (
    "skip the doctor",
    "no need to see a doctor",
    "don't need a doctor",
    "do not need a doctor",
    "no need for a doctor",
    "you're fine",
    "you are fine",
    "nothing to worry about",
    "you don't need to worry",
    "no need to worry",
    "it's nothing",
    "it is nothing",
)

# Dose-directive shapes: a quantity + unit, or imperative dosing language. These
# are checked against *output text only* — never against schema/field names.
_DOSE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|µg|g|ml|iu|units?)\b", re.IGNORECASE),
    re.compile(r"\btake\s+\d+\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+(?:tablets?|pills?|capsules?|drops?)\b", re.IGNORECASE),
    re.compile(r"\b(?:once|twice|\d+\s+times?)\s+(?:a|per)\s+day\b", re.IGNORECASE),
)


def contains_forbidden_reassurance(text: str) -> str | None:
    """Return the first forbidden reassurance found in ``text``, else ``None``."""
    lowered = text.lower()
    for phrase in FORBIDDEN_REASSURANCES:
        if phrase in lowered:
            return phrase
    return None


def contains_dose_directive(text: str) -> str | None:
    """Return the first dose-directive match found in ``text``, else ``None``.

    Operates on bot output text only (see the module docstring): this guards
    what Дбайло *says*, not what a user records about their own medication.
    """
    for pattern in _DOSE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def assert_safe_output(text: str) -> str:
    """Validate a piece of bot-facing output text, returning it unchanged.

    Raises :class:`ValueError` if the text reads as a dose directive or as a
    "skip the doctor" reassurance. Call this on anything Дбайло is about to say.
    """
    reassurance = contains_forbidden_reassurance(text)
    if reassurance is not None:
        raise ValueError(f"output contains a forbidden reassurance: {reassurance!r}")
    dose = contains_dose_directive(text)
    if dose is not None:
        raise ValueError(f"output reads as a dose directive: {dose!r}")
    return text
