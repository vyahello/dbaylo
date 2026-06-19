"""Core wellness-guardrail types: the L1 safety vocabulary and data carriers.

Structurally a sibling of :mod:`dbaylo.triage.types`. The guardrail is a pure,
deterministic evaluator (no LLM, no DB, no network) that decides whether a goal
or a piece of user text should be accepted, redirected toward something
sustainable, or escalated toward support.

Two input families feed it:

* a structured :class:`GoalSpec` (numeric threshold rules — e.g. weight-loss rate)
* free Ukrainian text (keyword signals — disordered-eating patterns)

Both resolve to the same ordered :class:`Concern`, and the engine takes the
``max`` over everything matched (floored at ``OK``) — "escalate up only", exactly
like triage.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum


class Concern(IntEnum):
    """How strongly the guardrail wants to intervene.

    The ordering *is* the mechanism: the outcome is ``max`` over matched rules,
    floored at :data:`Concern.OK`. A larger value means "intervene more". Unlike
    triage's floor (``MONITOR``, never "you're fine"), the wellness floor is
    ``OK`` — most goals are healthy and are simply accepted.
    """

    OK = 0
    """No concern — accept the goal / nothing flagged in the text."""

    REDIRECT = 1
    """Aggressive or unrealistic target — propose a sustainable version, don't comply."""

    SUPPORT = 2
    """Disordered-pattern signal — sustainable framing + suggest professional help."""


@dataclass(frozen=True)
class GoalSpec:
    """A wellness goal, possibly with parsed numeric parameters.

    ``raw_text`` is always the user's original Ukrainian wording (also scanned for
    text signals). The numeric fields are filled opportunistically by the parser
    in :mod:`dbaylo.companion.goals`; when ``None`` the numeric rules simply do
    not fire.
    """

    raw_text: str
    kind: str = "general"  # "weight_loss", "sleep", "hydration", "strength", ...
    current_kg: float | None = None
    target_kg: float | None = None
    loss_kg: float | None = None  # direct "lose N kg" when current/target unknown
    weeks: float | None = None

    def total_loss_kg(self) -> float | None:
        """Intended total loss in kg, from a direct figure or current−target."""
        if self.loss_kg is not None:
            return self.loss_kg if self.loss_kg > 0 else None
        if self.current_kg is not None and self.target_kg is not None:
            delta = self.current_kg - self.target_kg
            return delta if delta > 0 else None
        return None

    def weekly_loss_kg(self) -> float | None:
        """Projected kg lost per week, if enough is known; else ``None``."""
        total = self.total_loss_kg()
        if total is None or not self.weeks or self.weeks <= 0:
            return None
        return total / self.weeks


@dataclass(frozen=True)
class GoalRule:
    """A numeric threshold rule over a :class:`GoalSpec` (analogous to TriageRule).

    ``predicate`` returns True when the rule fires for a given spec. Atomic and
    independently testable; the message is care-oriented Ukrainian from locale.
    """

    id: str
    concern: Concern
    message: str
    rationale: str
    predicate: Callable[[GoalSpec], bool]

    def matches(self, goal: GoalSpec) -> bool:
        return self.predicate(goal)


@dataclass(frozen=True)
class TextSignal:
    """A disordered-pattern signal: keyword phrases mapped to a :class:`Concern`."""

    id: str
    concern: Concern
    message: str
    keywords: tuple[str, ...]

    def matches(self, text: str) -> bool:
        lowered = text.casefold()
        return any(keyword.casefold() in lowered for keyword in self.keywords)


@dataclass(frozen=True)
class GuardrailOutcome:
    """The guardrail's verdict — a sibling of :class:`triage.TriageOutcome`.

    ``message`` is always safety-checked Ukrainian; the disclaimer ("not a doctor")
    is always attached. No field ever carries a restrictive numeric prescription.
    """

    concern: Concern
    matched_ids: tuple[str, ...] = field(default_factory=tuple)
    message: str = ""
    disclaimer: str = ""

    @property
    def accepted(self) -> bool:
        """True iff the goal/text is fine to accept as-is (no intervention)."""
        return self.concern == Concern.OK
