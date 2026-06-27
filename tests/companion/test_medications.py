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


def test_parse_frequency() -> None:
    assert medications.parse_frequency("3 рази на день") == 3
    assert medications.parse_frequency("2 таблетки 3 рази в день") == 3  # the freq, not the amount
    assert medications.parse_frequency("двічі на день") == 2
    assert medications.parse_frequency("тричі на добу") == 3
    assert medications.parse_frequency("раз на день") == 1
    assert medications.parse_frequency("08:00, 20:00") is None  # explicit times, no frequency word
    assert medications.parse_frequency("ношпа") is None
    assert medications.parse_frequency("99 разів") is None  # out of the sane 1..6 range


def test_distribute_times_spreads_across_waking_hours() -> None:
    assert medications.distribute_times(1) == [time(9, 0)]
    assert medications.distribute_times(2) == [time(9, 0), time(21, 0)]
    assert medications.distribute_times(3) == [time(8, 0), time(14, 0), time(20, 0)]
    assert len(medications.distribute_times(4)) == 4
    assert medications.distribute_times(99) == medications.distribute_times(6)  # clamped


def test_parse_dose() -> None:
    assert medications.parse_dose("2 таблетки 3 рази") == "2 таблетки"
    assert medications.parse_dose("500 мг двічі") == "500 мг"
    assert medications.parse_dose("3 рази на день") is None  # a frequency is not a dose


def test_resolve_schedule_prefers_explicit_times_then_frequency() -> None:
    # A doctor prescribes a frequency, not hours — the bot spreads the day. Explicit times win.
    times, dose = medications.resolve_schedule("2 таблетки 3 рази в день")
    assert times == [time(8, 0), time(14, 0), time(20, 0)] and dose == "2 таблетки"
    times, dose = medications.resolve_schedule("08:00, 20:00")
    assert times == [time(8, 0), time(20, 0)] and dose is None
    assert medications.resolve_schedule("колись")[0] == []  # nothing usable -> re-ask


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
