"""L1 wellness guardrail — the deterministic disordered-eating / unsafe-goal core.

A sibling of :mod:`dbaylo.triage`: pure, deterministic, no LLM/DB/network. The
single entry point is :func:`evaluate`. Together with triage, these two cores own
*all* escalation in the product; the companion LLM never makes that call.
"""

from dbaylo.wellness.guardrail import evaluate
from dbaylo.wellness.types import Concern, GoalSpec, GuardrailOutcome

__all__ = ["Concern", "GoalSpec", "GuardrailOutcome", "evaluate"]
