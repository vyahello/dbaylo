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


def test_parse_when_accepts_period_and_iso_and_rejects_past_or_garbage() -> None:
    assert _parse_when("через 2 місяці") is not None  # a relative period
    assert _parse_when("2999-01-01") is not None  # an ISO date in the future
    assert _parse_when("2000-01-01") is None  # a past date is rejected (reminders are future)
    assert _parse_when("колись") is None  # unparseable -> None, never a crash


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
