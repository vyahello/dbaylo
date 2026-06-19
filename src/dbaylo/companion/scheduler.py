"""APScheduler wiring — reminders rebuilt from the DB on every startup.

Reminder rows are the **source of truth**. :func:`build_scheduler` loads every
active reminder and adds one job per row, so a restart reconstructs the exact same
schedule (jobs are not persisted in APScheduler's own store — the DB is). ``next_run``
is read from the built scheduler's triggers, never stored (a DB copy would go stale).

Job defaults use ``coalesce=True`` and a ``misfire_grace_time`` so a reminder whose
moment passed while the process was down fires once on startup rather than piling up;
a fired one-off (``date:``) reminder is then soft-deleted.

``python -m dbaylo.companion.scheduler --dry-run`` lists the jobs (id, type, trigger,
next run) **without starting the scheduler or firing anything**.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.companion import checkin, reminders
from dbaylo.companion.reminders import CronSpec, DateSpec, parse_schedule
from dbaylo.config import get_settings
from dbaylo.db import get_session
from dbaylo.db.models import Reminder, User

# A factory that yields an AsyncSession context manager (``get_session`` by default).
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
# Delivers a rendered message to a Telegram user id.
Sender = Callable[[int, str], Awaitable[None]]

# How long after the check-in prompt to send the single (no-nag) follow-up.
NUDGE_DELAY = timedelta(minutes=90)
MISFIRE_GRACE_S = 3600


def make_trigger(schedule: str, *, tz: ZoneInfo) -> CronTrigger | DateTrigger:
    """Turn a tagged ``schedule`` string into an APScheduler trigger."""
    spec = parse_schedule(schedule)
    if isinstance(spec, CronSpec):
        return CronTrigger(
            minute=spec.minute,
            hour=spec.hour,
            day=spec.day,
            month=spec.month,
            day_of_week=spec.day_of_week,
            timezone=tz,
        )
    assert isinstance(spec, DateSpec)
    return DateTrigger(run_date=spec.run_at, timezone=tz)


async def _send(sender: Sender, session: AsyncSession, user_id: int, text: str) -> None:
    user = await session.get(User, user_id)
    if user is not None and user.telegram_id is not None:
        await sender(user.telegram_id, text)


async def _fire_reminder(
    reminder_id: int,
    *,
    session_factory: SessionFactory,
    sender: Sender,
    scheduler: AsyncIOScheduler,
    tz: ZoneInfo,
) -> None:
    """Run when a reminder's trigger fires: render, send, then housekeep."""
    async with session_factory() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None or not reminder.active:
            return
        await _send(sender, session, reminder.user_id, reminders.render_reminder(reminder))

        if reminder.type == reminders.TYPE_CHECKIN:
            # Schedule exactly one gentle follow-up; it self-checks for a reply.
            scheduler.add_job(
                _fire_nudge,
                trigger=DateTrigger(run_date=datetime.now(tz) + NUDGE_DELAY, timezone=tz),
                kwargs={
                    "user_id": reminder.user_id,
                    "session_factory": session_factory,
                    "sender": sender,
                    "tz": tz,
                },
            )
        elif reminder.type == reminders.TYPE_REPEAT_LAB:
            # One-off: retire it without losing the record.
            await reminders.deactivate(session, reminder)
        await session.commit()


async def _fire_nudge(
    *,
    user_id: int,
    session_factory: SessionFactory,
    sender: Sender,
    tz: ZoneInfo,
) -> None:
    """The single check-in follow-up: send one gentle nudge iff no check-in today."""
    async with session_factory() as session:
        if await checkin.should_send_nudge(session, user_id=user_id, day=datetime.now(tz).date()):
            await _send(sender, session, user_id, locale.CHECKIN_NUDGE)


async def build_scheduler(
    *,
    session_factory: SessionFactory = get_session,
    sender: Sender,
    tz: ZoneInfo | None = None,
) -> AsyncIOScheduler:
    """Build a scheduler with one job per active reminder (not started)."""
    tz = tz or ZoneInfo(get_settings().timezone)
    scheduler = AsyncIOScheduler(
        timezone=tz, job_defaults={"coalesce": True, "misfire_grace_time": MISFIRE_GRACE_S}
    )
    async with session_factory() as session:
        rows = await reminders.active_reminders(session)
    for reminder in rows:
        scheduler.add_job(
            _fire_reminder,
            trigger=make_trigger(reminder.schedule, tz=tz),
            id=f"reminder:{reminder.id}",
            name=reminder.type,
            kwargs={
                "reminder_id": reminder.id,
                "session_factory": session_factory,
                "sender": sender,
                "scheduler": scheduler,
                "tz": tz,
            },
        )
    return scheduler


@dataclass(frozen=True)
class JobInfo:
    """A flattened view of one scheduled job for display."""

    id: str
    type: str
    trigger: str
    next_run: datetime | None


def describe_jobs(scheduler: AsyncIOScheduler, *, now: datetime | None = None) -> list[JobInfo]:
    """List the scheduler's jobs with their next run time (read from the triggers)."""
    tz = scheduler.timezone
    now = now or datetime.now(tz)
    infos: list[JobInfo] = []
    for job in scheduler.get_jobs():
        next_run = job.trigger.get_next_fire_time(None, now)
        infos.append(JobInfo(id=job.id, type=job.name, trigger=str(job.trigger), next_run=next_run))
    return infos


async def _noop_sender(telegram_id: int, text: str) -> None:  # pragma: no cover - dry-run stub
    return None


async def _dry_run() -> int:
    scheduler = await build_scheduler(sender=_noop_sender)
    jobs = describe_jobs(scheduler)
    if not jobs:
        print("No active reminders.")
        return 0
    print(f"{len(jobs)} scheduled job(s):")
    for job in jobs:
        when = job.next_run.isoformat() if job.next_run else "—"
        print(f"  [{job.type}] {job.id}: {job.trigger} -> next {when}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dbaylo.companion.scheduler")
    parser.add_argument(
        "--dry-run", action="store_true", help="list scheduled jobs; start nothing, fire nothing"
    )
    args = parser.parse_args(argv)
    if not args.dry_run:
        parser.error("only --dry-run is supported from the CLI")
    return asyncio.run(_dry_run())


if __name__ == "__main__":
    sys.exit(main())
