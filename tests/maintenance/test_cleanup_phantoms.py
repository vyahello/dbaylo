"""Phantom-row cleanup: identify and delete browsing-created junk, keep real data."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import reminders
from dbaylo.db.models import (
    Condition,
    ConditionStatus,
    Goal,
    Medication,
    Reminder,
    User,
)
from dbaylo.maintenance.cleanup_phantoms import delete_phantoms, find_phantoms, is_phantom


@pytest.mark.parametrize(
    ("value", "phantom"),
    [
        ("/goals", True),
        ("  /medication", True),
        ("", True),
        ("   ", True),
        (None, True),
        ("більше рухатися", False),
        ("тиск", False),
        ("Аспірин", False),
    ],
)
def test_is_phantom(value, phantom) -> None:
    assert is_phantom(value) is phantom


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=4242, name="Owner")
    session.add(user)
    await session.flush()
    return user


async def test_find_and_delete_keeps_real_rows(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    async_session.add_all(
        [
            Goal(user_id=user.id, type="general", target="більше рухатися"),
            Goal(user_id=user.id, type="general", target="/goals"),
            Goal(user_id=user.id, type="general", target="   "),
            Goal(user_id=user.id, type="general", target=None),
            Condition(user_id=user.id, name="тиск", status=ConditionStatus.ACTIVE),
            Condition(user_id=user.id, name="/medication", status=ConditionStatus.ACTIVE),
            Condition(user_id=user.id, name="", status=ConditionStatus.RESOLVED),
            Medication(user_id=user.id, name="Аспірин"),
            Medication(user_id=user.id, name="/foo"),
        ]
    )
    await async_session.flush()

    found = await find_phantoms(async_session)
    assert (len(found.goals), len(found.conditions), len(found.medications)) == (3, 2, 1)

    counts = await delete_phantoms(async_session)
    await async_session.commit()
    assert (counts.goals, counts.conditions, counts.medications) == (3, 2, 1)

    # Real rows survive.
    goals = (await async_session.scalars(select(Goal))).all()
    assert [g.target for g in goals] == ["більше рухатися"]
    conditions = (await async_session.scalars(select(Condition))).all()
    assert [c.name for c in conditions] == ["тиск"]
    medications = (await async_session.scalars(select(Medication))).all()
    assert [m.name for m in medications] == ["Аспірин"]


async def test_retires_checkin_when_last_active_concern_was_phantom(
    async_session: AsyncSession,
) -> None:
    user = await _user(async_session)
    async_session.add(Condition(user_id=user.id, name="/medication", status=ConditionStatus.ACTIVE))
    async_session.add(
        Reminder(
            user_id=user.id,
            type=reminders.TYPE_CHECKIN,
            schedule="cron:0 21 * * *",
            active=True,
        )
    )
    await async_session.flush()

    counts = await delete_phantoms(async_session)
    await async_session.commit()
    assert counts.checkins_retired == 1
    checkin = await async_session.scalar(
        select(Reminder).where(Reminder.type == reminders.TYPE_CHECKIN)
    )
    assert checkin is not None and checkin.active is False


async def test_keeps_checkin_when_a_real_concern_remains(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    async_session.add_all(
        [
            Condition(user_id=user.id, name="тиск", status=ConditionStatus.ACTIVE),
            Condition(user_id=user.id, name="/x", status=ConditionStatus.ACTIVE),
            Reminder(
                user_id=user.id,
                type=reminders.TYPE_CHECKIN,
                schedule="cron:0 21 * * *",
                active=True,
            ),
        ]
    )
    await async_session.flush()

    counts = await delete_phantoms(async_session)
    await async_session.commit()
    assert counts.checkins_retired == 0
    checkin = await async_session.scalar(
        select(Reminder).where(Reminder.type == reminders.TYPE_CHECKIN)
    )
    assert checkin is not None and checkin.active is True


async def test_deletes_medication_reminders(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    med = Medication(user_id=user.id, name="/foo")
    async_session.add(med)
    await async_session.flush()
    async_session.add(
        Reminder(
            user_id=user.id,
            type=reminders.TYPE_MEDICATION,
            schedule="cron:0 8 * * *",
            medication_id=med.id,
            active=True,
        )
    )
    await async_session.flush()

    counts = await delete_phantoms(async_session)
    await async_session.commit()
    assert counts.medications == 1 and counts.medication_reminders == 1
    assert (await async_session.scalars(select(Reminder))).first() is None
