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
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.db.models import Goal, GoalStatus, User
from dbaylo.safety import GateSource, screen
from dbaylo.wellness import Concern, GoalSpec

# Always-safe generic wellness goals — no numbers, so they never trip the dose/diet guard, and they
# encode "beauty via health / habits", never a crash protocol (rail #6). Offered alongside the
# data-derived ones so the screen is never empty for someone with clean labs.
GENERIC_GOALS: tuple[str, ...] = (
    "Налагодити режим сну",
    "Пити достатньо води щодня",
    "Додати більше руху щодня",
)

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
    """What ``set_goal`` returns: the message, whether it saved, and what decided it.

    ``concern`` is the wellness verdict (OK / REDIRECT / SUPPORT). It is ``None`` when
    a medical red flag short-circuited the goal — that path is a *triage* escalation,
    not a wellness concern, and ``source`` is :data:`GateSource.TRIAGE`.
    """

    message: str
    saved: bool
    source: GateSource
    concern: Concern | None


async def set_goal(session: AsyncSession, *, user: User, text: str) -> GoalResult:
    """Validate a goal through the safety gate; persist only when it clears.

    The gate runs the full canonical order, so a goal that names a red-flag symptom
    routes to triage (and is not stored), while an aggressive/disordered goal is
    redirected by the wellness guardrail (also not stored). Only a cleared goal
    persists.
    """
    spec = parse_goal(text)
    decision = screen(text, goal=spec)

    if decision.short_circuited:
        # Triage or guardrail short-circuit — do not store; surface the guidance.
        concern = decision.guardrail.concern if decision.guardrail is not None else None
        return GoalResult(
            message=decision.message, saved=False, source=decision.source, concern=concern
        )

    goal = Goal(user_id=user.id, type=spec.kind, target=text, status=GoalStatus.ACTIVE)
    session.add(goal)
    await session.flush()
    return GoalResult(
        message=locale.GOAL_ACCEPTED, saved=True, source=decision.source, concern=Concern.OK
    )


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _suggested_for(finding: object) -> str | None:
    """A neutral, data-framed goal for a currently out-of-range finding — "bring it back to range",
    no method implied, no dose/diet (rail #1/#6). The name is specimen-qualified (display_name) so a
    urine 'Еритроцити (сеча)' goal is never confused with the blood one. Watch/flag → Проблеми."""
    kind = getattr(finding, "kind", "")
    name = getattr(finding, "display_name", None) or getattr(finding, "name", "")
    if kind in ("high", "low") and name:
        return locale.GOAL_SUGGEST_NORMALIZE.format(name=name)
    return None


async def active_goal_texts(session: AsyncSession, *, user_id: int) -> list[str]:
    """The targets of the user's ACTIVE goals (shown in the screen's 'your goals' section)."""
    rows = await session.scalars(
        select(Goal.target).where(Goal.user_id == user_id, Goal.status == GoalStatus.ACTIVE)
    )
    return [t for t in rows.all() if t]


async def known_goal_texts(session: AsyncSession, *, user_id: int) -> list[str]:
    """The targets of EVERY goal the user has — any status. The suggester excludes these, so a goal
    you already adopted, achieved, or removed is never re-suggested at you."""
    rows = await session.scalars(select(Goal.target).where(Goal.user_id == user_id))
    return [t for t in rows.all() if t]


async def list_active_goals(session: AsyncSession, *, user_id: int) -> list[Goal]:
    """The user's ACTIVE goal rows (with ids), oldest first — for the manageable goals screen."""
    rows = await session.scalars(
        select(Goal)
        .where(Goal.user_id == user_id, Goal.status == GoalStatus.ACTIVE)
        .order_by(Goal.created_at)
    )
    return list(rows.all())


async def achieve_goal(session: AsyncSession, *, goal_id: int, user_id: int) -> Goal | None:
    """Mark a goal achieved (✅). Kept as a record; never re-suggested."""
    return await _set_status(session, goal_id=goal_id, user_id=user_id, status=GoalStatus.ACHIEVED)


async def remove_goal(session: AsyncSession, *, goal_id: int, user_id: int) -> Goal | None:
    """Drop a goal (🗑) — undo an accidental adopt, or one you no longer want. Marked ABANDONED (a
    record, so it isn't re-suggested), not deleted."""
    return await _set_status(session, goal_id=goal_id, user_id=user_id, status=GoalStatus.ABANDONED)


async def _set_status(
    session: AsyncSession, *, goal_id: int, user_id: int, status: GoalStatus
) -> Goal | None:
    goal = await session.get(Goal, goal_id)
    if goal is None or goal.user_id != user_id:
        return None
    goal.status = status
    await session.flush()
    return goal


async def propose_goals(session: AsyncSession, user_id: int, *, today: date) -> list[str]:
    """What the agent suggests as goals: a "bring it to range" goal per currently out-of-range
    finding, then generic wellness goals — EXCLUDING any goal the user already has (active,
    achieved, OR removed). Pure + deterministic (the guardrail still vets each on adopt)."""
    from dbaylo.companion import health  # lazy import: avoid a module-load cycle

    picture = await health.analyze_health(session, user_id, today=today)
    existing = [_norm(t) for t in await known_goal_texts(session, user_id=user_id)]
    candidates = [g for f in picture.current if (g := _suggested_for(f))]
    candidates.extend(GENERIC_GOALS)
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _norm(candidate)
        if key in seen or any(key in e or e in key for e in existing):
            continue
        seen.add(key)
        out.append(candidate)
    return out[:5]


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
