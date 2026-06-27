"""Reminders: schedule parsing, CRUD, and safe message rendering."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import reminders
from dbaylo.companion.reminders import (
    CronSpec,
    DateSpec,
    daily_cron,
    once,
    parse_schedule,
)
from dbaylo.db.models import Reminder, User
from dbaylo.triage.safety import contains_dose_directive


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=99, name="Test")
    session.add(user)
    await session.flush()
    return user


def test_parse_cron_schedule() -> None:
    spec = parse_schedule("cron:0 21 * * *")
    assert isinstance(spec, CronSpec)
    assert spec.hour == "21" and spec.minute == "0"


def test_parse_date_schedule() -> None:
    spec = parse_schedule("date:2026-09-01T09:00:00")
    assert isinstance(spec, DateSpec)
    assert spec.run_at == datetime(2026, 9, 1, 9, 0, 0)


@pytest.mark.parametrize("bad", ["cron:0 21 * *", "weekly:mon", "0 21 * * *", "date:not-a-date"])
def test_parse_rejects_malformed_schedules(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_schedule(bad)


def test_schedule_builders() -> None:
    assert daily_cron(21) == "cron:0 21 * * *"
    assert once(datetime(2026, 9, 1, 9, 0)).startswith("date:2026-09-01T09:00")


async def test_ensure_checkin_reminder_is_idempotent(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    a = await reminders.ensure_checkin_reminder(async_session, user=user)
    b = await reminders.ensure_checkin_reminder(async_session, user=user)
    assert a.id == b.id


async def test_active_and_deactivate(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    rem = await reminders.create_reminder(
        async_session,
        user=user,
        type=reminders.TYPE_REPEAT_LAB,
        schedule=once(datetime(2026, 9, 1, 9, 0)),
        payload="загальний аналіз крові",
    )
    assert len(await reminders.active_reminders(async_session)) == 1
    await reminders.deactivate(async_session, rem)
    assert await reminders.active_reminders(async_session) == []


def test_medication_reminder_text_carries_no_dose() -> None:
    rem = Reminder(type=reminders.TYPE_MEDICATION, schedule=daily_cron(9), payload="Аспірин")
    text = reminders.render_reminder(rem)
    assert "Аспірин" in text
    assert contains_dose_directive(text) is None


def test_medication_reminder_can_show_the_doctors_strength() -> None:
    # The doctor's drug STRENGTH is shown as a record (rail #1) — never a dose directive: the text
    # still passes the dose-directive guard.
    rem = Reminder(type=reminders.TYPE_MEDICATION, schedule=daily_cron(21), payload="зопіклон")
    text = reminders.render_reminder(rem, dose="7,5 мг")
    assert "зопіклон" in text and "7,5 мг" in text
    assert contains_dose_directive(text) is None


def test_medication_reminder_falls_back_when_dose_reads_as_a_directive() -> None:
    # Defense in depth: if a malformed "dose" would read as a directive, the reminder drops it and
    # renders the safe dose-less line rather than ever emitting a prescription.
    rem = Reminder(type=reminders.TYPE_MEDICATION, schedule=daily_cron(9), payload="Аспірин")
    text = reminders.render_reminder(rem, dose="по 2 таблетки")
    assert "Аспірин" in text and "таблетки" not in text
    assert contains_dose_directive(text) is None


def test_repeat_lab_reminder_text() -> None:
    rem = Reminder(
        type=reminders.TYPE_REPEAT_LAB, schedule=once(datetime(2026, 9, 1, 9, 0)), payload="ТТГ"
    )
    assert "ТТГ" in reminders.render_reminder(rem)
