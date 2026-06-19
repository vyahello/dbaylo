"""Disordered-pattern text signals for the wellness guardrail.

Each signal is a set of Ukrainian keyword phrases (kept in ``locale`` so the guard
and tests read from one source) mapped to a :class:`Concern`. Detection is pure
substring matching — no LLM. The keyword map is deliberately limited and is
documented (in ``locale.WELLNESS_SIGNAL_KEYWORDS``) as extensible, like
``labs.trends.ANALYTE_ALIASES``.

The purging keywords are disjoint from the vomiting symptom keywords in
``locale.SYMPTOM_KEYWORDS``: self-induced purging vs. involuntary vomiting. Triage
runs *before* the guardrail, so an overlap would let a generic symptom mask a
purging signal — the two maps are kept separate by construction.
"""

from __future__ import annotations

from dbaylo import locale
from dbaylo.wellness.types import Concern, TextSignal

# Signal id -> the concern it raises. SUPPORT (disordered-eating) outranks REDIRECT.
_SIGNAL_CONCERNS: dict[str, Concern] = {
    "extreme_restriction": Concern.SUPPORT,
    "skipped_meals": Concern.SUPPORT,
    "purging": Concern.SUPPORT,
    "compulsive_exercise": Concern.SUPPORT,
    "crash_diet_language": Concern.REDIRECT,
}

# SUPPORT signals share one supportive message; REDIRECT signals reuse the
# sustainable-goal redirect. Both are safety-checked at guardrail-construction.
_SIGNAL_MESSAGES: dict[Concern, str] = {
    Concern.SUPPORT: locale.GUARDRAIL_SUPPORT,
    Concern.REDIRECT: locale.GOAL_REDIRECT_AGGRESSIVE,
}

TEXT_SIGNALS: tuple[TextSignal, ...] = tuple(
    TextSignal(
        id=signal_id,
        concern=_SIGNAL_CONCERNS[signal_id],
        message=_SIGNAL_MESSAGES[_SIGNAL_CONCERNS[signal_id]],
        keywords=keywords,
    )
    for signal_id, keywords in locale.WELLNESS_SIGNAL_KEYWORDS.items()
)
