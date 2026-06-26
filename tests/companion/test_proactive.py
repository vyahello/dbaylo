"""The conditional-check-in invariant and reminder scheduling, against a live scheduler.

A check-in job exists iff at least one active concern exists; adding the first
concern schedules it and resolving the last removes it. Medication adds N jobs and
turning it off removes them all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.companion import proactive
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.db.models import User

TZ = ZoneInfo("Europe/Kyiv")


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=500, name="Test")
    session.add(user)
    await session.flush()
    return user


async def _sender(telegram_id: int, text: str, *, buttons: object | None = None) -> None:
    return None


@pytest_asyncio.fixture
async def scheduler(async_session: AsyncSession) -> AsyncIterator[ReminderScheduler]:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield async_session

    rs = ReminderScheduler(sender=_sender, session_factory=factory, tz=TZ)
    await rs.start()  # empty DB -> starts with no jobs
    yield rs
    rs.shutdown()


def _count(scheduler: ReminderScheduler, type_: str) -> int:
    return sum(job.type == type_ for job in scheduler.list_jobs())


async def test_no_concern_means_no_checkin_job(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    await _user(async_session)
    assert _count(scheduler, "checkin") == 0


async def test_an_active_goal_schedules_the_checkin(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    # Goals are functional: an active goal alone turns ON the daily check-in (Дбайло follows up).
    from dbaylo.companion import goals

    user = await _user(async_session)
    assert _count(scheduler, "checkin") == 0
    result = await goals.set_goal(async_session, user=user, text="Більше рухатися щодня")
    assert result.saved
    await proactive.reconcile_checkin(async_session, user=user, scheduler=scheduler)
    await async_session.commit()
    assert _count(scheduler, "checkin") == 1  # a goal alone is reason enough to check in


async def test_resolve_then_reopen_restores_the_concern_and_checkin(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    # ✅ resolve closes a concern (it leaves «під наглядом»), but it is NOT gone: it lands in the
    # «✔️ Вирішені» archive and ↩️ re-opens it (back under nadhliad, check-in back on).
    from dbaylo.companion import concerns

    user = await _user(async_session)
    c = await proactive.add_problem(async_session, user=user, name="залізо", scheduler=scheduler)
    await async_session.commit()
    assert _count(scheduler, "checkin") == 1

    await proactive.resolve_problem(
        async_session, user_id=user.id, condition_id=c.id, scheduler=scheduler
    )
    await async_session.commit()
    assert _count(scheduler, "checkin") == 0  # nothing active -> retired
    resolved = await concerns.list_resolved(async_session, user_id=user.id)
    assert [r.id for r in resolved] == [c.id]  # archived, not gone

    reopened = await proactive.reopen_problem(
        async_session, user_id=user.id, condition_id=c.id, scheduler=scheduler
    )
    await async_session.commit()
    assert reopened is not None
    assert _count(scheduler, "checkin") == 1  # back under watch -> check-in back on
    assert not await concerns.list_resolved(async_session, user_id=user.id)  # left the archive
    assert [a.id for a in await concerns.list_active(async_session, user_id=user.id)] == [c.id]


async def test_first_problem_schedules_checkin_then_last_resolve_removes_it(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    user = await _user(async_session)
    a = await proactive.add_problem(async_session, user=user, name="тиск", scheduler=scheduler)
    b = await proactive.add_problem(async_session, user=user, name="сон", scheduler=scheduler)
    await async_session.commit()
    assert _count(scheduler, "checkin") == 1  # exactly one, for two concerns

    await proactive.resolve_problem(
        async_session, user_id=user.id, condition_id=a.id, scheduler=scheduler
    )
    await async_session.commit()
    assert _count(scheduler, "checkin") == 1  # one concern still active -> stays

    await proactive.resolve_problem(
        async_session, user_id=user.id, condition_id=b.id, scheduler=scheduler
    )
    await async_session.commit()
    assert _count(scheduler, "checkin") == 0  # none left -> removed


async def test_medication_schedules_all_jobs_and_turn_off_removes_all(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    user = await _user(async_session)
    med, _created = await proactive.add_medication(
        async_session,
        user=user,
        name="Аспірин",
        times=[time(8, 0), time(20, 0)],
        scheduler=scheduler,
    )
    await async_session.commit()
    assert _count(scheduler, "medication") == 2

    await proactive.turn_off_medication(async_session, medication_id=med.id, scheduler=scheduler)
    await async_session.commit()
    assert _count(scheduler, "medication") == 0  # no orphaned jobs


async def test_reconcile_checkin_schedules_from_a_data_flag_without_a_concern(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    # The big-idea trigger: a currently out-of-range indicator warrants a proactive check-in even
    # with NO manually-added concern.
    from datetime import date as _date
    from pathlib import Path

    from dbaylo.labs.intake import create_pending_report, persist_confirmed
    from dbaylo.labs.schema import ExtractedAnalyte

    user = await _user(async_session)
    assert _count(scheduler, "checkin") == 0  # nothing yet

    report = await create_pending_report(async_session, user=user, file_path=Path("/tmp/g.png"))
    await persist_confirmed(
        async_session,
        report=report,
        analytes=[
            ExtractedAnalyte(
                analyte="Глюкоза", value=7.0, unit="ммоль/л", ref_low=3.9, ref_high=6.1
            )
        ],
        report_date=_date(2026, 6, 2),
        lab="Synevo",
    )
    await proactive.reconcile_checkin(async_session, user=user, scheduler=scheduler)
    await async_session.commit()
    assert _count(scheduler, "checkin") == 1  # the data flag scheduled it


async def test_dismiss_problem_retires_the_data_driven_checkin(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    # "Не турбує" on the only out-of-range finding -> the data-driven check-in is retired (the agent
    # stops nagging about something the user waved off).
    from datetime import date as _date
    from pathlib import Path

    from dbaylo.labs.intake import create_pending_report, persist_confirmed
    from dbaylo.labs.schema import ExtractedAnalyte

    user = await _user(async_session)
    report = await create_pending_report(async_session, user=user, file_path=Path("/tmp/g2.png"))
    await persist_confirmed(
        async_session,
        report=report,
        analytes=[
            ExtractedAnalyte(
                analyte="Глюкоза", value=7.0, unit="ммоль/л", ref_low=3.9, ref_high=6.1
            )
        ],
        report_date=_date(2026, 6, 2),
        lab="Synevo",
    )
    await proactive.reconcile_checkin(async_session, user=user, scheduler=scheduler)
    assert _count(scheduler, "checkin") == 1  # the flag scheduled it

    await proactive.dismiss_problem(async_session, user=user, name="Глюкоза", scheduler=scheduler)
    await async_session.commit()
    assert _count(scheduler, "checkin") == 0  # waved off -> nothing warrants a check-in anymore


async def test_restore_problem_undoes_a_dismissal(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    # An accidental ✖ is reversible: restoring drops the dismissal, so the finding is proposed again
    # AND the data-driven check-in comes back.
    from datetime import date as _date
    from pathlib import Path

    from dbaylo.companion import concerns, health
    from dbaylo.labs.intake import create_pending_report, persist_confirmed
    from dbaylo.labs.schema import ExtractedAnalyte

    user = await _user(async_session)
    report = await create_pending_report(async_session, user=user, file_path=Path("/tmp/g3.png"))
    await persist_confirmed(
        async_session,
        report=report,
        analytes=[
            ExtractedAnalyte(
                analyte="Глюкоза", value=7.0, unit="ммоль/л", ref_low=3.9, ref_high=6.1
            )
        ],
        report_date=_date(2026, 6, 2),
        lab="Synevo",
    )
    dismissed = await proactive.dismiss_problem(
        async_session, user=user, name="Глюкоза", scheduler=scheduler
    )
    await async_session.commit()
    today = _date(2026, 6, 25)
    assert await health.propose_problems(async_session, user.id, today=today) == []  # not proposed
    assert _count(scheduler, "checkin") == 0

    restored = await proactive.restore_problem(
        async_session, user_id=user.id, condition_id=dismissed.id, scheduler=scheduler
    )
    await async_session.commit()
    assert restored is not None
    assert await concerns.list_dismissed(async_session, user_id=user.id) == []  # dismissal gone
    proposed = [f.name for f in await health.propose_problems(async_session, user.id, today=today)]
    assert proposed == ["Глюкоза"]  # proposed again
    assert _count(scheduler, "checkin") == 1  # the flag re-enables the check-in


async def test_repeat_lab_is_scheduled(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    user = await _user(async_session)
    await proactive.add_repeat_lab(
        async_session,
        user=user,
        run_at=datetime(2027, 1, 1, 9, 0),
        label="ТТГ",
        scheduler=scheduler,
    )
    await async_session.commit()
    assert _count(scheduler, "repeat_lab") == 1


async def test_add_consult_reminder_dedupes_identical_requests(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    # Asking for the same reminder twice must not leave two identical reminders (the owner hit this
    # by repeating "запиши мене …").
    user = await _user(async_session)
    run_at = datetime(2027, 7, 6, 9, 0)
    label = "Консультація уролога + УЗД нирок (UROSVIT) — 2027-07-11"

    first, created1 = await proactive.add_consult_reminder(
        async_session, user=user, run_at=run_at, label=label, scheduler=scheduler
    )
    assert created1 and _count(scheduler, "consult") == 1

    again, created2 = await proactive.add_consult_reminder(
        async_session, user=user, run_at=run_at, label=label, scheduler=scheduler
    )
    assert not created2 and again.id == first.id  # reused, not duplicated
    assert _count(scheduler, "consult") == 1  # still just one job

    # A genuinely different reminder is still created.
    other, created3 = await proactive.add_consult_reminder(
        async_session, user=user, run_at=run_at, label="інша справа", scheduler=scheduler
    )
    assert created3 and other.id != first.id and _count(scheduler, "consult") == 2


async def test_reminders_list_taps_open_a_view_not_a_delete(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    # The fix: tapping a reminder in the list must READ it (open a card), never delete it. So the
    # list buttons carry a *view* callback; turning off lives behind the card's explicit 🗑 button.
    from dbaylo.bot import proactive_flow
    from dbaylo.companion import callbacks

    user = await _user(async_session)
    rem = await proactive.add_repeat_lab(
        async_session,
        user=user,
        run_at=datetime(2027, 1, 1, 9, 0),
        label="ТТГ",
        scheduler=scheduler,
    )
    await proactive.add_medication(
        async_session, user=user, name="Аспірин", times=[time(8, 0)], scheduler=scheduler
    )
    await async_session.commit()

    text, keyboard = await proactive_flow._reminders_payload(
        async_session, user_id=user.id, scheduler=scheduler
    )
    assert text == locale.REMINDERS_HEADER and keyboard is not None
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    # Every list button is a VIEW (read) — never a turn-off.
    assert any(d == callbacks.reminder_view(rem.id) for d in datas)
    assert any(d and d.startswith(callbacks.MEDICATION_VIEW + ":") for d in datas)
    assert not any(d and d.startswith(callbacks.REMINDER_OFF + ":") for d in datas)
    assert not any(d and d.startswith(callbacks.MEDICATION_OFF + ":") for d in datas)


async def test_checkin_is_an_info_line_not_a_manageable_reminder(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    # The daily check-in is agent-managed: it shows as an info line above the list, never as a
    # deletable button (a concern keeps it alive, so deleting it here would be pointless).
    from dbaylo.bot import proactive_flow
    from dbaylo.companion import callbacks

    user = await _user(async_session)
    await proactive.add_problem(async_session, user=user, name="тиск", scheduler=scheduler)
    await async_session.commit()
    assert _count(scheduler, "checkin") == 1  # a concern scheduled the check-in

    text, keyboard = await proactive_flow._reminders_payload(
        async_session, user_id=user.id, scheduler=scheduler
    )
    # Only the check-in exists -> info line + "no manual reminders", and NO keyboard at all.
    assert keyboard is None
    assert "Щоденний догляд" in text and locale.REMINDERS_NONE_MANUAL in text

    # Add a real (manageable) reminder -> it gets a button, the check-in stays an info line only.
    rem = await proactive.add_repeat_lab(
        async_session,
        user=user,
        run_at=datetime(2027, 1, 1, 9, 0),
        label="ТТГ",
        scheduler=scheduler,
    )
    await async_session.commit()
    text2, keyboard2 = await proactive_flow._reminders_payload(
        async_session, user_id=user.id, scheduler=scheduler
    )
    assert keyboard2 is not None
    datas = [b.callback_data for row in keyboard2.inline_keyboard for b in row]
    assert datas == [callbacks.reminder_view(rem.id)]  # only the repeat-lab, not the check-in
    assert "Щоденний догляд" in text2  # the check-in info line is still shown above


async def test_card_keyboard_offers_delete_and_back(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    from dbaylo.bot import proactive_flow
    from dbaylo.companion import callbacks

    keyboard = proactive_flow._card_keyboard(callbacks.reminder_off(7))
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert datas == [callbacks.reminder_off(7), callbacks.REMINDERS_BACK]  # 🗑 delete · ◀ back


async def test_delete_reminder_hard_deletes_the_row(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    # A turned-off reminder can't be turned back on, so the card DELETES the row (not soft-disable).
    from sqlalchemy import select

    from dbaylo.db.models import Reminder

    user = await _user(async_session)
    rem = await proactive.add_repeat_lab(
        async_session,
        user=user,
        run_at=datetime(2027, 1, 1, 9, 0),
        label="ТТГ",
        scheduler=scheduler,
    )
    await async_session.commit()
    assert _count(scheduler, "repeat_lab") == 1

    await proactive.delete_reminder(async_session, reminder=rem, scheduler=scheduler)
    await async_session.commit()
    assert _count(scheduler, "repeat_lab") == 0  # job gone
    remaining = (await async_session.execute(select(Reminder).where(Reminder.id == rem.id))).all()
    assert remaining == []  # the row is GONE, not just inactive


async def test_delete_medication_reminders_removes_rows_but_keeps_the_record(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    from sqlalchemy import select

    from dbaylo.db.models import Medication, Reminder

    user = await _user(async_session)
    med, _created = await proactive.add_medication(
        async_session,
        user=user,
        name="Аспірин",
        times=[time(8, 0), time(20, 0)],
        scheduler=scheduler,
    )
    await async_session.commit()
    assert _count(scheduler, "medication") == 2

    await proactive.delete_medication_reminders(
        async_session, medication_id=med.id, scheduler=scheduler
    )
    await async_session.commit()
    assert _count(scheduler, "medication") == 0  # all jobs gone
    rows = (
        await async_session.execute(select(Reminder).where(Reminder.medication_id == med.id))
    ).all()
    assert rows == []  # the medication's reminder rows are gone
    assert await async_session.get(Medication, med.id) is not None  # the prescription record stays


async def test_empty_reminders_list_has_no_keyboard(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    from dbaylo.bot import proactive_flow

    user = await _user(async_session)
    text, keyboard = await proactive_flow._reminders_payload(
        async_session, user_id=user.id, scheduler=scheduler
    )
    assert text == locale.REMINDERS_EMPTY and keyboard is None


async def test_medications_list_taps_open_a_card_not_a_turn_off(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    # The fix: tapping a medication must OPEN its card (read), never destructively turn it off.
    from dbaylo.bot import proactive_flow
    from dbaylo.companion import callbacks

    user = await _user(async_session)
    med, _created = await proactive.add_medication(
        async_session, user=user, name="Метформін", times=[time(9, 0)], scheduler=scheduler
    )
    await async_session.commit()
    text, keyboard = await proactive_flow._medications_payload(async_session, user_id=user.id)
    assert keyboard is not None
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert datas == [callbacks.medication_view(med.id, "m")]  # opens the card (origin = meds list)
    assert not any(d.startswith(callbacks.MEDICATION_OFF + ":") for d in datas)  # never a turn-off

    # A turned-off medication leaves the list (its record + dose are kept elsewhere).
    await proactive.turn_off_medication(async_session, medication_id=med.id, scheduler=scheduler)
    await async_session.commit()
    text2, keyboard2 = await proactive_flow._medications_payload(async_session, user_id=user.id)
    assert keyboard2 is None and text2 == locale.MED_LIST_EMPTY


def test_med_card_shows_the_dose_as_a_record() -> None:
    from dbaylo.bot import proactive_flow
    from dbaylo.db.models import Medication

    med = Medication(name="Метформін", dose="850 мг", schedule="09:00, 21:00")
    card = proactive_flow._med_card(med, when="2026-06-27 09:00")
    assert "Метформін" in card and "850 мг" in card  # the dose is visible as a record
    assert "09:00, 21:00" in card  # the times are shown


def test_med_card_keyboard_back_follows_the_origin() -> None:
    from dbaylo.bot import proactive_flow
    from dbaylo.companion import callbacks

    from_meds = proactive_flow._med_card_keyboard(5, "m")
    from_rems = proactive_flow._med_card_keyboard(5, "r")
    meds_datas = [b.callback_data for row in from_meds.inline_keyboard for b in row]
    rems_datas = [b.callback_data for row in from_rems.inline_keyboard for b in row]
    # Both offer a deliberate turn-off; back returns to the list the card was opened from.
    assert meds_datas == [callbacks.medication_off(5, "m"), callbacks.MED_LIST_BACK]
    assert rems_datas == [callbacks.medication_off(5, "r"), callbacks.REMINDERS_BACK]
