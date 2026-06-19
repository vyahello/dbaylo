"""Reminder rows are the source of truth; this module reads/writes them.

A reminder's ``schedule`` column is a small tagged string so the scheduler can
rebuild a trigger from the DB alone:

* ``cron:<m> <h> <dom> <mon> <dow>`` — a recurring APScheduler cron trigger
* ``date:<ISO-8601>`` — a one-off trigger (e.g. a repeat-lab reminder)

Parsing here is pure (no APScheduler import); :mod:`dbaylo.companion.scheduler`
turns a :class:`ScheduleSpec` into an actual trigger. Reminder *message* rendering
is also here and always defers to a doctor for medication (rail #1 — never a dose).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.db.models import Reminder, User
from dbaylo.triage.safety import assert_safe_output

# Reminder type tokens (English, stored in Reminder.type).
TYPE_CHECKIN = "checkin"
TYPE_MEDICATION = "medication"
TYPE_REPEAT_LAB = "repeat_lab"


@dataclass(frozen=True)
class CronSpec:
    minute: str
    hour: str
    day: str
    month: str
    day_of_week: str


@dataclass(frozen=True)
class DateSpec:
    run_at: datetime


ScheduleSpec = CronSpec | DateSpec


def parse_schedule(schedule: str) -> ScheduleSpec:
    """Parse a tagged ``schedule`` string into a :class:`ScheduleSpec` (pure)."""
    tag, _, body = schedule.partition(":")
    if tag == "cron":
        fields = body.split()
        if len(fields) != 5:
            raise ValueError(f"cron schedule needs 5 fields, got {len(fields)}: {schedule!r}")
        minute, hour, day, month, day_of_week = fields
        return CronSpec(minute, hour, day, month, day_of_week)
    if tag == "date":
        return DateSpec(datetime.fromisoformat(body))
    raise ValueError(f"unknown schedule tag in {schedule!r}; expected 'cron:' or 'date:'")


def daily_cron(hour: int, minute: int = 0) -> str:
    """Build a daily ``cron:`` schedule string (e.g. ``daily_cron(21)``)."""
    return f"cron:{minute} {hour} * * *"


def once(run_at: datetime) -> str:
    """Build a one-off ``date:`` schedule string."""
    return f"date:{run_at.isoformat()}"


async def create_reminder(
    session: AsyncSession,
    *,
    user: User,
    type: str,
    schedule: str,
    payload: str | None = None,
) -> Reminder:
    """Create a reminder row (validates the schedule string is parseable)."""
    parse_schedule(schedule)  # fail fast on a malformed schedule
    reminder = Reminder(user_id=user.id, type=type, schedule=schedule, payload=payload)
    session.add(reminder)
    await session.flush()
    return reminder


async def ensure_checkin_reminder(
    session: AsyncSession, *, user: User, hour: int = 21, minute: int = 0
) -> Reminder:
    """Get-or-create the user's single daily check-in reminder."""
    existing = await session.scalar(
        select(Reminder).where(
            Reminder.user_id == user.id,
            Reminder.type == TYPE_CHECKIN,
            Reminder.active.is_(True),
        )
    )
    if existing is not None:
        return existing
    return await create_reminder(
        session, user=user, type=TYPE_CHECKIN, schedule=daily_cron(hour, minute)
    )


async def active_reminders(session: AsyncSession) -> list[Reminder]:
    """All active reminders across users — the scheduler's startup source of truth."""
    rows = await session.scalars(select(Reminder).where(Reminder.active.is_(True)))
    return list(rows.all())


async def deactivate(session: AsyncSession, reminder: Reminder) -> None:
    """Soft-delete a reminder (e.g. a fired one-off) without losing the record."""
    reminder.active = False
    await session.flush()


def render_reminder(reminder: Reminder) -> str:
    """Render the Ukrainian message for a reminder; always safety-checked.

    Medication reminders never carry a dose — they name the medication and defer
    to the doctor's instructions (rail #1).
    """
    name = reminder.payload or ""
    if reminder.type == TYPE_MEDICATION:
        body = locale.REMINDER_MEDICATION.format(name=name)
    elif reminder.type == TYPE_REPEAT_LAB:
        body = locale.REMINDER_REPEAT_LAB.format(name=name)
    else:  # check-in
        body = locale.CHECKIN_PROMPT
    return assert_safe_output(body)
