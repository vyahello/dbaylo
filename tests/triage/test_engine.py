"""Engine tests — the safety core. The escalate-up-only invariant is the headline.

These tests exhaustively explore the symptom space (it is small and finite) so
the monotonicity property is proven, not sampled.
"""

from __future__ import annotations

import itertools

import pytest

from dbaylo.triage import evaluate
from dbaylo.triage.engine import DEFAULT_FLOOR
from dbaylo.triage.rules import RULES
from dbaylo.triage.types import Action, Symptom, SymptomReport, TriageRule

ALL_SYMPTOMS = tuple(Symptom)


def _all_reports() -> list[SymptomReport]:
    """Every subset of the symptom vocabulary (the full input space)."""
    reports = []
    for r in range(len(ALL_SYMPTOMS) + 1):
        for combo in itertools.combinations(ALL_SYMPTOMS, r):
            reports.append(SymptomReport.of(*combo))
    return reports


def test_empty_report_returns_floor_not_reassurance() -> None:
    outcome = evaluate(SymptomReport.of())
    assert outcome.action == DEFAULT_FLOOR
    assert outcome.matched_rule_ids == ()
    # The floor message defaults toward care; it never says "you're fine".
    assert "doctor" in outcome.message.lower()


def test_outcome_action_never_below_floor() -> None:
    for report in _all_reports():
        assert evaluate(report).action >= DEFAULT_FLOOR


def test_action_is_max_of_matched_rules() -> None:
    for report in _all_reports():
        outcome = evaluate(report)
        matched = [r for r in RULES if r.matches(report)]
        expected = max((r.action for r in matched), default=DEFAULT_FLOOR)
        expected = max(expected, DEFAULT_FLOOR)
        assert outcome.action == expected


@pytest.mark.parametrize("extra", ALL_SYMPTOMS, ids=lambda s: s.value)
def test_monotonicity_adding_a_symptom_never_lowers_action(extra: Symptom) -> None:
    """escalate-up-only, formalised: report ∪ {s} is never milder than report."""
    for report in _all_reports():
        before = evaluate(report).action
        after = evaluate(SymptomReport.of(*report.symptoms, extra)).action
        assert after >= before


def test_disclaimer_always_present() -> None:
    for report in _all_reports():
        assert evaluate(report).disclaimer
        assert "not a doctor" in evaluate(report).disclaimer.lower()


def test_matched_rule_ids_reported() -> None:
    outcome = evaluate(SymptomReport.of(Symptom.INABILITY_TO_URINATE))
    assert "ks_inability_to_urinate" in outcome.matched_rule_ids


def test_rules_are_injectable() -> None:
    custom = (
        TriageRule(
            id="t_x",
            condition="test",
            triggers=frozenset({Symptom.FEVER}),
            action=Action.SEE_DOCTOR,
            message="Please see a doctor about this fever.",
            rationale="test rule",
        ),
    )
    assert evaluate(SymptomReport.of(Symptom.FEVER), rules=custom).action == Action.SEE_DOCTOR
    # A symptom no custom rule covers falls back to the safe floor.
    assert evaluate(SymptomReport.of(Symptom.CHILLS), rules=custom).action == DEFAULT_FLOOR
