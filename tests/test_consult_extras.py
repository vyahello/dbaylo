"""Consult Phase-2 extras: the reminder mini-flow (#4d) + clinic-coverage entry (#3).

Covers the deterministic, testable pieces — the when-parser, the new reminder type's render, the
one-off creation, and the callback round-trip. The Telegram handlers themselves are thin wiring.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.bot.consult_flow import _parse_when
from dbaylo.companion import callbacks, reminders
from dbaylo.db.models import Reminder
from dbaylo.labs.intake import ensure_user
from dbaylo.triage.safety import contains_dose_directive, contains_forbidden_reassurance


def test_consult_remind_when_callback_roundtrips() -> None:
    assert callbacks.parse_consult_remind_when(callbacks.consult_remind_when(30)) == 30
    assert callbacks.parse_consult_remind_when("not-this") is None


def test_typed_intents_route_to_reminder_or_clinic_not_the_llm() -> None:
    from dbaylo.bot.consult_flow import _wants_clinics, _wants_reminder

    # A typed "remind me" must open the reminder flow (the bug: the LLM claimed it couldn't).
    for t in ("зроби нагадування", "нагадай мені про запис", "постав нагадування на 22 липня"):
        assert _wants_reminder(t) and not _wants_clinics(t)
    # An explicit clinic ask routes to the finder, not the reminder flow.
    assert _wants_clinics("де зробити аналіз у Львові") and not _wants_reminder(
        "де зробити аналіз у Львові"
    )
    # A plain medical question is neither — it goes to the normal consult.
    assert not _wants_reminder("що це означає?") and not _wants_clinics("що це означає?")


def test_parse_when_accepts_period_and_iso_and_rejects_past_or_garbage() -> None:
    assert _parse_when("через 2 місяці") is not None  # a relative period
    assert _parse_when("2999-01-01") is not None  # an ISO date in the future
    assert _parse_when("2000-01-01") is None  # a past date is rejected (reminders are future)
    assert _parse_when("колись") is None  # unparseable -> None, never a crash


def test_parse_when_accepts_a_ukrainian_date_and_defaults_to_9am() -> None:
    when = _parse_when("11 грудня 2099")  # day + Ukrainian month + year, far future
    assert when is not None and when.month == 12 and when.day == 11 and when.hour == 9


def test_parse_ukrainian_date_picks_next_occurrence_without_a_year() -> None:
    from datetime import date

    from dbaylo.companion.reminders import parse_ukrainian_date

    today = date(2026, 6, 25)
    assert parse_ukrainian_date("11 липня", today=today) == date(2026, 7, 11)  # later this year
    assert parse_ukrainian_date("3 лютого", today=today) == date(
        2027, 2, 3
    )  # already past -> next yr
    assert parse_ukrainian_date("просто текст", today=today) is None
    assert parse_ukrainian_date("99 жабня", today=today) is None  # bad day / unknown month


def test_render_consult_reminder_names_it_and_is_safe() -> None:
    rem = Reminder(
        user_id=1,
        type=reminders.TYPE_CONSULT,
        schedule="date:2026-09-01T09:00:00",
        payload="УЗД нирок",
    )
    out = reminders.render_reminder(rem)
    assert "УЗД нирок" in out  # the agreed item is named
    assert contains_forbidden_reassurance(out) is None and contains_dose_directive(out) is None


async def test_create_consult_reminder_persists_a_one_off(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    rem = await reminders.create_consult_reminder(
        async_session,
        user=user,
        run_at=datetime(2026, 9, 1, 9, 0),
        label="Консультація уролога",
    )
    assert rem.type == reminders.TYPE_CONSULT
    assert rem.schedule.startswith("date:") and rem.payload == "Консультація уролога"
