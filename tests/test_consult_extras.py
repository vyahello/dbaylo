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


def test_primed_recent_window() -> None:
    from datetime import timedelta

    from dbaylo.bot.consult_flow import _PRIME_TTL, _now, _primed_recent

    assert _primed_recent(_now().isoformat())  # just now -> recent
    assert _primed_recent((_now() - _PRIME_TTL + timedelta(minutes=1)).isoformat())  # inside window
    assert not _primed_recent((_now() - _PRIME_TTL - timedelta(minutes=1)).isoformat())  # stale
    assert not _primed_recent("not-a-date") and not _primed_recent(None)


class _FakeState:
    def __init__(self, data: dict) -> None:
        self._data = data

    async def get_data(self) -> dict:
        return self._data


async def test_start_primed_consult_skips_when_nothing_recent_is_primed() -> None:
    # No prime / a stale prime -> returns False so the companion gives an ordinary reply (it never
    # touches `message` / `scheduler` in that case).
    from datetime import timedelta

    from dbaylo.bot import consult_flow

    assert not await consult_flow.start_primed_consult(None, _FakeState({}), scheduler=None)  # type: ignore[arg-type]
    stale = {
        "consult_primed": {"kind": "indicator", "report_id": 0, "key": "k", "name": "Глюкоза"},
        "consult_primed_ts": (consult_flow._now() - timedelta(hours=1)).isoformat(),
    }
    assert not await consult_flow.start_primed_consult(None, _FakeState(stale), scheduler=None)  # type: ignore[arg-type]


# --- #3: smart routing of a data question into a focused indicator consult --------


def _routing_message(text: str):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    message = AsyncMock()
    message.text = text
    message.from_user = SimpleNamespace(id=4242)
    message.chat = SimpleNamespace(id=4242)
    return message


def _patch_routing(monkeypatch, *, indicators):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from dbaylo.bot import consult_flow

    @asynccontextmanager
    async def _fake_session():
        yield AsyncMock()

    monkeypatch.setattr(consult_flow, "get_session", _fake_session)
    monkeypatch.setattr(consult_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7)))
    monkeypatch.setattr(consult_flow.health, "list_indicators", AsyncMock(return_value=indicators))
    monkeypatch.setattr(consult_flow, "build_context", AsyncMock(return_value=("CTX", "Залізо")))
    run = AsyncMock()
    monkeypatch.setattr(consult_flow, "_run_consult_turn", run)
    return run


async def test_data_question_routes_to_a_focused_indicator_consult(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from dbaylo.bot import consult_flow
    from dbaylo.companion.health import HealthFinding

    finding = HealthFinding(
        name="Залізо",
        value="",
        ref="",
        flag_text="",
        direction="STABLE",
        last_date=None,
        n_points=2,
        series_key="blood\x1fзалізо",
    )
    run = _patch_routing(monkeypatch, indicators=[finding])
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})

    handled = await consult_flow.start_data_question_consult(
        _routing_message("чому в мене низьке залізо?"), state, scheduler=AsyncMock()
    )

    assert handled is True
    run.assert_awaited_once()  # the consult answered the question as its first turn
    subject = state.update_data.await_args.kwargs["consult_subject"]
    assert subject["key"] == "blood\x1fзалізо"  # anchored to THAT indicator


async def test_non_question_is_not_routed(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from dbaylo.bot import consult_flow

    run = _patch_routing(monkeypatch, indicators=[])  # list_indicators must not even be needed
    handled = await consult_flow.start_data_question_consult(
        _routing_message("сьогодні приймав залізо"), AsyncMock(), scheduler=AsyncMock()
    )
    assert handled is False
    run.assert_not_awaited()


async def test_question_without_a_matching_indicator_is_not_routed(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from dbaylo.bot import consult_flow
    from dbaylo.companion.health import HealthFinding

    finding = HealthFinding(
        name="Залізо",
        value="",
        ref="",
        flag_text="",
        direction="STABLE",
        last_date=None,
        n_points=1,
        series_key="fe",
    )
    run = _patch_routing(monkeypatch, indicators=[finding])
    handled = await consult_flow.start_data_question_consult(
        _routing_message("чому я постійно втомлений?"), AsyncMock(), scheduler=AsyncMock()
    )
    assert handled is False  # a generic complaint with no named indicator -> ordinary chat
    run.assert_not_awaited()


def test_booking_lead_fires_well_before_a_far_visit_and_clamps_a_near_one() -> None:
    # A booking reminder fires several days before the visit (the slot isn't arranged yet — time to
    # call and agree); if the visit is too soon, it clamps to "soon", never after the visit.
    from datetime import timedelta

    from dbaylo.bot.consult_flow import _BOOKING_LEAD_DAYS, _booking_lead, _now

    assert _BOOKING_LEAD_DAYS >= 4  # enough runway to actually call + arrange
    far = _now() + timedelta(days=30)
    assert _booking_lead(far) == far - timedelta(days=_BOOKING_LEAD_DAYS)

    near = _now() + timedelta(hours=20)  # sooner than the lead
    lead_near = _booking_lead(near)
    assert _now() < lead_near <= near  # clamped to soon, never after the visit


def test_booking_requests_route_to_the_reminder_flow() -> None:
    # "запиши мене …" can't be a real booking, so it must reach the reminder flow (which saves it +
    # nudges to call), instead of the LLM repeating "I can't book you".
    from dbaylo.bot.consult_flow import _wants_booking

    for t in (
        "запиши мене на Огієнка у Львові Уросвіт 11 липня",
        "запиши мене на консультацію і УЗД нирок на 11 липня в Уросвіт",
        "зможеш записати мене в уросвіт на узд",
        "забронюй на 3 вересня",
    ):
        assert _wants_booking(t)
    # A pure advice question is NOT a booking (so it still gets a real consult answer).
    assert not _wants_booking("що порадиш робити з каменями?")
    assert not _wants_booking("запиши собі на майбутнє")  # not "мене/на/до" -> not a booking


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
