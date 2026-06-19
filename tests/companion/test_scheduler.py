"""Scheduler: DB-as-source-of-truth, startup rebuild, dry-run listing, firing."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import reminders, scheduler
from dbaylo.companion.checkin import process_checkin
from dbaylo.companion.reminders import daily_cron, once
from dbaylo.db.models import User

TZ = ZoneInfo("Europe/Kyiv")


def _factory(session: AsyncSession) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    return factory


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def __call__(self, telegram_id: int, text: str) -> None:
        self.sent.append((telegram_id, text))


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=500, name="Test")
    session.add(user)
    await session.flush()
    return user


def test_make_trigger_cron_and_date() -> None:
    assert isinstance(scheduler.make_trigger("cron:0 21 * * *", tz=TZ), CronTrigger)
    assert isinstance(scheduler.make_trigger("date:2026-09-01T09:00:00", tz=TZ), DateTrigger)


async def test_build_scheduler_one_job_per_active_reminder(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    await reminders.ensure_checkin_reminder(async_session, user=user)
    await reminders.create_reminder(
        async_session,
        user=user,
        type=reminders.TYPE_MEDICATION,
        schedule=daily_cron(9),
        payload="Аспірин",
    )
    sched = await scheduler.build_scheduler(
        session_factory=_factory(async_session), sender=_Recorder(), tz=TZ
    )
    jobs = scheduler.describe_jobs(sched)
    assert len(jobs) == 2
    # next_run is read from the built scheduler's triggers, not the DB, and is set.
    assert all(job.next_run is not None for job in jobs)
    assert {job.type for job in jobs} == {reminders.TYPE_CHECKIN, reminders.TYPE_MEDICATION}


async def test_rebuild_is_idempotent_across_restart(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    await reminders.ensure_checkin_reminder(async_session, user=user)
    first = await scheduler.build_scheduler(
        session_factory=_factory(async_session), sender=_Recorder(), tz=TZ
    )
    second = await scheduler.build_scheduler(
        session_factory=_factory(async_session), sender=_Recorder(), tz=TZ
    )
    ids_first = {j.id for j in scheduler.describe_jobs(first)}
    ids_second = {j.id for j in scheduler.describe_jobs(second)}
    assert ids_first == ids_second  # rows are the source of truth -> stable job ids


async def test_describe_jobs_does_not_fire(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    await reminders.ensure_checkin_reminder(async_session, user=user)
    recorder = _Recorder()
    sched = await scheduler.build_scheduler(
        session_factory=_factory(async_session), sender=recorder, tz=TZ
    )
    scheduler.describe_jobs(sched)
    assert recorder.sent == []  # listing must not send anything


async def test_fire_checkin_sends_prompt_and_schedules_one_nudge(
    async_session: AsyncSession,
) -> None:
    user = await _user(async_session)
    rem = await reminders.ensure_checkin_reminder(async_session, user=user)
    recorder = _Recorder()
    sched = AsyncIOScheduler(timezone=TZ)
    await scheduler._fire_reminder(
        rem.id,
        session_factory=_factory(async_session),
        sender=recorder,
        scheduler=sched,
        tz=TZ,
    )
    assert len(recorder.sent) == 1
    assert recorder.sent[0][0] == user.telegram_id
    # Exactly one follow-up nudge job was scheduled.
    assert len(sched.get_jobs()) == 1


async def test_fire_repeat_lab_deactivates_one_off(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    rem = await reminders.create_reminder(
        async_session,
        user=user,
        type=reminders.TYPE_REPEAT_LAB,
        schedule=once(datetime(2026, 9, 1, 9, 0)),
        payload="ТТГ",
    )
    sched = AsyncIOScheduler(timezone=TZ)
    await scheduler._fire_reminder(
        rem.id,
        session_factory=_factory(async_session),
        sender=_Recorder(),
        scheduler=sched,
        tz=TZ,
    )
    assert await reminders.active_reminders(async_session) == []


async def test_nudge_only_when_no_checkin(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    recorder = _Recorder()
    factory = _factory(async_session)
    # No check-in yet -> the nudge fires.
    await scheduler._fire_nudge(user_id=user.id, session_factory=factory, sender=recorder, tz=TZ)
    assert len(recorder.sent) == 1

    # After a check-in, a later nudge is suppressed.
    await process_checkin(
        async_session, user=user, text="спав 7 годин", check_date=datetime.now(TZ).date()
    )
    recorder.sent.clear()
    await scheduler._fire_nudge(user_id=user.id, session_factory=factory, sender=recorder, tz=TZ)
    assert recorder.sent == []
