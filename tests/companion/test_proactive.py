"""The conditional-check-in invariant and reminder scheduling, against a live scheduler.

A check-in job exists iff at least one active concern exists; adding the first
concern schedules it and resolving the last removes it. Medication adds N jobs and
turning it off removes them all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import proactive
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.db.models import User

TZ = ZoneInfo("Europe/Kyiv")


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=500, name="Test")
    session.add(user)
    await session.flush()
    return user


async def _sender(telegram_id: int, text: str, *, buttons: object | None = None) -> None:
    return None


@pytest_asyncio.fixture
async def scheduler(async_session: AsyncSession) -> AsyncIterator[ReminderScheduler]:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield async_session

    rs = ReminderScheduler(sender=_sender, session_factory=factory, tz=TZ)
    await rs.start()  # empty DB -> starts with no jobs
    yield rs
    rs.shutdown()


def _count(scheduler: ReminderScheduler, type_: str) -> int:
    return sum(job.type == type_ for job in scheduler.list_jobs())


async def test_no_concern_means_no_checkin_job(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    await _user(async_session)
    assert _count(scheduler, "checkin") == 0


async def test_first_problem_schedules_checkin_then_last_resolve_removes_it(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    user = await _user(async_session)
    a = await proactive.add_problem(async_session, user=user, name="тиск", scheduler=scheduler)
    b = await proactive.add_problem(async_session, user=user, name="сон", scheduler=scheduler)
    await async_session.commit()
    assert _count(scheduler, "checkin") == 1  # exactly one, for two concerns

    await proactive.resolve_problem(
        async_session, user_id=user.id, condition_id=a.id, scheduler=scheduler
    )
    await async_session.commit()
    assert _count(scheduler, "checkin") == 1  # one concern still active -> stays

    await proactive.resolve_problem(
        async_session, user_id=user.id, condition_id=b.id, scheduler=scheduler
    )
    await async_session.commit()
    assert _count(scheduler, "checkin") == 0  # none left -> removed


async def test_medication_schedules_all_jobs_and_turn_off_removes_all(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    user = await _user(async_session)
    med, _created = await proactive.add_medication(
        async_session,
        user=user,
        name="Аспірин",
        times=[time(8, 0), time(20, 0)],
        scheduler=scheduler,
    )
    await async_session.commit()
    assert _count(scheduler, "medication") == 2

    await proactive.turn_off_medication(async_session, medication_id=med.id, scheduler=scheduler)
    await async_session.commit()
    assert _count(scheduler, "medication") == 0  # no orphaned jobs


async def test_repeat_lab_is_scheduled(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    user = await _user(async_session)
    await proactive.add_repeat_lab(
        async_session,
        user=user,
        run_at=datetime(2027, 1, 1, 9, 0),
        label="ТТГ",
        scheduler=scheduler,
    )
    await async_session.commit()
    assert _count(scheduler, "repeat_lab") == 1
