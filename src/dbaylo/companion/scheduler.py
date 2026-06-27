"""APScheduler wiring — reminders rebuilt from the DB on startup, plus live add/remove.

Reminder rows are the **source of truth**. :class:`ReminderScheduler` loads every
active reminder on ``start`` (one job per row) and also lets handlers ``schedule`` a
newly-created reminder or ``unschedule`` one **without a restart** — the running
process's jobs are kept in sync with the DB as the user adds/removes things.

``next_run`` is read from the triggers, never stored. The job store is in-memory, but
reminders are **durable across a restart**: each fire records ``Reminder.last_fired_at``,
and on ``start`` a **catch-up** pass delivers any occurrence that came due since that
anchor while the process was down (bounded by :data:`MAX_CATCHUP`, coalesced to one
delivery per reminder) — so nothing is silently lost. An overdue one-off is delivered
then retired; a future one is just scheduled.

``python -m dbaylo.companion.scheduler --dry-run`` lists the jobs without firing.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.companion import checkin, health, reminders
from dbaylo.companion.reminders import CronSpec, DateSpec, parse_schedule
from dbaylo.config import get_settings
from dbaylo.db import get_session
from dbaylo.db.models import Reminder, User

# A factory that yields an AsyncSession context manager (``get_session`` by default).
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
# Inline buttons as (label, callback_data) pairs — built into a keyboard by the bot.
Buttons = Sequence[tuple[str, str]]


class Sender(Protocol):
    """Delivers a message (and optional inline buttons) to a Telegram user id."""

    async def __call__(
        self, telegram_id: int, text: str, *, buttons: Buttons | None = None
    ) -> None: ...


class DialogReset(Protocol):
    """Clears any in-progress FSM dialog for a Telegram user (the safety belt below). Provided by
    the bot layer (it owns the FSM storage); ``None`` in dry-run / tests, where it is a no-op."""

    async def __call__(self, telegram_id: int) -> None: ...


# How long after the check-in prompt to send the single (no-nag) follow-up.
NUDGE_DELAY = timedelta(minutes=90)
MISFIRE_GRACE_S = 3600
# On startup, deliver a missed occurrence only if it came due within this window — so a
# long outage never replays a huge backlog, and very stale reminders are not resurrected.
MAX_CATCHUP = timedelta(hours=12)


def _aware(dt: datetime, tz: ZoneInfo) -> datetime:
    """Coerce a (possibly naive, SQLite-stored) datetime to ``tz``."""
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def last_due_occurrence(
    trigger: CronTrigger | DateTrigger, *, floor: datetime, now: datetime
) -> datetime | None:
    """The most recent time the trigger should have fired in ``(floor, now]``, else None.

    Pure: drives the startup catch-up. For a one-off it is the run time if overdue; for a
    cron it is the latest occurrence at/under ``now`` and strictly after ``floor`` (the
    last-fired anchor), so a quick restart never re-fires the occurrence just delivered.
    """
    if isinstance(trigger, DateTrigger):
        run = trigger.run_date
        return run if floor < run <= now else None
    last: datetime | None = None
    candidate = trigger.get_next_fire_time(floor, floor)
    while candidate is not None and candidate <= now:
        last = candidate
        candidate = trigger.get_next_fire_time(candidate, candidate)
    return last


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


async def _send(
    sender: Sender,
    session: AsyncSession,
    user_id: int,
    text: str,
    *,
    buttons: Buttons | None = None,
) -> None:
    user = await session.get(User, user_id)
    if user is not None and user.telegram_id is not None:
        await sender(user.telegram_id, text, buttons=buttons)


async def _fire_reminder(
    reminder_id: int,
    *,
    session_factory: SessionFactory,
    sender: Sender,
    scheduler: AsyncIOScheduler,
    tz: ZoneInfo,
    dialog_reset: DialogReset | None = None,
) -> None:
    """Run when a reminder's trigger fires: render, send, then housekeep."""
    async with session_factory() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None or not reminder.active:
            return

        # Record the fire so the startup catch-up never re-delivers this occurrence
        # after a quick restart.
        reminder.last_fired_at = datetime.now(tz)

        if reminder.type == reminders.TYPE_CHECKIN:
            # Safety belt: a check-in invites a FREE-FORM reply, but the user may be parked in an
            # unrelated FSM dialog (e.g. a half-open add-medication). Clear it FIRST so the reply
            # reaches the gate/companion — a symptom routes to triage, never gets eaten as a dialog
            # answer (the "symptom stored as a drug name" bug). Best-effort: a reset failure must
            # never block the prompt from going out.
            if dialog_reset is not None:
                user = await session.get(User, reminder.user_id)
                if user is not None and user.telegram_id is not None:
                    with contextlib.suppress(Exception):
                        await dialog_reset(user.telegram_id)
            # The prompt + a "still relevant?" review for each due active concern.
            for text, buttons in await checkin.checkin_messages(
                session, user_id=reminder.user_id, now=datetime.now(tz)
            ):
                await _send(sender, session, reminder.user_id, text, buttons=buttons)
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
            await _send(sender, session, reminder.user_id, reminders.render_reminder(reminder))
            await reminders.deactivate(session, reminder)  # one-off: retire it
        else:  # medication
            await _send(sender, session, reminder.user_id, reminders.render_reminder(reminder))
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


