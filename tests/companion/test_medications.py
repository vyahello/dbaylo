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
    assert medications.parse_frequency("3 р/д") == 3  # the doctor's abbreviation
    assert medications.parse_frequency("по 1 таб 3 р/д – 1 міс") == 3
    assert medications.parse_frequency("двічі на день") == 2
    assert medications.parse_frequency("тричі на добу") == 3
    assert medications.parse_frequency("раз на день") == 1
    assert medications.parse_frequency("08:00, 20:00") is None  # explicit times, no frequency word
    assert medications.parse_frequency("ношпа") is None
    assert medications.parse_frequency("99 разів") is None  # out of the sane 1..6 range


def test_times_from_text_handles_real_prescription_shorthand() -> None:
    # The owner's urologist script: "зранку", "на ніч", "3 р/д" — doctor shorthand, not clock times.
    assert medications.times_from_text("60 мг по 1 кап зранку - 3 міс") == [time(9, 0)]
    assert medications.times_from_text("7,5 мг по 1 таб на ніч – 1 міс") == [time(21, 0)]
    assert medications.times_from_text("по 1 таб 3 р/д – 1 міс") == [
        time(8, 0),
        time(14, 0),
        time(20, 0),
    ]
    assert medications.times_from_text("вранці та ввечері") == [time(9, 0), time(21, 0)]
    assert medications.times_from_text("08:00, 20:00") == [time(8, 0), time(20, 0)]  # explicit wins
    assert medications.times_from_text("за потреби") == []  # nothing schedulable


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


def test_course_end_from_duration() -> None:
    from datetime import date

    start = date(2026, 6, 27)
    assert medications.course_end(start, "3 міс.") == date(2026, 9, 27)
    assert medications.course_end(start, "1 міс.") == date(2026, 7, 27)
    assert medications.course_end(start, "10 днів") == date(2026, 7, 7)
    assert medications.course_end(start, "2 тижні") == date(2026, 7, 11)
    assert medications.course_end(start, "до 15.07") == date(2026, 7, 15)
    assert medications.course_end(start, None) is None  # open-ended -> never expires
    assert medications.course_end(start, "за потреби") is None


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


async def test_add_medication_stores_the_prescription_photo_path(
    async_session: AsyncSession,
) -> None:
    # A med read from a prescription photo keeps the photo path (so the user can re-open it); a
    # manually-entered med has none.
    user = await _user(async_session)
    med, _ = await medications.add_medication(
        async_session, user=user, name="Но-шпа", times=[time(9, 0)], source_file="/data/rx/42.jpg"
    )
    assert med.source_file == "/data/rx/42.jpg"
    manual, _ = await medications.add_medication(
        async_session, user=user, name="Вітамін D", times=[time(9, 0)]
    )
    assert manual.source_file is None


async def test_add_medication_stores_the_course_group(async_session: AsyncSession) -> None:
    # Meds from one prescription share a course label (the 💊 list groups them); a manual one none.
    user = await _user(async_session)
    med, _ = await medications.add_medication(
        async_session, user=user, name="Буспірон", times=[time(8, 0)], course="Рецепт від уролога"
    )
    assert med.course == "Рецепт від уролога"
    manual, _ = await medications.add_medication(
        async_session, user=user, name="Магній", times=[time(9, 0)]
    )
    assert manual.course is None


async def test_list_by_course_returns_only_that_prescription(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    for name, course in [("A", "Рецепт"), ("B", "Рецепт"), ("C", "Інший")]:
        await medications.add_medication(
            async_session, user=user, name=name, times=[time(9, 0)], course=course
        )
    group = await medications.list_by_course(async_session, user_id=user.id, course="Рецепт")
    assert {m.name for m in group} == {"A", "B"}
