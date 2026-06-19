"""The wellness guardrail: a pure function from a goal/text to a concern verdict.

The L1 safety core, analogous to :func:`dbaylo.triage.engine.evaluate`:

* The outcome concern is ``max`` over every matched rule/signal, floored at
  ``Concern.OK`` — "escalate up only". Adding a signal can only raise the concern
  (monotonicity), never lower it.
* The composed message is run through :func:`safety.assert_safe_output`, so the
  guardrail itself can never emit a dose directive or a restrictive-diet
  prescription.

No LLM, no DB, no network — enforced by a structural test.
"""

from __future__ import annotations

from dbaylo.triage import safety
from dbaylo.wellness.rules import GOAL_RULES
from dbaylo.wellness.signals import TEXT_SIGNALS
from dbaylo.wellness.types import (
    Concern,
    GoalRule,
    GoalSpec,
    GuardrailOutcome,
    TextSignal,
)

DEFAULT_FLOOR: Concern = Concern.OK


def evaluate(
    goal: GoalSpec | None = None,
    text: str | None = None,
    *,
    goal_rules: tuple[GoalRule, ...] = GOAL_RULES,
    text_signals: tuple[TextSignal, ...] = TEXT_SIGNALS,
) -> GuardrailOutcome:
    """Evaluate a goal and/or free text and return a care-oriented verdict.

    ``goal`` and ``text`` are both optional; a ``goal`` also contributes its
    ``raw_text`` to the text scan. ``goal_rules`` / ``text_signals`` are injectable
    for testing and default to the active sets.
    """
    scan_text = " ".join(part for part in (text, goal.raw_text if goal else None) if part)

    matched_goal = tuple(rule for rule in goal_rules if goal is not None and rule.matches(goal))
    matched_text = tuple(sig for sig in text_signals if scan_text and sig.matches(scan_text))

    concerns = [rule.concern for rule in matched_goal] + [sig.concern for sig in matched_text]
    concern = max((*concerns, DEFAULT_FLOOR))

    matched_ids = tuple(rule.id for rule in matched_goal) + tuple(sig.id for sig in matched_text)
    message = _compose_message(concern, matched_goal, matched_text)

    return GuardrailOutcome(
        concern=concern,
        matched_ids=matched_ids,
        # Every emitted string is validated: no dose directive, no restrictive diet.
        message=safety.assert_safe_output(message),
        disclaimer=safety.DISCLAIMER,
    )


def _compose_message(
    concern: Concern,
    matched_goal: tuple[GoalRule, ...],
    matched_text: tuple[TextSignal, ...],
) -> str:
    """Surface the message of the decisive (max-concern) rule/signal.

    OK has no message (nothing to say). For REDIRECT/SUPPORT we take the first
    decisive rule's message — they are intentionally shared per concern level, so
    the verdict reads with one clear voice rather than a pile-up of advice.
    """
    if concern == Concern.OK:
        return ""
    # Text signals first, then goal rules — both expose (concern, message).
    candidates: list[tuple[Concern, str]] = [(s.concern, s.message) for s in matched_text]
    candidates += [(r.concern, r.message) for r in matched_goal]
    for item_concern, message in candidates:
        if item_concern == concern:
            return message
    return ""  # unreachable: a non-OK concern always has a decisive match
