"""Coordinator: keep the live scheduler in sync with concern/medication/reminder state.

The conditional-check-in invariant lives here: a check-in reminder (and its job)
exists **iff** the user has at least one active concern. Adding the first concern
schedules it; resolving the last one removes it. Medication add schedules one job per
dose time; turning a medication off removes them all. Everything runs against the
**live** :class:`ReminderScheduler`, so changes take effect without a restart.
"""

from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import concerns, medications, reminders
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.db.models import Condition, Medication, Reminder, User


async def _active_checkin(session: AsyncSession, user_id: int) -> Reminder | None:
    result: Reminder | None = await session.scalar(
        select(Reminder).where(
            Reminder.user_id == user_id,
            Reminder.type == reminders.TYPE_CHECKIN,
            Reminder.active.is_(True),
        )
    )
    return result


async def add_problem(
    session: AsyncSession,
    *,
    user: User,
    name: str,
    scheduler: ReminderScheduler,
    report_id: int | None = None,
) -> Condition:
    """Add an active concern; schedule the daily check-in if it's the first one."""
    condition = await concerns.add_active(session, user=user, name=name, report_id=report_id)
    if await _active_checkin(session, user.id) is None:
        reminder = await reminders.ensure_checkin_reminder(session, user=user)
        await session.flush()
        scheduler.schedule(reminder)
    return condition


async def resolve_problem(
    session: AsyncSession, *, user_id: int, condition_id: int, scheduler: ReminderScheduler
) -> Condition | None:
    """Resolve a concern; remove the check-in when no active concern remains."""
    condition = await concerns.resolve(session, condition_id)
    if condition is None or condition.user_id != user_id:
        return condition
    if await concerns.count_active(session, user_id=user_id) == 0:
        reminder = await _active_checkin(session, user_id)
        if reminder is not None:
            reminder_id = reminder.id
            await reminders.deactivate(session, reminder)
            scheduler.unschedule(reminder_id)
    return condition


async def add_medication(
    session: AsyncSession,
    *,
    user: User,
    name: str,
    times: list[time],
    scheduler: ReminderScheduler,
) -> tuple[Medication, list[Reminder]]:
    medication, created = await medications.add_medication(
        session, user=user, name=name, times=times
    )
    for reminder in created:
        scheduler.schedule(reminder)
    return medication, created


async def add_repeat_lab(
    session: AsyncSession,
    *,
    user: User,
    run_at: datetime,
    label: str,
    scheduler: ReminderScheduler,
    report_id: int | None = None,
) -> Reminder:
    reminder = await reminders.create_repeat_lab(
        session, user=user, run_at=run_at, label=label, report_id=report_id
    )
    scheduler.schedule(reminder)
    return reminder


async def turn_off_reminder(
    session: AsyncSession, *, reminder: Reminder, scheduler: ReminderScheduler
) -> None:
    reminder_id = reminder.id
    await reminders.deactivate(session, reminder)
    scheduler.unschedule(reminder_id)


async def turn_off_medication(
    session: AsyncSession, *, medication_id: int, scheduler: ReminderScheduler
) -> None:
    ids = await reminders.deactivate_medication(session, medication_id)
    scheduler.unschedule_many(ids)
