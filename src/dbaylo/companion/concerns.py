"""Active health concerns — the state that drives the conditional daily check-in.

A :class:`Condition` is ACTIVE or RESOLVED. The daily check-in is scheduled iff at
least one ACTIVE concern exists (never an unconditional daily ping). An active
concern is also offered for closure periodically (``due_for_review``) so it doesn't
ping forever on the user's memory.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.db.models import Condition, ConditionStatus, User

REVIEW_INTERVAL = timedelta(days=7)


def _naive(dt: datetime) -> datetime:
    """Drop tzinfo so a tz-aware ``now`` compares cleanly with SQLite's naive stamps."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


async def add_active(
    session: AsyncSession, *, user: User, name: str, report_id: int | None = None
) -> Condition:
    condition = Condition(
        user_id=user.id, name=name.strip(), status=ConditionStatus.ACTIVE, report_id=report_id
    )
    session.add(condition)
    await session.flush()
    return condition


async def dismiss(session: AsyncSession, *, user: User, name: str) -> Condition:
    """Record an AI-proposed finding the user declined to track ("Не турбує"). Stored as a DISMISSED
    Condition so it is neither re-proposed nor used to keep the data-driven check-in alive."""
    condition = Condition(user_id=user.id, name=name.strip(), status=ConditionStatus.DISMISSED)
    session.add(condition)
    await session.flush()
    return condition


async def names_dismissed(session: AsyncSession, *, user_id: int) -> list[str]:
    """Names of the user's DISMISSED findings (so a current flag they waved off stops nagging)."""
    rows = await session.scalars(
        select(Condition.name).where(
            Condition.user_id == user_id, Condition.status == ConditionStatus.DISMISSED
        )
    )
    return [n for n in rows.all() if n]


async def names_active_or_dismissed(session: AsyncSession, *, user_id: int) -> list[str]:
    """Names already tracked OR dismissed — the set an AI proposal must exclude (don't re-offer
    something the user already tracks or explicitly waved off)."""
    rows = await session.scalars(
        select(Condition.name).where(
            Condition.user_id == user_id,
            Condition.status.in_([ConditionStatus.ACTIVE, ConditionStatus.DISMISSED]),
        )
    )
    return [n for n in rows.all() if n]


async def list_dismissed(session: AsyncSession, *, user_id: int) -> list[Condition]:
    """The user's DISMISSED findings (so a wrongly-waved-off one can be restored)."""
    rows = await session.scalars(
        select(Condition)
        .where(Condition.user_id == user_id, Condition.status == ConditionStatus.DISMISSED)
        .order_by(Condition.created_at)
    )
    return list(rows.all())


async def undismiss(session: AsyncSession, *, user_id: int, condition_id: int) -> Condition | None:
    """Undo a dismissal ("повернути під нагляд"): drop the DISMISSED row so the finding is proposed
    again. Returns the removed row (for the toast), or ``None`` if not this user's dismissal."""
    condition = await session.get(Condition, condition_id)
    if (
        condition is None
        or condition.user_id != user_id
        or condition.status != ConditionStatus.DISMISSED
    ):
        return None
    await session.delete(condition)
    await session.flush()
    return condition


async def resolve(session: AsyncSession, condition_id: int) -> Condition | None:
    condition = await session.get(Condition, condition_id)
    if condition is not None:
        condition.status = ConditionStatus.RESOLVED
        await session.flush()
    return condition


async def list_resolved(session: AsyncSession, *, user_id: int) -> list[Condition]:
    """The user's RESOLVED concerns (the «✔️ Вирішені» archive) — most recently resolved first, so a
    closed concern can be reviewed or re-opened. Read-only."""
    rows = await session.scalars(
        select(Condition)
        .where(Condition.user_id == user_id, Condition.status == ConditionStatus.RESOLVED)
        .order_by(Condition.created_at.desc())
    )
    return list(rows.all())


async def reopen(session: AsyncSession, *, user_id: int, condition_id: int) -> Condition | None:
    """Re-open a RESOLVED concern ("знову під нагляд"): set it back to ACTIVE. Returns the row (for
    the toast + a check-in reconcile), or ``None`` if it is not this user's resolved concern."""
    condition = await session.get(Condition, condition_id)
    if (
        condition is None
        or condition.user_id != user_id
        or condition.status != ConditionStatus.RESOLVED
    ):
        return None
    condition.status = ConditionStatus.ACTIVE
    await session.flush()
    return condition


async def rename(session: AsyncSession, condition_id: int, new_name: str) -> Condition | None:
    condition = await session.get(Condition, condition_id)
    if condition is not None:
        condition.name = new_name.strip()
        await session.flush()
    return condition


async def list_active(session: AsyncSession, *, user_id: int) -> list[Condition]:
    rows = await session.scalars(
        select(Condition)
        .where(Condition.user_id == user_id, Condition.status == ConditionStatus.ACTIVE)
        .order_by(Condition.created_at)
    )
    return list(rows.all())


async def count_active(session: AsyncSession, *, user_id: int) -> int:
    total = await session.scalar(
        select(func.count())
        .select_from(Condition)
        .where(Condition.user_id == user_id, Condition.status == ConditionStatus.ACTIVE)
    )
    return int(total or 0)


async def due_for_review(
    session: AsyncSession, *, user_id: int, now: datetime, interval: timedelta = REVIEW_INTERVAL
) -> list[Condition]:
    """Active concerns last reviewed (or created) at least ``interval`` ago."""
    threshold = _naive(now) - interval
    due: list[Condition] = []
    for condition in await list_active(session, user_id=user_id):
        reference = condition.last_review_at or condition.created_at
        if reference is None or _naive(reference) <= threshold:
            due.append(condition)
    return due


async def mark_reviewed(session: AsyncSession, condition_id: int, now: datetime) -> None:
    condition = await session.get(Condition, condition_id)
    if condition is not None:
        condition.last_review_at = now
        await session.flush()
