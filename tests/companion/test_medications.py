"""Medications: time parsing and reminder creation (one per time, no dose)."""

from __future__ import annotations

from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import medications, reminders
from dbaylo.db.models import User


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=7, name="Test")
    session.add(user)
    await session.flush()
    return user


def test_parse_times() -> None:
    assert medications.parse_times("08:00, 20:00") == [time(8, 0), time(20, 0)]
    assert medications.parse_times("щодня о 9:30") == [time(9, 30)]
    assert medications.parse_times("08:00 08:00") == [time(8, 0)]  # de-duplicated
    assert medications.parse_times("колись") == []


async def test_add_medication_creates_one_reminder_per_time(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    med, created = await medications.add_medication(
        async_session, user=user, name="Аспірин", times=[time(8, 0), time(20, 0)]
    )
    assert med.schedule == "08:00, 20:00"
    assert len(created) == 2
    assert {r.schedule for r in created} == {"cron:0 8 * * *", "cron:0 20 * * *"}
    assert all(r.type == reminders.TYPE_MEDICATION for r in created)
    assert all(r.medication_id == med.id for r in created)
    # The reminder text never carries a dose (rail #1).
    from dbaylo.triage.safety import contains_dose_directive

    for reminder in created:
        assert contains_dose_directive(reminders.render_reminder(reminder)) is None
