"""Deterministic free-text -> Symptom routing (companion -> triage handoff).

The companion detects symptom *tokens* from Ukrainian free text and hands a
:class:`SymptomReport` to the deterministic triage engine. The LLM never makes the
escalation call — this keyword pass and ``triage.evaluate`` do, exactly as the
discovery's rail demands.

The keyword map (``locale.SYMPTOM_KEYWORDS``) is limited and extensible, and is
kept disjoint from the wellness purging signals so triage's earlier pass cannot
mask a purging signal (see ``locale`` for the note).
"""

from __future__ import annotations

from dbaylo import locale
from dbaylo.triage import evaluate
from dbaylo.triage.types import Symptom, SymptomReport, TriageOutcome


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


def triage_for_text(text: str) -> TriageOutcome | None:
    """Route free text to triage; ``None`` when no symptom token is detected.

    When something is detected, the returned outcome is produced entirely by the
    deterministic engine — the companion surfaces it verbatim and does not call
    the LLM for that turn.
    """
    symptoms = detect_symptoms(text)
    if not symptoms:
        return None
    return evaluate(SymptomReport(symptoms))
