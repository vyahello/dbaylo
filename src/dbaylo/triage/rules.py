"""Seeded triage rules.

Stage 1 seeds the kidney-stone red flags from the discovery document. Each rule
is atomic (AND within a rule; OR is expressed as separate rules) so the rule set
reads like a checklist a clinician could audit.

Escalation levels are deliberately biased upward — these are red flags, and the
engine's only job is to escalate toward care.
"""

from __future__ import annotations

from dbaylo import locale
from dbaylo.triage.types import Action, Symptom, TriageRule

# --- Kidney stones (the first seeded condition) ---------------------------------
#
# Discovery §55: "Seeded first for kidney stones (fever/chills, inability to
# urinate, uncontrolled vomiting, first-time blood in urine -> escalate)."

KIDNEY_STONE_RULES: tuple[TriageRule, ...] = (
    TriageRule(
        id="ks_inability_to_urinate",
        condition="kidney_stone",
        triggers=frozenset({Symptom.INABILITY_TO_URINATE}),
        action=Action.EMERGENCY,
        message=locale.KS_INABILITY_TO_URINATE,
        rationale="Acute urinary retention / obstruction is a urological emergency.",
        source="docs/dbaylo-discovery.md#L3",
    ),
    TriageRule(
        id="ks_fever_chills",
        condition="kidney_stone",
        triggers=frozenset({Symptom.FEVER, Symptom.CHILLS}),
        action=Action.URGENT_CARE,
        message=locale.KS_FEVER_CHILLS,
        rationale="Fever + chills raises concern for infection; needs prompt assessment.",
        source="docs/dbaylo-discovery.md#L3",
    ),
    TriageRule(
        # Edit B: fever + chills *with flank pain* is its own EMERGENCY rule, not a
        # branch in the engine. Both this and ks_fever_chills match, and max()
        # lifts the outcome to EMERGENCY — the engine stays a pure matcher.
        id="ks_fever_chills_flank",
        condition="kidney_stone",
        triggers=frozenset({Symptom.FEVER, Symptom.CHILLS, Symptom.FLANK_PAIN}),
        action=Action.EMERGENCY,
        message=locale.KS_FEVER_CHILLS_FLANK,
        rationale=(
            "Fever + chills + flank pain suggests an obstructed, infected stone "
            "(possible urosepsis) — a time-critical emergency."
        ),
        source="docs/dbaylo-discovery.md#L3",
    ),
    TriageRule(
        id="ks_uncontrolled_vomiting",
        condition="kidney_stone",
        triggers=frozenset({Symptom.UNCONTROLLED_VOMITING}),
        action=Action.URGENT_CARE,
        message=locale.KS_UNCONTROLLED_VOMITING,
        rationale="Intractable vomiting risks dehydration; needs prompt assessment.",
        source="docs/dbaylo-discovery.md#L3",
    ),
    TriageRule(
        id="ks_blood_first_time",
        condition="kidney_stone",
        triggers=frozenset({Symptom.BLOOD_IN_URINE_FIRST_TIME}),
        action=Action.SEE_DOCTOR,
        message=locale.KS_BLOOD_FIRST_TIME,
        rationale="New-onset haematuria always warrants medical evaluation.",
        source="docs/dbaylo-discovery.md#L3",
    ),
)

# The full active rule set. Later stages append rules for further conditions.
RULES: tuple[TriageRule, ...] = (*KIDNEY_STONE_RULES,)