def _add_job(
    scheduler: AsyncIOScheduler,
    reminder: Reminder,
    *,
    session_factory: SessionFactory,
    sender: Sender,
    tz: ZoneInfo,
    dialog_reset: DialogReset | None = None,
) -> None:
    scheduler.add_job(
        _fire_reminder,
        trigger=make_trigger(reminder.schedule, tz=tz),
        id=f"reminder:{reminder.id}",
        name=reminder.type,
        replace_existing=True,
        kwargs={
            "reminder_id": reminder.id,
            "session_factory": session_factory,
            "sender": sender,
            "scheduler": scheduler,
            "tz": tz,
            "dialog_reset": dialog_reset,
        },
    )


async def build_scheduler(
    *,
    session_factory: SessionFactory = get_session,
    sender: Sender,
    tz: ZoneInfo | None = None,
) -> AsyncIOScheduler:
    """Build a scheduler with one job per active reminder (not started). Dry-run/tests."""
    tz = tz or ZoneInfo(get_settings().timezone)
    scheduler = AsyncIOScheduler(
        timezone=tz, job_defaults={"coalesce": True, "misfire_grace_time": MISFIRE_GRACE_S}
    )
    async with session_factory() as session:
        rows = await reminders.active_reminders(session)
    for reminder in rows:
        _add_job(scheduler, reminder, session_factory=session_factory, sender=sender, tz=tz)
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


class ReminderScheduler:
    """A running scheduler that stays in sync with the DB as reminders change.

    Stored in ``dispatcher["reminder_scheduler"]`` so handlers can ``schedule`` a
    reminder they just created or ``unschedule`` one they retired — live, no restart.
    """

    def __init__(
        self,
        *,
        sender: Sender,
        session_factory: SessionFactory = get_session,
        tz: ZoneInfo | None = None,
        dialog_reset: DialogReset | None = None,
    ) -> None:
        self._sender = sender
        self._sf = session_factory
        self._dialog_reset = dialog_reset
        self._tz = tz or ZoneInfo(get_settings().timezone)
        self._scheduler = AsyncIOScheduler(
            timezone=self._tz,
            job_defaults={"coalesce": True, "misfire_grace_time": MISFIRE_GRACE_S},
        )

    async def reconcile(self) -> None:
        """Startup self-heal: a check-in reminder exists iff the user should have one — an active
        concern OR a currently out-of-range indicator (``health.should_have_checkin``). Also
        re-times an existing check-in to the configured hour (``ensure_checkin_reminder`` updates
        it), so a changed check-in time takes effect on restart; ``start()`` then schedules it."""
        today = datetime.now(self._tz).date()
        async with self._sf() as session:
            users = (await session.scalars(select(User))).all()
            for user in users:
                wanted = await health.should_have_checkin(session, user.id, today=today)
                existing = await session.scalar(
                    select(Reminder).where(
                        Reminder.user_id == user.id,
                        Reminder.type == reminders.TYPE_CHECKIN,
                        Reminder.active.is_(True),
                    )
                )
                if wanted:
                    await reminders.ensure_checkin_reminder(session, user=user)  # create OR retime
                elif existing is not None:
                    await reminders.deactivate(session, existing)
            await session.commit()

    async def start(self) -> None:
        await self.reconcile()
        self._scheduler.start()
        # Deliver anything that came due while we were down (durability), THEN schedule
        # future occurrences. Catch-up runs first so an overdue one-off is retired and
        # not also scheduled — no double-fire.
        await self._catch_up_missed()
        async with self._sf() as session:
            rows = await reminders.active_reminders(session)
        for reminder in rows:
            self.schedule(reminder)

    async def _catch_up_missed(self) -> None:
        """For each active reminder, deliver one missed occurrence (if any) from downtime."""
        now = datetime.now(self._tz)
        async with self._sf() as session:
            rows = await reminders.active_reminders(session)
        for reminder in rows:
            floor = now - MAX_CATCHUP
            if reminder.last_fired_at is not None:
                floor = max(floor, _aware(reminder.last_fired_at, self._tz))
            trigger = make_trigger(reminder.schedule, tz=self._tz)
            if last_due_occurrence(trigger, floor=floor, now=now) is not None:
                await _fire_reminder(
                    reminder.id,
                    session_factory=self._sf,
                    sender=self._sender,
                    scheduler=self._scheduler,
                    tz=self._tz,
                    dialog_reset=self._dialog_reset,
                )

    def schedule(self, reminder: Reminder) -> None:
        _add_job(
            self._scheduler,
            reminder,
            session_factory=self._sf,
            sender=self._sender,
            tz=self._tz,
            dialog_reset=self._dialog_reset,
        )

    def unschedule(self, reminder_id: int) -> None:
        job_id = f"reminder:{reminder_id}"
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.remove_job(job_id)

    def unschedule_many(self, reminder_ids: Iterable[int]) -> None:
        for reminder_id in reminder_ids:
            self.unschedule(reminder_id)

    def list_jobs(self) -> list[JobInfo]:
        return describe_jobs(self._scheduler)

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)


async def _noop_sender(
    telegram_id: int, text: str, *, buttons: Buttons | None = None
) -> None:  # pragma: no cover - dry-run stub
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
