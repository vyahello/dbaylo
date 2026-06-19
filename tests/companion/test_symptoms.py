"""Symptom detection: deterministic free-text -> Symptom tokens.

The symptom -> triage *escalation* now lives in ``dbaylo.safety.gate`` (see
``tests/safety/test_gate.py``); this module only covers the keyword detection.
"""

from __future__ import annotations

from dbaylo.companion.symptoms import detect_symptoms
from dbaylo.triage.types import Symptom


def test_detects_multiple_symptom_tokens() -> None:
    found = detect_symptoms("у мене висока температура, озноб і болить бік")
    assert found == frozenset({Symptom.FEVER, Symptom.CHILLS, Symptom.FLANK_PAIN})


def test_no_symptom_text_returns_empty() -> None:
    assert detect_symptoms("сьогодні чудовий настрій і багато енергії") == frozenset()


def test_first_time_blood_adds_rule_bearing_token() -> None:
    found = detect_symptoms("вперше помітив кров у сечі")
    assert Symptom.BLOOD_IN_URINE in found
    assert Symptom.BLOOD_IN_URINE_FIRST_TIME in found


def test_purging_language_is_not_a_vomiting_symptom() -> None:
    """Disjointness: self-induced purging must not register as the vomiting symptom,
    or triage's earlier pass would mask the wellness purging signal."""
    found = detect_symptoms("викликаю блювоту після їжі")
    assert Symptom.UNCONTROLLED_VOMITING not in found
    assert found == frozenset()


def test_uncontrolled_vomiting_is_detected_for_involuntary_phrasing() -> None:
    found = detect_symptoms("не можу зупинити блювоту вже годину")
    assert Symptom.UNCONTROLLED_VOMITING in found
