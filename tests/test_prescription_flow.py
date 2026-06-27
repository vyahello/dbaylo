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


def test_group_by_course_splits_grouped_and_ungrouped() -> None:
    from dbaylo.bot import proactive_flow

    m1 = SimpleNamespace(id=1, name="Симода", course="Рецепт", source_file="/x")
    m2 = SimpleNamespace(id=2, name="Соннат", course="Рецепт", source_file="/x")
    m3 = SimpleNamespace(id=3, name="Магній", course=None, source_file=None)  # manual -> ungrouped
    grouped, ungrouped = proactive_flow._group_by_course([m1, m2, m3])
    assert grouped == [("Рецепт", [m1, m2])]  # a prescription's meds are ONE group
    assert ungrouped == [m3]


def test_course_card_lists_its_meds_and_shares_one_photo() -> None:
    from dbaylo.bot import proactive_flow
    from dbaylo.companion import callbacks

    meds = [
        SimpleNamespace(name="Симода", schedule="09:00", until=None),
        SimpleNamespace(name="Буспірон", schedule="08:00, 14:00, 20:00", until=None),
    ]
    card = proactive_flow._course_card("Заспокійливі", meds)
    assert "Заспокійливі" in card and "Симода" in card and "08:00, 14:00, 20:00" in card
    kb = proactive_flow._course_card_keyboard(7, "r", has_file=True)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert callbacks.course_file(7, "r") in datas  # ONE shared photo for the group
    assert callbacks.course_off(7, "r") in datas  # turn off the WHOLE course
    no_photo = proactive_flow._course_card_keyboard(7, "r", has_file=False)
    datas2 = [b.callback_data for row in no_photo.inline_keyboard for b in row]
    assert not any(d.startswith(callbacks.COURSE_FILE) for d in datas2)


def test_med_card_keyboard_shows_the_file_button_only_with_a_photo() -> None:
    from dbaylo.bot import proactive_flow
    from dbaylo.companion import callbacks

    with_photo = proactive_flow._med_card_keyboard(7, "m", has_file=True)
    datas = [b.callback_data for row in with_photo.inline_keyboard for b in row]
    assert callbacks.medication_file(7, "m") in datas  # 📄 opens the prescription photo

    no_photo = proactive_flow._med_card_keyboard(7, "m", has_file=False)
    datas = [b.callback_data for row in no_photo.inline_keyboard for b in row]
    assert not any(d.startswith(callbacks.MEDICATION_FILE) for d in datas)


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


def test_render_confirm_shows_the_course_group() -> None:
    text = prescription_flow._render_confirm(
        [_med("Буспірон", times=("08:00",))], course="Рецепт від уролога"
    )
    assert "Рецепт від уролога" in text  # the meds are filed under a named prescription group


async def test_typed_message_renames_the_course(monkeypatch) -> None:
    # While confirming, a typed message renames the prescription GROUP (the agent's default → the
    # user's own words), then re-shows the confirm.
    state = AsyncMock()
    state.get_data = AsyncMock(
        return_value={"meds": [prescription_flow._med_to_state(_med("Х", times=("08:00",)))]}
    )
    message = AsyncMock()
    message.text = "Уролог, червень"
    await prescription_flow.on_prescription_course(message, state)
    state.update_data.assert_awaited_once_with(course="Уролог, червень")
    assert "Уролог, червень" in message.answer.call_args.args[0]


async def test_confirm_files_meds_under_the_course_and_photo(monkeypatch) -> None:
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
            "meds": [prescription_flow._med_to_state(_med("Буспірон", times=("08:00",)))],
            "course": "Рецепт від уролога",
            "rx_path": "/data/rx/1.jpg",
        }
    )
    callback = AsyncMock()
    callback.from_user = SimpleNamespace(id=4242)
    callback.message = AsyncMock(spec=Message)
    callback.message.answer = AsyncMock()
    callback.message.edit_reply_markup = AsyncMock()  # spec doesn't mark it awaitable
    await prescription_flow.on_prescription_confirm(callback, state, reminder_scheduler=object())
    assert add.await_args.kwargs["course"] == "Рецепт від уролога"
    assert add.await_args.kwargs["source_file"] == "/data/rx/1.jpg"


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
    # The result navigates forward (no dead-end): jump to 💊 Мої ліки / 🔔 Нагадування.
    from dbaylo.companion import callbacks as cb

    kb = callback.message.answer.call_args.kwargs.get("reply_markup")
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert cb.MENU_MED_LIST in datas and cb.MENU_OPEN_REMINDERS in datas


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
