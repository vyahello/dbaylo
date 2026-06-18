"""The triage engine: a pure function from symptoms to an escalation verdict.

The single safety invariant — "escalate up only" — is implemented here and is
the most heavily tested code in the project:

* The outcome action is ``max`` over matched rules, floored at ``Action.MONITOR``.
* No branch returns anything below the floor, and no message reassures the user
  that they can skip care (enforced at construction time via ``safety``).

A direct corollary, which the tests assert as a property: adding a symptom to a
report can never *lower* the action (monotonicity). Adding symptoms can only
match more rules, so ``max`` can only rise.
"""

from __future__ import annotations

from dbaylo import locale
from dbaylo.triage import safety
from dbaylo.triage.rules import RULES
from dbaylo.triage.types import (
    Action,
    SymptomReport,
    TriageOutcome,
    TriageRule,
)

# The care-oriented floor. When nothing matches we do not say "you're fine" — we
# default toward attention. This is the encoded form of the safety asymmetry.
DEFAULT_FLOOR: Action = Action.MONITOR

_FLOOR_MESSAGE = locale.FLOOR_MESSAGE


def evaluate(
    report: SymptomReport,
    rules: tuple[TriageRule, ...] = RULES,
) -> TriageOutcome:
    """Evaluate a symptom report and return a care-oriented verdict.

    ``rules`` is injectable for testing; it defaults to the active rule set.
    """
    matched = tuple(rule for rule in rules if rule.matches(report))

    # Escalate up only: take the strongest matched action, never below the floor.
    action = max((rule.action for rule in matched), default=DEFAULT_FLOOR)
    action = max(action, DEFAULT_FLOOR)

    message = _compose_message(action, matched)

    return TriageOutcome(
        action=action,
        matched_rule_ids=tuple(rule.id for rule in matched),
        # Every emitted string is validated: no dose directive, no "skip care".
        message=safety.assert_safe_output(message),
        disclaimer=safety.DISCLAIMER,
    )


def _compose_message(action: Action, matched: tuple[TriageRule, ...]) -> str:
    """Build the user-facing guidance from the rules at the decisive level."""
    if not matched:
        return _FLOOR_MESSAGE

    # Surface only the messages of the rules that set the (maximum) action, so the
    # guidance matches the verdict rather than burying it under milder advice.
    decisive = [rule for rule in matched if rule.action == action]
    return " ".join(rule.message for rule in decisive)
