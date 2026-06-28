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
_DOSE_UNIT_SOFT: re.Pattern[str] = re.compile(locale.DOSE_UNIT_SOFT_PATTERN, re.IGNORECASE)
_DOSE_VERB: re.Pattern[str] = re.compile(locale.DOSE_VERB_PATTERN, re.IGNORECASE)
_DIET_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in locale.DIET_PRESCRIPTION_PATTERNS
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

    The PRIMARY (hard) signal: it keys on dosing *verb/intent* (приймай / по N /
    a dosage form / a per-time MASS amount / a counted frequency), not on a bare
    number+unit. So "80 кг" and "1500 мл на день" are *not* directives, while
    "приймай 2 таблетки" and "500 мг/добу" are. Operates on bot output text only
    (see the module docstring): it guards what Дбайло *says*, not what a user
    records about their own medication.
    """
    for pattern in _DOSE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def contains_dose_unit_mention(text: str) -> str | None:
    """Return a bare number+unit mention (e.g. "400 мг", "2000 мл"), else ``None``.

    The demoted SECONDARY signal: weaker than :func:`contains_dose_directive` and
    deliberately *not* part of :func:`assert_safe_output`, so legitimate companion
    numerics (body weight, hydration volumes) are never blocked. Exposed for soft
    routing / telemetry only.
    """
    match = _DOSE_UNIT_SOFT.search(text)
    return match.group(0) if match else None


def contains_dose_verb(text: str) -> str | None:
    """Return the first imperative dosing verb in ``text`` (приймай / випий / …), else ``None``.

    Narrow helper used to vet a medication reminder's doctor-attributed AMOUNT record: the count,
    dosage form, and strength the doctor wrote are shown as record-keeping, but if such a record
    also carries a dosing *verb* it would read as Дбайло *ordering* a dose (rail #1) and must be
    refused. Unlike :func:`contains_dose_directive`, this keys on the verb alone — the amount
    itself is allowed in the reminder's record by design.
    """
    match = _DOSE_VERB.search(text)
    return match.group(0) if match else None


def contains_diet_prescription(text: str) -> str | None:
    """Return the first restrictive-diet directive found in ``text``, else ``None``.

    Rail #6: precise calorie targets, macro-gram targets, and fasting protocols.
    Like the dose patterns, each requires a number, an imperative, or a named
    protocol, so cautionary copy ("голодування виснажує") and ALLOWED health-
    literacy ranges (sleep hours, hydration л/мл, activity frequency) stay safe.
    """
    for pattern in _DIET_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def assert_safe_output(text: str) -> str:
    """Validate a piece of bot-facing output text, returning it unchanged.

    Raises :class:`ValueError` if the text reads as a "skip the doctor"
    reassurance, a dose directive, or a restrictive-diet prescription (rail #6).
    Call this on anything Дбайло is about to say.
    """
    reassurance = contains_forbidden_reassurance(text)
    if reassurance is not None:
        raise ValueError(f"output contains a forbidden reassurance: {reassurance!r}")
    dose = contains_dose_directive(text)
    if dose is not None:
        raise ValueError(f"output reads as a dose directive: {dose!r}")
    diet = contains_diet_prescription(text)
    if diet is not None:
        raise ValueError(f"output reads as a restrictive-diet prescription: {diet!r}")
    return text
