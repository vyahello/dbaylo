"""The safety gate — the single sanctioned path from user text toward the LLM.

Pure orchestration over the two deterministic safety cores, encoding the one
canonical escalation order (escalate up only):

1. **symptoms -> triage** — a medical red flag outranks everything;
2. else the **wellness guardrail** — disordered-eating / unsafe goals;
3. else **cleared** — the caller may proceed to the LLM.

Every entry point (companion chat, free-text, check-in, goals) routes through
:func:`screen`; nothing re-implements the order inline (enforced by an import-graph
test). The gate calls no LLM, no DB, and adds no rules — it composes the existing
engines.

**Precedence:** when a text carries BOTH a symptom and a disordered-eating signal,
triage wins — the chain short-circuits on the first (most acute) match and never
reaches the guardrail. The composed message is run through ``assert_safe_output``;
since the engines already emit safety-checked text, that re-wrap is idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from dbaylo.companion.symptoms import detect_symptoms
from dbaylo.triage import evaluate as triage_evaluate
from dbaylo.triage.safety import assert_safe_output
from dbaylo.triage.types import SymptomReport, TriageOutcome
from dbaylo.wellness import Concern, GoalSpec, GuardrailOutcome
from dbaylo.wellness import evaluate as guardrail_evaluate


class GateSource(StrEnum):
    """Which layer decided the turn (a StrEnum so it compares equal to its value)."""

    TRIAGE = "triage"
    GUARDRAIL = "guardrail"
    CLEARED = "cleared"


@dataclass(frozen=True)
class GateDecision:
    """The gate's verdict for one piece of user text.

    ``cleared`` means the deterministic cores said nothing and the caller may go to
    the LLM. Otherwise ``message`` is the safety-checked, disclaimer-appended text
    to surface verbatim. ``triage`` / ``guardrail`` carry the underlying outcome of
    whichever leg ran (the guardrail also runs, and is attached, when cleared).
    """

    cleared: bool
    source: GateSource
    message: str
    triage: TriageOutcome | None = None
    guardrail: GuardrailOutcome | None = None

    @property
    def short_circuited(self) -> bool:
        return not self.cleared


def _triage(text: str) -> TriageOutcome | None:
    """Detect symptom tokens and, if any, run the deterministic triage engine."""
    symptoms = detect_symptoms(text)
    return triage_evaluate(SymptomReport(symptoms)) if symptoms else None


def screen(text: str, *, goal: GoalSpec | None = None) -> GateDecision:
    """Run the canonical safety order over ``text`` (and an optional structured goal).

    ``goal`` lets the goals flow pass a parsed :class:`GoalSpec` so the guardrail's
    structured rules (e.g. weight-loss rate) can fire; for all other callers it is
    ``None`` and only the text is screened.
    """
    outcome = _triage(text)
    if outcome is not None:
        message = assert_safe_output(f"{outcome.message}\n\n{outcome.disclaimer}")
        return GateDecision(
            cleared=False, source=GateSource.TRIAGE, message=message, triage=outcome
        )

    verdict = guardrail_evaluate(goal=goal, text=text)
    if verdict.concern != Concern.OK:
        message = assert_safe_output(f"{verdict.message}\n\n{verdict.disclaimer}")
        return GateDecision(
            cleared=False, source=GateSource.GUARDRAIL, message=message, guardrail=verdict
        )

    return GateDecision(cleared=True, source=GateSource.CLEARED, message="", guardrail=verdict)
