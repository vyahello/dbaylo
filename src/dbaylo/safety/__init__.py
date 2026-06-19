"""The safety gate facade — the one canonical user-text -> escalation order.

:func:`screen` is the single sanctioned path from user text toward the LLM; it
composes the triage and wellness cores (no LLM, no DB, no new rules). Distinct from
:mod:`dbaylo.triage.safety`, which holds the guard *primitives* the gate builds on.
"""

from dbaylo.safety.gate import GateDecision, GateSource, screen

__all__ = ["GateDecision", "GateSource", "screen"]
