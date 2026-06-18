"""Core triage types: the controlled vocabulary and the data carriers.

The engine is a pure set-matcher over a *controlled vocabulary* of symptoms.
Free-text -> symptom mapping is an upstream concern (a later stage); nothing in
this package parses natural language or calls an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum


class Action(IntEnum):
    """An escalation level.

    The ordering *is* the safety mechanism: "escalate up only" is implemented as
    ``max(...)`` over matched rules, floored at :data:`Action.MONITOR`. A larger
    value always means "more strongly toward care". There is deliberately no
    level below ``MONITOR`` — no "you're fine, skip the doctor" outcome exists.
    """

    MONITOR = 0
    """Keep tracking; seek care if it persists or worsens. Never "you're fine"."""

    SEE_DOCTOR = 1
    """Book a (non-urgent) doctor visit."""

    URGENT_CARE = 2
    """Seek care promptly — urgent care today."""

    EMERGENCY = 3
    """Emergency services now (ambulance / emergency department)."""


class Symptom(StrEnum):
    """Controlled vocabulary of reportable symptoms.

    Qualifiers (e.g. "first time") are modelled as their own tokens so the
    engine stays a pure membership matcher. Whether a finding is "first time" is
    decided upstream, not here.
    """

    FEVER = "fever"
    CHILLS = "chills"
    FLANK_PAIN = "flank_pain"
    SEVERE_PAIN = "severe_pain"
    INABILITY_TO_URINATE = "inability_to_urinate"
    UNCONTROLLED_VOMITING = "uncontrolled_vomiting"
    BLOOD_IN_URINE = "blood_in_urine"
    BLOOD_IN_URINE_FIRST_TIME = "blood_in_urine_first_time"


@dataclass(frozen=True)
class TriageRule:
    """A single, atomic red-flag rule.

    A rule fires iff every symptom in :attr:`triggers` is present in the report
    (logical AND within a rule). For OR semantics, write multiple rules — this
    keeps each rule independently reviewable and testable.
    """

    id: str
    condition: str
    triggers: frozenset[Symptom]
    action: Action
    message: str
    rationale: str
    source: str | None = None

    def matches(self, report: SymptomReport) -> bool:
        """True iff all of this rule's triggers are present in ``report``."""
        return self.triggers <= report.symptoms


@dataclass(frozen=True)
class SymptomReport:
    """The structured input to the engine: a set of reported symptoms."""

    symptoms: frozenset[Symptom] = field(default_factory=frozenset)

    @classmethod
    def of(cls, *symptoms: Symptom) -> SymptomReport:
        """Convenience constructor: ``SymptomReport.of(Symptom.FEVER, ...)``."""
        return cls(frozenset(symptoms))


@dataclass(frozen=True)
class TriageOutcome:
    """The engine's verdict.

    ``message`` and ``disclaimer`` are always care-oriented; the disclaimer
    ("not a doctor") is always attached. No field ever carries a dose directive
    or a "skip the doctor" conclusion.
    """

    action: Action
    matched_rule_ids: tuple[str, ...]
    message: str
    disclaimer: str
