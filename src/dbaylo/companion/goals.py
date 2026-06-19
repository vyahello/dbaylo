"""Goals: parse a free-text wellness goal, guardrail it, then persist (or redirect).

The flow honours the L1 safety rail: a goal is validated through
``wellness.guardrail.evaluate`` **before** it is accepted. Only an ``OK`` verdict
writes a :class:`Goal` row; a REDIRECT/SUPPORT verdict is returned to the caller
(its Ukrainian message guides the user) and nothing is stored.

The weight-loss parser is deliberately lenient and deterministic — it extracts
what it can ("на 8 кг за 4 тижні", "з 90 до 80 кг за місяць") and leaves the rest
``None`` (the numeric rule simply will not fire).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.db.models import Goal, GoalStatus, User
from dbaylo.wellness import Concern, GoalSpec, GuardrailOutcome
from dbaylo.wellness import evaluate as guardrail_evaluate

_WEIGHT_LOSS_HINTS = (
    "схуд",
    "скинути",
    "скинь",
    "зігнати",
    "знизити вагу",
    "втратити",
    "жир",
    "кг",
    "вага",
    "важу",
)
_NUM = r"(\d+(?:[.,]\d+)?)"


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "."))


def _weeks_from_text(text: str) -> float | None:
    """Parse a duration ("за 4 тижні", "за місяць", "за 30 днів") into weeks."""
    if m := re.search(rf"за\s+{_NUM}\s*(?:тижн|нед)", text):
        return _to_float(m.group(1))
    if m := re.search(rf"за\s+{_NUM}\s*(?:місяц|міс)", text):
        return _to_float(m.group(1)) * 4.345
    if m := re.search(rf"за\s+{_NUM}\s*(?:дн|днів|день)", text):
        return _to_float(m.group(1)) / 7.0
    if re.search(r"за\s+місяць", text):
        return 4.345
    if re.search(r"за\s+тиждень", text):
        return 1.0
    return None


def parse_goal(text: str) -> GoalSpec:
    """Build a :class:`GoalSpec` from free Ukrainian text (best-effort, pure)."""
    lowered = text.casefold()
    if not any(hint in lowered for hint in _WEIGHT_LOSS_HINTS):
        return GoalSpec(raw_text=text)

    weeks = _weeks_from_text(lowered)
    current_kg = target_kg = loss_kg = None

    if m := re.search(rf"з\s+{_NUM}\s*(?:кг)?\s+до\s+{_NUM}\s*кг", lowered):
        current_kg, target_kg = _to_float(m.group(1)), _to_float(m.group(2))
    elif m := re.search(rf"(?:на|скинути|втратити)\s+{_NUM}\s*кг", lowered):
        loss_kg = _to_float(m.group(1))

    return GoalSpec(
        raw_text=text,
        kind="weight_loss",
        current_kg=current_kg,
        target_kg=target_kg,
        loss_kg=loss_kg,
        weeks=weeks,
    )


@dataclass(frozen=True)
class GoalResult:
    """What ``set_goal`` returns: the user-facing message and whether it was saved."""

    message: str
    saved: bool
    concern: Concern


async def set_goal(session: AsyncSession, *, user: User, text: str) -> GoalResult:
    """Validate a goal through the guardrail; persist only on an OK verdict."""
    spec = parse_goal(text)
    outcome: GuardrailOutcome = guardrail_evaluate(goal=spec, text=text)

    if not outcome.accepted:
        # Redirect / support — do not store; the guardrail message guides the user.
        return GoalResult(
            message=f"{outcome.message}\n\n{outcome.disclaimer}",
            saved=False,
            concern=outcome.concern,
        )

    goal = Goal(user_id=user.id, type=spec.kind, target=text, status=GoalStatus.ACTIVE)
    session.add(goal)
    await session.flush()
    return GoalResult(message=locale.GOAL_ACCEPTED, saved=True, concern=Concern.OK)


async def list_goals(session: AsyncSession, *, user: User) -> str:
    """Render the user's active/known goals as Ukrainian text."""
    goals = (
        await session.scalars(select(Goal).where(Goal.user_id == user.id).order_by(Goal.created_at))
    ).all()
    if not goals:
        return locale.GOAL_LIST_EMPTY

    lines = [locale.GOAL_LIST_HEADER]
    for goal in goals:
        status = locale.GOAL_STATUS_LABELS.get(goal.status.value, goal.status.value)
        lines.append(f"• {goal.target} ({status})")
    return "\n".join(lines)
