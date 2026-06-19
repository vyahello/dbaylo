"""Active-concern state: add / resolve / rename / list / count / review timing."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import concerns
from dbaylo.db.models import ConditionStatus, User


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=7, name="Test")
    session.add(user)
    await session.flush()
    return user


async def test_add_active_and_list(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    condition = await concerns.add_active(async_session, user=user, name="високий тиск")
    assert condition.status == ConditionStatus.ACTIVE
    assert await concerns.count_active(async_session, user_id=user.id) == 1
    assert [c.name for c in await concerns.list_active(async_session, user_id=user.id)] == [
        "високий тиск"
    ]


async def test_resolve_drops_from_active(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    condition = await concerns.add_active(async_session, user=user, name="x")
    await concerns.resolve(async_session, condition.id)
    assert await concerns.count_active(async_session, user_id=user.id) == 0


async def test_rename(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    condition = await concerns.add_active(async_session, user=user, name="креатинін поза нормою")
    await concerns.rename(async_session, condition.id, "камені в нирках")
    actives = await concerns.list_active(async_session, user_id=user.id)
    assert actives[0].name == "камені в нирках"


async def test_due_for_review_respects_interval(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    condition = await concerns.add_active(async_session, user=user, name="x")
    t0 = datetime(2026, 1, 1)
    await concerns.mark_reviewed(async_session, condition.id, t0)

    # < 7 days since the last review -> not due.
    assert (
        await concerns.due_for_review(async_session, user_id=user.id, now=t0 + timedelta(days=3))
        == []
    )
    # >= 7 days -> due.
    due = await concerns.due_for_review(async_session, user_id=user.id, now=t0 + timedelta(days=8))
    assert [c.id for c in due] == [condition.id]
    # reviewing it again resets the clock.
    await concerns.mark_reviewed(async_session, condition.id, t0 + timedelta(days=8))
    assert (
        await concerns.due_for_review(async_session, user_id=user.id, now=t0 + timedelta(days=9))
        == []
    )
