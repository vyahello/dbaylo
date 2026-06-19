"""The safety gate: the canonical order, precedence, and safe messages."""

from __future__ import annotations

from dbaylo.safety import GateSource, screen
from dbaylo.triage.safety import DISCLAIMER, assert_safe_output
from dbaylo.wellness import Concern, GoalSpec


def test_benign_text_is_cleared() -> None:
    decision = screen("хочу більше гуляти на свіжому повітрі")
    assert decision.cleared
    assert not decision.short_circuited
    assert decision.source == GateSource.CLEARED
    assert decision.message == ""
    # The guardrail still ran (and cleared) on a cleared decision.
    assert decision.guardrail is not None and decision.guardrail.concern == Concern.OK


def test_symptoms_route_to_triage() -> None:
    decision = screen("у мене температура, озноб і біль у боці")
    assert decision.short_circuited
    assert decision.source == GateSource.TRIAGE
    assert decision.triage is not None
    assert DISCLAIMER in decision.message


def test_disordered_text_routes_to_guardrail() -> None:
    decision = screen("я нічого не їм цілими днями")
    assert decision.short_circuited
    assert decision.source == GateSource.GUARDRAIL
    assert decision.guardrail is not None and decision.guardrail.concern == Concern.SUPPORT
    assert DISCLAIMER in decision.message


def test_structured_goal_rate_redirects() -> None:
    spec = GoalSpec(
        raw_text="схуднути на 10 кг за 2 тижні", kind="weight_loss", loss_kg=10, weeks=2
    )
    decision = screen(spec.raw_text, goal=spec)
    assert decision.source == GateSource.GUARDRAIL
    assert decision.guardrail is not None and decision.guardrail.concern == Concern.REDIRECT


def test_symptom_outranks_disordered_signal() -> None:
    """Precedence: a medical red flag wins; the chain stops before the guardrail."""
    decision = screen("температура і озноб, і я нічого не їм цілими днями")
    assert decision.source == GateSource.TRIAGE
    assert decision.triage is not None
    # The guardrail never ran (short-circuited on the more acute match).
    assert decision.guardrail is None


def test_every_short_circuit_message_is_safe() -> None:
    for text in (
        "у мене температура і озноб",
        "я нічого не їм цілими днями",
        "хочу сидіти на жорсткій дієті",
    ):
        decision = screen(text)
        if decision.short_circuited:
            assert assert_safe_output(decision.message) == decision.message
            assert DISCLAIMER in decision.message
