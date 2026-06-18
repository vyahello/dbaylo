"""L3 — Triage (the deterministic red-flag safety core).

No LLM lives here. A pure, fully-tested rule engine maps a set of reported
symptoms to an escalation level, under one non-negotiable invariant:

    escalate UP only — the engine never concludes "you can skip the doctor."

The public surface is intentionally tiny.
"""

from dbaylo.triage.engine import evaluate
from dbaylo.triage.types import (
    Action,
    Symptom,
    SymptomReport,
    TriageOutcome,
    TriageRule,
)

__all__ = [
    "Action",
    "Symptom",
    "SymptomReport",
    "TriageOutcome",
    "TriageRule",
    "evaluate",
]
