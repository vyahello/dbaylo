"""Seeded wellness-guardrail rules: numeric goal-parameter thresholds.

Stage 3 seeds the weight-loss-rate rule. The threshold is **general, conservative
guidance, not clinical authority**: public-health bodies commonly describe roughly
0.5–1.0 kg/week as a typical *sustainable* pace, so a projected rate above
1.0 kg/week is treated as aggressive and REDIRECTed toward something gentler. The
redirect message (in ``locale``) deliberately frames this as "more sustainable",
never as a medical limit.

Extensible: BMI/underweight-target checks would need a stored height (we do not
keep one yet) — a documented future extension.
"""

from __future__ import annotations

from dbaylo import locale
from dbaylo.wellness.types import Concern, GoalRule, GoalSpec

# General, non-clinical sustainable-pace threshold (kg per week). See module docs.
MAX_SUSTAINABLE_WEEKLY_LOSS_KG = 1.0


def _weight_loss_too_fast(goal: GoalSpec) -> bool:
    rate = goal.weekly_loss_kg()
    return rate is not None and rate > MAX_SUSTAINABLE_WEEKLY_LOSS_KG


GOAL_RULES: tuple[GoalRule, ...] = (
    GoalRule(
        id="weight_loss_rate_too_fast",
        concern=Concern.REDIRECT,
        message=locale.GOAL_REDIRECT_AGGRESSIVE,
        rationale=(
            "A projected loss above ~1 kg/week is faster than the commonly cited "
            "sustainable pace; redirect toward a gentler target (general guidance, "
            "not a clinical limit)."
        ),
        predicate=_weight_loss_too_fast,
    ),
)
