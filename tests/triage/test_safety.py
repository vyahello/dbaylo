"""Safety guards — discovery rails #1 and #3, as executable checks."""

from __future__ import annotations

import pytest

from dbaylo.triage import evaluate
from dbaylo.triage.engine import _FLOOR_MESSAGE
from dbaylo.triage.rules import RULES
from dbaylo.triage.safety import (
    DISCLAIMER,
    assert_safe_output,
    contains_dose_directive,
    contains_forbidden_reassurance,
)
from dbaylo.triage.types import Symptom, SymptomReport


def _all_emitted_messages() -> list[str]:
    return [rule.message for rule in RULES] + [_FLOOR_MESSAGE, DISCLAIMER]


# --- Rail #3: no "skip the doctor" reassurance anywhere -------------------------


@pytest.mark.parametrize("text", _all_emitted_messages())
def test_no_message_contains_forbidden_reassurance(text: str) -> None:
    assert contains_forbidden_reassurance(text) is None


def test_forbidden_reassurance_is_detected() -> None:
    assert contains_forbidden_reassurance("Honestly, you're fine, skip the doctor.")
    assert contains_forbidden_reassurance("No need to see a doctor here.")
    assert contains_forbidden_reassurance("All good") is None


# --- Rail #1: no dose directive in OUTPUT TEXT (not field names) ----------------


@pytest.mark.parametrize("text", _all_emitted_messages())
def test_no_message_reads_as_dose_directive(text: str) -> None:
    assert contains_dose_directive(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Take 2 tablets after food.",
        "Ibuprofen 400 mg twice a day.",
        "Use 5ml every morning.",
        "Take 3 times a day with water.",
    ],
)
def test_dose_directive_is_detected(text: str) -> None:
    assert contains_dose_directive(text) is not None


def test_storing_a_dose_is_not_output_and_is_allowed() -> None:
    """A medication record a user enters is data, not bot output — guard ignores it.

    The guard only inspects strings the bot is about to *say*. Recording
    'Ibuprofen 400 mg' as Medication data never passes through assert_safe_output.
    """
    # Sanity: the guard would flag it *if* it were bot output...
    with pytest.raises(ValueError):
        assert_safe_output("Ibuprofen 400 mg twice a day.")
    # ...but the field name / stored value itself is never run through the guard.


def test_assert_safe_output_passes_clean_text() -> None:
    clean = "Please see a doctor to have this looked at."
    assert assert_safe_output(clean) == clean


def test_assert_safe_output_rejects_reassurance() -> None:
    with pytest.raises(ValueError, match="forbidden reassurance"):
        assert_safe_output("You're fine, nothing to worry about.")


# --- Every real outcome is safe by construction --------------------------------


def test_every_outcome_passes_safety_guards() -> None:
    reports = [
        SymptomReport.of(),
        SymptomReport.of(Symptom.FEVER),
        SymptomReport.of(Symptom.FEVER, Symptom.CHILLS),
        SymptomReport.of(Symptom.FEVER, Symptom.CHILLS, Symptom.FLANK_PAIN),
        SymptomReport.of(Symptom.INABILITY_TO_URINATE),
        SymptomReport.of(Symptom.BLOOD_IN_URINE_FIRST_TIME),
    ]
    for report in reports:
        outcome = evaluate(report)
        assert contains_forbidden_reassurance(outcome.message) is None
        assert contains_dose_directive(outcome.message) is None
        assert outcome.disclaimer == DISCLAIMER
