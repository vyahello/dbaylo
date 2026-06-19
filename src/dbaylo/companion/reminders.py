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

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.config import get_settings
from dbaylo.db.models import Reminder, User
from dbaylo.triage.safety import assert_safe_output

# "через 10 днів" / "через 2 тижні" / "через місяць" / "через рік" -> a future datetime.
_RELATIVE_RE = re.compile(
    r"через\s+(\d+)?\s*(дн|день|тижд|тижн|тиждень|місяц|міс|рок|рік|год)", re.IGNORECASE
)


def parse_relative_when(text: str, *, base: datetime) -> datetime | None:
    """Parse a relative timeframe into a future datetime (months ~30 days, year 365)."""
    match = _RELATIVE_RE.search(text.casefold())
    if match is None:
        return None
    count = int(match.group(1)) if match.group(1) else 1
    unit = match.group(2)
    if unit.startswith(("дн", "день")):
        days = count
    elif unit.startswith(("тижд", "тижн", "тиждень")):
        days = count * 7
    elif unit.startswith(("місяц", "міс")):
        days = count * 30
    elif unit.startswith(("рок", "рік")):
        days = count * 365
    else:
        return None
    return base + timedelta(days=days)


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
    medication_id: int | None = None,
    report_id: int | None = None,
) -> Reminder:
    """Create a reminder row (validates the schedule string is parseable).

    ``last_fired_at`` is anchored at creation so the scheduler's startup catch-up never
    delivers an occurrence from *before* the reminder existed (it only catches up missed
    occurrences after this anchor).
    """
    parse_schedule(schedule)  # fail fast on a malformed schedule
    reminder = Reminder(
        user_id=user.id,
        type=type,
        schedule=schedule,
        payload=payload,
        medication_id=medication_id,
        report_id=report_id,
        last_fired_at=datetime.now(ZoneInfo(get_settings().timezone)),
    )
    session.add(reminder)
    await session.flush()
    return reminder


async def create_repeat_lab(
    session: AsyncSession, *, user: User, run_at: datetime, label: str, report_id: int | None = None
) -> Reminder:
    """Create a one-off repeat-lab reminder for ``run_at`` (offered on lab confirm)."""
    return await create_reminder(
        session,
        user=user,
        type=TYPE_REPEAT_LAB,
        schedule=once(run_at),
        payload=label,
        report_id=report_id,
    )


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


async def active_reminders_for_user(session: AsyncSession, *, user_id: int) -> list[Reminder]:
    """A single user's active reminders (for the /reminders management list)."""
    rows = await session.scalars(
        select(Reminder)
        .where(Reminder.user_id == user_id, Reminder.active.is_(True))
        .order_by(Reminder.type, Reminder.id)
    )
    return list(rows.all())


async def deactivate(session: AsyncSession, reminder: Reminder) -> None:
    """Soft-delete a reminder (e.g. a fired one-off) without losing the record."""
    reminder.active = False
    await session.flush()


async def deactivate_medication(session: AsyncSession, medication_id: int) -> list[int]:
    """Soft-delete every active reminder for a medication; return their ids to unschedule.

    One medication maps to one reminder per dose time, so turning it off must retire
    them all — no orphaned jobs left running.
    """
    rows = await session.scalars(
        select(Reminder).where(Reminder.medication_id == medication_id, Reminder.active.is_(True))
    )
    ids: list[int] = []
    for reminder in rows.all():
        reminder.active = False
        ids.append(reminder.id)
    await session.flush()
    return ids


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
