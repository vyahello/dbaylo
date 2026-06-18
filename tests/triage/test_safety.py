"""Safety guards — discovery rails #1 and #3, as executable checks.

The vocabulary is Ukrainian (it lives in ``dbaylo.locale``), so these assertions
are written against Ukrainian phrases — both the legitimate bot copy that must
pass and the forbidden phrasing that must be caught.
"""

from __future__ import annotations

import pytest

from dbaylo import locale
from dbaylo.triage import evaluate
from dbaylo.triage.rules import RULES
from dbaylo.triage.safety import (
    DISCLAIMER,
    assert_safe_output,
    contains_dose_directive,
    contains_forbidden_reassurance,
)
from dbaylo.triage.types import Symptom, SymptomReport


def _all_emitted_messages() -> list[str]:
    return [rule.message for rule in RULES] + [locale.FLOOR_MESSAGE, DISCLAIMER]


# --- Rail #3: no "skip the doctor" reassurance anywhere -------------------------


@pytest.mark.parametrize("text", _all_emitted_messages())
def test_no_message_contains_forbidden_reassurance(text: str) -> None:
    assert contains_forbidden_reassurance(text) is None


def test_forbidden_reassurance_is_detected() -> None:
    assert contains_forbidden_reassurance("Чесно, все добре, можеш не йти до лікаря.")
    assert contains_forbidden_reassurance("Лікар не потрібен.")
    assert contains_forbidden_reassurance("Та це дрібниця, нічого страшного.")
    # Innocuous copy is not flagged.
    assert contains_forbidden_reassurance("Дякую, я занотував.") is None


# --- Rail #1: no dose directive in OUTPUT TEXT (not field names) ----------------


@pytest.mark.parametrize("text", _all_emitted_messages())
def test_no_message_reads_as_dose_directive(text: str) -> None:
    assert contains_dose_directive(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Приймай 2 таблетки після їжі.",
        "Ібупрофен 400 мг двічі на день.",
        "Використовуй 5 мл щоранку.",
        "Призначаю по 2 капсули на день.",
        "Пий по 10 крапель тричі на день.",
    ],
)
def test_dose_directive_is_detected(text: str) -> None:
    assert contains_dose_directive(text) is not None


def test_negated_medication_advice_is_not_a_dose_directive() -> None:
    """Legitimate, care-oriented copy that mentions meds without dosing is safe."""
    safe = "Не приймай ліки без призначення лікаря."
    assert contains_dose_directive(safe) is None
    # The disclaimer says "я не призначаю лікування" — must not self-trip the guard.
    assert contains_dose_directive(DISCLAIMER) is None


def test_storing_a_dose_is_not_output_and_is_allowed() -> None:
    """A medication record a user enters is data, not bot output — guard ignores it.

    The guard only inspects strings the bot is about to *say*. Recording
    'Ібупрофен 400 мг' as Medication data never passes through assert_safe_output.
    """
    # Sanity: the guard would flag it *if* it were bot output...
    with pytest.raises(ValueError):
        assert_safe_output("Ібупрофен 400 мг двічі на день.")
    # ...but the field name / stored value itself is never run through the guard.


def test_assert_safe_output_passes_clean_text() -> None:
    clean = "Будь ласка, звернись до лікаря, щоб це оглянути."
    assert assert_safe_output(clean) == clean


def test_assert_safe_output_rejects_reassurance() -> None:
    with pytest.raises(ValueError, match="forbidden reassurance"):
        assert_safe_output("Все добре, нічого страшного.")


def test_assert_safe_output_rejects_dose_directive() -> None:
    with pytest.raises(ValueError, match="dose directive"):
        assert_safe_output("Приймай 2 таблетки двічі на день.")


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
