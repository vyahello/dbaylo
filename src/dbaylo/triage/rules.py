"""Seeded triage rules.

Stage 1 seeds the kidney-stone red flags from the discovery document. Each rule
is atomic (AND within a rule; OR is expressed as separate rules) so the rule set
reads like a checklist a clinician could audit.

Escalation levels are deliberately biased upward — these are red flags, and the
engine's only job is to escalate toward care.
"""

from __future__ import annotations

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
        message=(
            "Not being able to urinate can mean a blockage and needs to be seen "
            "right now. Please get emergency care or call emergency services."
        ),
        rationale="Acute urinary retention / obstruction is a urological emergency.",
        source="docs/dbaylo-discovery.md#L3",
    ),
    TriageRule(
        id="ks_fever_chills",
        condition="kidney_stone",
        triggers=frozenset({Symptom.FEVER, Symptom.CHILLS}),
        action=Action.URGENT_CARE,
        message=(
            "Fever with chills can signal an infection that needs prompt "
            "attention. Please seek urgent care today."
        ),
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
        message=(
            "Fever and chills together with flank pain can mean an infected, "
            "blocked kidney — this is an emergency. Please get emergency care or "
            "call emergency services now."
        ),
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
        message=(
            "Vomiting you can't keep on top of can leave you dehydrated and needs "
            "prompt care. Please seek urgent care today."
        ),
        rationale="Intractable vomiting risks dehydration; needs prompt assessment.",
        source="docs/dbaylo-discovery.md#L3",
    ),
    TriageRule(
        id="ks_blood_first_time",
        condition="kidney_stone",
        triggers=frozenset({Symptom.BLOOD_IN_URINE_FIRST_TIME}),
        action=Action.SEE_DOCTOR,
        message=(
            "Seeing blood in your urine for the first time should always be "
            "checked by a doctor. Please book a visit to have it looked at."
        ),
        rationale="New-onset haematuria always warrants medical evaluation.",
        source="docs/dbaylo-discovery.md#L3",
    ),
)

# The full active rule set. Later stages append rules for further conditions.
RULES: tuple[TriageRule, ...] = (*KIDNEY_STONE_RULES,)
