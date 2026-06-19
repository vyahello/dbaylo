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


async def resolve(session: AsyncSession, condition_id: int) -> Condition | None:
    condition = await session.get(Condition, condition_id)
    if condition is not None:
        condition.status = ConditionStatus.RESOLVED
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
