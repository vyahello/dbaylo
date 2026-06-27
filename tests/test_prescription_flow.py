"""Prescription flow: confirm rendering + the confirm handler creating meds (dose stored, not in
the reminder), and a DB check that add_medication persists the dose as record-keeping."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.bot import prescription_flow
from dbaylo.labs.prescription import ExtractedMedication


def _med(name, dose=None, times=(), frequency=None) -> ExtractedMedication:
    return ExtractedMedication(name=name, dose=dose, times=times, frequency=frequency)


def test_with_resolved_times_spreads_a_frequency_only_med() -> None:
    # A doctor writes "двічі на день", not hours — the bot fills the times so the med is scheduled,
    # not skipped for manual entry. Explicit-times (or no usable frequency) is left unchanged.
    spread = prescription_flow._with_resolved_times(_med("Сироп", frequency="двічі на день"))
    assert spread.times == ("09:00", "21:00")
    explicit = _med("Аспірин", times=("08:00", "20:00"), frequency="двічі")
    assert prescription_flow._with_resolved_times(explicit).times == ("08:00", "20:00")  # untouched
    vague = _med("Мазь", frequency="за потреби")  # no parseable N/day -> still manual
    assert prescription_flow._with_resolved_times(vague).times == ()


def test_render_confirm_shows_dose_and_times_and_flags_missing_time() -> None:
    text = prescription_flow._render_confirm(
        [
            _med("Аспірин", dose="500 мг", times=("08:00", "20:00")),
            _med("Сироп", dose="10 мл", frequency="двічі на день"),
        ]
    )
    assert "Аспірин" in text and "500 мг" in text and "08:00, 20:00" in text
    assert "Сироп" in text and locale.PRESCRIPTION_LINE_NO_TIME in text  # no clock time -> flagged


def test_result_text_created_and_skipped() -> None:
    assert prescription_flow._result_text(["Аспірин"], []) == locale.PRESCRIPTION_SAVED.format(
        names="Аспірин"
    )
    both = prescription_flow._result_text(["Аспірин"], ["Сироп"])
    assert "Аспірин" in both and "Сироп" in both
    assert prescription_flow._result_text([], ["Сироп"]) == locale.PRESCRIPTION_NOTHING_SAVED


def test_state_roundtrip_preserves_fields() -> None:
    med = _med("Метформін", dose="850 мг", times=("09:00",), frequency=None)
    restored = prescription_flow._med_from_state(prescription_flow._med_to_state(med))
    assert restored == med


async def test_confirm_creates_timed_meds_with_dose_and_skips_untimed(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_session():
        yield AsyncMock()

    monkeypatch.setattr(prescription_flow, "get_session", fake_session)
    monkeypatch.setattr(
        prescription_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    add = AsyncMock(return_value=(SimpleNamespace(id=1), []))
    monkeypatch.setattr(prescription_flow.proactive, "add_medication", add)

    state = AsyncMock()
    state.get_data = AsyncMock(
        return_value={
            "meds": [
                prescription_flow._med_to_state(_med("Аспірин", dose="500 мг", times=("08:00",))),
                prescription_flow._med_to_state(_med("Сироп", frequency="двічі")),  # no time
            ]
        }
    )
    callback = AsyncMock()
    callback.from_user = SimpleNamespace(id=4242)
    callback.message = AsyncMock(spec=Message)  # passes isinstance(_, Message)
    callback.message.answer = AsyncMock()
    callback.message.edit_reply_markup = AsyncMock()  # spec doesn't mark it awaitable

    await prescription_flow.on_prescription_confirm(callback, state, reminder_scheduler=object())

    add.assert_awaited_once()  # only the timed med is created
    assert add.await_args.kwargs["name"] == "Аспірин"
    assert add.await_args.kwargs["dose"] == "500 мг"  # dose stored as record
    assert add.await_args.kwargs["times"] == [time(8, 0)]
    sent = callback.message.answer.call_args.args[0]
    assert "Аспірин" in sent and "Сироп" in sent  # created + skipped both reported


async def test_add_medication_persists_dose_but_reminder_text_has_none(
    async_session: AsyncSession,
) -> None:
    from dbaylo.companion import medications, reminders
    from dbaylo.db.models import User

    user = User(telegram_id=99, name="Owner")
    async_session.add(user)
    await async_session.flush()

    med, created = await medications.add_medication(
        async_session, user=user, name="Аспірин", times=[time(8, 0)], dose="500 мг"
    )
    assert med.dose == "500 мг"  # stored as record-keeping (rail #1 allows it)
    # The reminder text names the drug and defers to the doctor — never the dose.
    body = reminders.render_reminder(created[0])
    assert "Аспірин" in body and "500" not in body and "мг" not in body
