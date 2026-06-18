"""Safety guards encoded in code, not just documented.

Two of the discovery's non-negotiable rails live here as executable checks:

* Rail #1 — "not a doctor, not a prescriber": every outcome carries a disclaimer,
  and bot-generated *output text* must never read as a dose directive.
* Rail #3 — "triage asymmetry, escalate up only": output text must never tell the
  user they can skip care.

This module is the *mechanism*; the Ukrainian vocabulary it checks against
(``FORBIDDEN_REASSURANCES``, the dose-directive patterns, the disclaimer) lives
in :mod:`dbaylo.locale`, so guard and tests read from one source.

Scope note (important): these scanners operate on **bot-generated output text**
(triage messages today; LLM output later) — *not* on database field names.
Storing what a doctor prescribed (``Medication.dose``, ``Medication.schedule``)
is record-keeping, not prescribing. The prohibition is on what Дбайло *says*,
never on what the user records.
"""

from __future__ import annotations

import re

from dbaylo import locale

# Re-exported so callers can keep importing the disclaimer from the guard module.
DISCLAIMER = locale.DISCLAIMER
FORBIDDEN_REASSURANCES = locale.FORBIDDEN_REASSURANCES

_DOSE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in locale.DOSE_DIRECTIVE_PATTERNS
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
