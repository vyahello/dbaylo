"""Each seeded kidney-stone red flag fires at the expected escalation level."""

from __future__ import annotations

import pytest

from dbaylo.triage import evaluate
from dbaylo.triage.rules import RULES
from dbaylo.triage.types import Action, Symptom, SymptomReport


@pytest.mark.parametrize(
    ("symptoms", "rule_id", "expected"),
    [
        ((Symptom.INABILITY_TO_URINATE,), "ks_inability_to_urinate", Action.EMERGENCY),
        ((Symptom.FEVER, Symptom.CHILLS), "ks_fever_chills", Action.URGENT_CARE),
        (
            (Symptom.FEVER, Symptom.CHILLS, Symptom.FLANK_PAIN),
            "ks_fever_chills_flank",
            Action.EMERGENCY,
        ),
        ((Symptom.UNCONTROLLED_VOMITING,), "ks_uncontrolled_vomiting", Action.URGENT_CARE),
        ((Symptom.BLOOD_IN_URINE_FIRST_TIME,), "ks_blood_first_time", Action.SEE_DOCTOR),
    ],
)
def test_seeded_red_flag(symptoms: tuple[Symptom, ...], rule_id: str, expected: Action) -> None:
    outcome = evaluate(SymptomReport.of(*symptoms))
    assert rule_id in outcome.matched_rule_ids
    assert outcome.action == expected


def test_fever_chills_flank_escalates_above_fever_chills_alone() -> None:
    """Edit B: the flank rule lifts fever+chills from URGENT_CARE to EMERGENCY via max()."""
    fc = evaluate(SymptomReport.of(Symptom.FEVER, Symptom.CHILLS)).action
    fcf = evaluate(SymptomReport.of(Symptom.FEVER, Symptom.CHILLS, Symptom.FLANK_PAIN)).action
    assert fc == Action.URGENT_CARE
    assert fcf == Action.EMERGENCY
    assert fcf > fc


def test_fever_alone_does_not_trigger_fever_chills_rule() -> None:
    outcome = evaluate(SymptomReport.of(Symptom.FEVER))
    assert "ks_fever_chills" not in outcome.matched_rule_ids


def test_all_rule_ids_are_unique() -> None:
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))


def test_every_rule_has_a_message_and_rationale() -> None:
    for rule in RULES:
        assert rule.message.strip()
        assert rule.rationale.strip()
        assert rule.triggers  # no empty-trigger (always-on) rules
