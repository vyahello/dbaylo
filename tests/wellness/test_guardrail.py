"""Unit tests for the L1 wellness guardrail — the deterministic disordered-eating
/ unsafe-goal safety core. Mirrors the bar set by the triage tests.
"""

from __future__ import annotations

import pytest

from dbaylo.triage.safety import DISCLAIMER, assert_safe_output
from dbaylo.wellness import Concern, GoalSpec, evaluate
from dbaylo.wellness.rules import GOAL_RULES, MAX_SUSTAINABLE_WEEKLY_LOSS_KG
from dbaylo.wellness.signals import TEXT_SIGNALS

# --- Floor: benign input is accepted (OK) --------------------------------------


def test_no_input_is_ok() -> None:
    outcome = evaluate()
    assert outcome.concern == Concern.OK
    assert outcome.accepted
    assert outcome.message == ""
    assert outcome.disclaimer == DISCLAIMER


@pytest.mark.parametrize(
    "text",
    [
        "хочу краще спати",
        "пити достатньо води щодня",
        "більше рухатися і гуляти",
        "набрати трохи м'язів у залі",
    ],
)
def test_healthy_goals_are_accepted(text: str) -> None:
    assert evaluate(text=text).concern == Concern.OK


def test_moderate_weight_loss_is_accepted() -> None:
    # ~0.5 kg/week is within the sustainable band -> OK.
    spec = GoalSpec(raw_text="схуднути на 4 кг", kind="weight_loss", loss_kg=4, weeks=8)
    assert evaluate(goal=spec).concern == Concern.OK


# --- REDIRECT: aggressive goal parameters --------------------------------------


def test_aggressive_weight_loss_is_redirected() -> None:
    spec = GoalSpec(
        raw_text="схуднути на 10 кг за 2 тижні", kind="weight_loss", loss_kg=10, weeks=2
    )
    outcome = evaluate(goal=spec)
    assert outcome.concern == Concern.REDIRECT
    assert "weight_loss_rate_too_fast" in outcome.matched_ids
    assert outcome.message  # a real redirect message, not empty


def test_threshold_is_exclusive() -> None:
    # Exactly at the threshold is allowed; just above it redirects.
    at = GoalSpec(raw_text="", kind="weight_loss", loss_kg=MAX_SUSTAINABLE_WEEKLY_LOSS_KG, weeks=1)
    above = GoalSpec(raw_text="", kind="weight_loss", loss_kg=1.5, weeks=1)
    assert evaluate(goal=at).concern == Concern.OK
    assert evaluate(goal=above).concern == Concern.REDIRECT


def test_from_to_weight_parsing_path() -> None:
    spec = GoalSpec(raw_text="", kind="weight_loss", current_kg=90, target_kg=80, weeks=2)
    assert spec.weekly_loss_kg() == pytest.approx(5.0)
    assert evaluate(goal=spec).concern == Concern.REDIRECT


# --- SUPPORT: disordered-pattern signals ---------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "я взагалі нічого не їм",
        "пропускаю прийоми їжі весь день",
        "викликаю блювоту після їжі",
        "маю спалити все з'їдене на тренуванні",
    ],
)
def test_disordered_text_escalates_to_support(text: str) -> None:
    outcome = evaluate(text=text)
    assert outcome.concern == Concern.SUPPORT
    assert outcome.message


def test_support_outranks_redirect() -> None:
    # A crash-diet phrase (REDIRECT) plus a purging phrase (SUPPORT) -> SUPPORT.
    text = "сиджу на жорсткій дієті і викликаю блювоту після їжі"
    outcome = evaluate(text=text)
    assert outcome.concern == Concern.SUPPORT


# --- Escalate-up-only property (monotonicity) ----------------------------------


def test_adding_a_signal_never_lowers_the_concern() -> None:
    base = evaluate(text="хочу більше рухатися")
    added = evaluate(text="хочу більше рухатися, але нічого не їм цілими днями")
    assert added.concern >= base.concern
    assert added.concern == Concern.SUPPORT


def test_goal_text_is_also_scanned() -> None:
    # The raw goal text feeds the signal scan even without a separate `text` arg.
    spec = GoalSpec(raw_text="хочу схуднути і викликати блювоту після їжі")
    assert evaluate(goal=spec).concern == Concern.SUPPORT


# --- Every emitted message is safe by construction -----------------------------


def test_all_rule_and_signal_messages_pass_the_safety_guard() -> None:
    for rule in GOAL_RULES:
        assert assert_safe_output(rule.message) == rule.message
    for signal in TEXT_SIGNALS:
        assert assert_safe_output(signal.message) == signal.message


def test_every_outcome_carries_the_disclaimer_and_safe_message() -> None:
    inputs = [
        evaluate(),
        evaluate(text="хочу краще спати"),
        evaluate(goal=GoalSpec(raw_text="-10 кг", kind="weight_loss", loss_kg=10, weeks=1)),
        evaluate(text="нічого не їм"),
    ]
    for outcome in inputs:
        assert outcome.disclaimer == DISCLAIMER
        assert assert_safe_output(outcome.message) == outcome.message


def test_rules_are_injectable() -> None:
    # An empty rule/signal set means everything is OK (the floor).
    spec = GoalSpec(raw_text="нічого не їм", kind="weight_loss", loss_kg=10, weeks=1)
    assert evaluate(goal=spec, text="нічого не їм", goal_rules=(), text_signals=()).concern == (
        Concern.OK
    )
