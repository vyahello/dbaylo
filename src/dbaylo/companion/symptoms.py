"""Deterministic free-text -> Symptom *detection* (the companion's keyword pass).

This module only turns Ukrainian free text into a set of :class:`Symptom` tokens;
it does **not** call the triage engine. The symptom -> triage escalation lives in
:mod:`dbaylo.safety.gate`, the single sanctioned choke-point. ``detect_symptoms``
stays here because the check-in flow also uses it to *record* the symptom column
(not to escalate).

The keyword map (``locale.SYMPTOM_KEYWORDS``) is limited and extensible, and is
kept disjoint from the wellness purging signals so triage's earlier pass cannot
mask a purging signal (see ``locale`` for the note).
"""

from __future__ import annotations

from dbaylo import locale
from dbaylo.triage.types import Symptom


def detect_symptoms(text: str) -> frozenset[Symptom]:
    """Return the Symptom tokens whose keywords appear in ``text`` (pure)."""
    lowered = text.casefold()
    found: set[Symptom] = set()
    for value, keywords in locale.SYMPTOM_KEYWORDS.items():
        if any(keyword.casefold() in lowered for keyword in keywords):
            found.add(Symptom(value))

    # A first-time blood-in-urine mention escalates via the rule-bearing token.
    if Symptom.BLOOD_IN_URINE in found and any(
        marker in lowered for marker in locale.FIRST_TIME_MARKERS
    ):
        found.add(Symptom.BLOOD_IN_URINE_FIRST_TIME)

    return frozenset(found)
