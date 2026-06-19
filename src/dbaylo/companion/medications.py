"""Medications — user-entered record-keeping that drives recurring reminders.

Rail #1: the bot never suggests or selects a drug or a dose. The user types the
medication name and the dose *times* (from their doctor's prescription); we store a
:class:`Medication` record and create one recurring :class:`Reminder` per time. The
reminder text names the medication and defers to the doctor — it never carries a dose.

Turning a medication off deactivates **all** of its reminders (one per time), so no
orphaned jobs keep firing.
"""

from __future__ import annotations

import re
from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import reminders
from dbaylo.db.models import Medication, Reminder, User

_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def parse_times(text: str) -> list[time]:
    """Extract dose times (HH:MM) from free text, de-duplicated, in order."""
    seen: set[time] = set()
    out: list[time] = []
    for match in _TIME_RE.finditer(text):
        t = time(int(match.group(1)), int(match.group(2)))
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


async def add_medication(
    session: AsyncSession, *, user: User, name: str, times: list[time]
) -> tuple[Medication, list[Reminder]]:
    """Record the medication and create one daily reminder per dose time."""
    medication = Medication(
        user_id=user.id,
        name=name.strip(),
        schedule=", ".join(t.strftime("%H:%M") for t in times),
    )
    session.add(medication)
    await session.flush()

    created: list[Reminder] = []
    for t in times:
        reminder = await reminders.create_reminder(
            session,
            user=user,
            type=reminders.TYPE_MEDICATION,
            schedule=f"cron:{t.minute} {t.hour} * * *",
            payload=medication.name,
            medication_id=medication.id,
        )
        created.append(reminder)
    return medication, created


async def list_medications(session: AsyncSession, *, user_id: int) -> list[Medication]:
    rows = await session.scalars(
        select(Medication).where(Medication.user_id == user_id).order_by(Medication.created_at)
    )
    return list(rows.all())
