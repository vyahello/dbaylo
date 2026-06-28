"""Tier 1.3 navigator FSM: the typed answer is gated exactly like the command arg.

Being in the navigator state is NOT a trusted bypass — a symptom typed into the drug
field short-circuits to triage (no fetch), and a blank answer saves/searches nothing.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Message

from dbaylo import locale
from dbaylo.bot import navigator_flow
from dbaylo.companion import callbacks


@asynccontextmanager
async def _fake_session():
    yield AsyncMock()


async def test_price_options_propose_the_users_meds(monkeypatch) -> None:
    # 💊 Ціна ліків proposes the user's OWN meds for a one-tap price + ✏️ type-another + 📋 manage.
    monkeypatch.setattr(navigator_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        navigator_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7, city=None))
    )
    monkeypatch.setattr(
        navigator_flow,
        "_unique_meds",
        AsyncMock(
            return_value=[
                SimpleNamespace(name="Метформін", dose="850 мг", course="Курс А"),
                SimpleNamespace(name="Аспірин", dose=None, course=None),
            ]
        ),
    )
    message = AsyncMock()
    await navigator_flow.open_price_options(message, AsyncMock(), telegram_id=4242)
    datas = [
        b.callback_data
        for row in message.answer.call_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert callbacks.price_med(0) in datas and callbacks.price_med(1) in datas  # one per med
    assert callbacks.PRICE_TYPE in datas  # ✏️ type another
    assert callbacks.MENU_MED_LIST in datas  # 📋 manage meds (single source of truth)
    assert callbacks.PRICE_CHANGE_CITY in datas  # 📍 set/change the city
    # The med with a recorded strength shows it; the course med is marked ①, standalone 💊.
    labels = [
        b.text
        for row in message.answer.call_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert any("850 мг" in label for label in labels)
    assert any(label.startswith("①") for label in labels)  # the course med is numbered
    # The legend in the message body maps ① to its prescription name.
    body = message.answer.call_args.args[0]
    assert "Курс А" in body and "①" in body


async def test_price_options_fall_back_to_typing_without_meds(monkeypatch) -> None:
    monkeypatch.setattr(navigator_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        navigator_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7, city=None))
    )
    monkeypatch.setattr(navigator_flow, "_unique_meds", AsyncMock(return_value=[]))
    started = {}

    async def fake_start(message, state):
        started["called"] = True

    monkeypatch.setattr(navigator_flow, "start_price_dialog", fake_start)
    await navigator_flow.open_price_options(AsyncMock(), AsyncMock(), telegram_id=4242)
    assert started.get("called")  # no meds -> the type-a-drug dialog


async def test_price_med_tap_prices_by_index_with_the_web_agent(monkeypatch) -> None:
    # Tapping a proposed med runs the smart web-search price lookup, folding in its dosage.
    monkeypatch.setattr(navigator_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        navigator_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7, city="Львів"))
    )
    monkeypatch.setattr(
        navigator_flow,
        "_unique_meds",
        AsyncMock(return_value=[SimpleNamespace(name="Метформін", dose="850 мг")]),
    )
    monkeypatch.setattr(navigator_flow, "_city_for", AsyncMock(return_value="Львів"))
    run_price = AsyncMock(return_value=SimpleNamespace(text="ціни…"))
    monkeypatch.setattr(navigator_flow, "run_price", run_price)
    callback = AsyncMock()
    callback.data = callbacks.price_med(0)
    callback.from_user = SimpleNamespace(id=4242)
    callback.message = AsyncMock(spec=Message)
    callback.message.answer = AsyncMock()
    await navigator_flow.on_price_med(callback, AsyncMock())
    callback.answer.assert_awaited()  # ack first (the search is slow)
    assert run_price.await_args.args[0] == "Метформін"
    assert run_price.await_args.kwargs["use_web_agent"] is True  # smart web-search path
    assert run_price.await_args.kwargs["dose"] == "850 мг"  # the doctor's strength is searched
    assert run_price.await_args.kwargs["city"] == "Львів"  # in the user's city


async def test_price_field_symptom_short_circuits_to_triage() -> None:
    # "температура і озноб" is a kidney-stone red flag — run_price screens BEFORE any
    # fetch, so this never hits the network and never becomes a price search.
    message = AsyncMock()
    message.text = "температура і озноб"
    state = AsyncMock()
    await navigator_flow.on_price_text(message, state)
    state.clear.assert_awaited_once()
    sent = message.answer.call_args.args[0]
    assert "лікар" in sent.lower()  # triage guidance, not a drug listing


async def test_coverage_field_symptom_short_circuits_to_triage() -> None:
    message = AsyncMock()
    message.text = "болить нирка що робити"
    state = AsyncMock()
    await navigator_flow.on_coverage_text(message, state)
    state.clear.assert_awaited_once()
    sent = message.answer.call_args.args[0]
    assert "лікар" in sent.lower()


async def test_blank_price_answer_searches_nothing() -> None:
    message = AsyncMock()
    message.text = "   "
    state = AsyncMock()
    await navigator_flow.on_price_text(message, state)
    state.clear.assert_awaited_once()
    message.answer.assert_awaited_once_with(locale.NOTHING_SAVED)


async def test_city_round_trips_and_ignores_blank(async_session) -> None:
    # The city is asked once and remembered on User.city (reused by price + clinic search).
    from dbaylo.labs.intake import ensure_user, get_city, set_city

    await ensure_user(async_session, telegram_id=555)
    assert await get_city(async_session, telegram_id=555) is None
    await set_city(async_session, telegram_id=555, city="Львів")
    assert await get_city(async_session, telegram_id=555) == "Львів"
    await set_city(async_session, telegram_id=555, city="   ")  # blank is ignored, not cleared
    assert await get_city(async_session, telegram_id=555) == "Львів"


async def test_change_city_persists_a_canonical_form(monkeypatch) -> None:
    # Typing a city in any case form stores the canonical name (cities.parse_city), then re-opens.
    saved = {}

    async def fake_set_city(session, *, telegram_id, city):
        saved["city"] = city

    monkeypatch.setattr(navigator_flow, "get_session", _fake_session)
    monkeypatch.setattr(navigator_flow, "set_city", fake_set_city)
    monkeypatch.setattr(navigator_flow, "open_price_options", AsyncMock())
    message = AsyncMock()
    message.text = "у львові"
    message.from_user = SimpleNamespace(id=4242)
    await navigator_flow.on_city_text(message, AsyncMock())
    assert saved["city"] == "Львів"  # canonicalized from the locative "львові"


async def test_price_thread_starts_and_remembers_drug_and_city(monkeypatch) -> None:
    # A free-form price request starts a remembered conversation (drug + city stored in FSM data).
    monkeypatch.setattr(navigator_flow, "_city_for", AsyncMock(return_value="Львів"))
    ff = AsyncMock(return_value="*Но-шпа*\n• 50 грн\n\nP.S.")
    monkeypatch.setattr(navigator_flow, "find_prices_freeform", ff)
    message = AsyncMock()
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    handled = await navigator_flow.maybe_handle_price(
        message, state, "знайди ціни на Но-шпа", telegram_id=1
    )
    assert handled is True
    assert ff.await_args.kwargs["history"] == []  # first turn, no prior context
    saved = state.update_data.await_args.kwargs
    assert saved["price_city"] == "Львів" and saved["price_ts"]
    assert any(t["text"] == "знайди ціни на Но-шпа" for t in saved["price_transcript"])


async def test_price_followup_continues_with_remembered_context(monkeypatch) -> None:
    # "а дешевше є?" continues the SAME thread — the drug + city come from the stored transcript.
    monkeypatch.setattr(navigator_flow, "_city_for", AsyncMock(return_value=None))
    ff = AsyncMock(return_value="ще дешевше…\n\nP.S.")
    monkeypatch.setattr(navigator_flow, "find_prices_freeform", ff)
    data = {
        "price_transcript": [
            {"role": "user", "text": "ціни на Но-шпа"},
            {"role": "assistant", "text": "50 грн"},
        ],
        "price_city": "Львів",
        "price_ts": navigator_flow._now().isoformat(),
    }
    message = AsyncMock()
    state = AsyncMock()
    state.get_data = AsyncMock(return_value=data)
    handled = await navigator_flow.maybe_handle_price(message, state, "а дешевше є?", telegram_id=1)
    assert handled is True
    assert ff.await_args.kwargs["history"] == [
        ("user", "ціни на Но-шпа"),
        ("assistant", "50 грн"),
    ]
    assert ff.await_args.kwargs["city"] == "Львів"  # remembered, not re-asked


async def test_non_price_text_without_a_thread_is_not_handled() -> None:
    message = AsyncMock()
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    handled = await navigator_flow.maybe_handle_price(message, state, "як справи?", telegram_id=1)
    assert handled is False  # falls back to the companion chat


def test_price_thread_freshness_honours_the_ttl() -> None:
    live = {
        "price_transcript": [{"role": "user", "text": "x"}],
        "price_ts": navigator_flow._now().isoformat(),
    }
    assert navigator_flow.price_thread_fresh(live)
    stale = {
        "price_transcript": [{"role": "user", "text": "x"}],
        "price_ts": "2020-01-01T00:00:00+00:00",
    }
    assert not navigator_flow.price_thread_fresh(stale)
    assert not navigator_flow.price_thread_fresh({})  # nothing stored


async def test_start_price_dialog_is_cancellable() -> None:
    from dbaylo.companion import callbacks

    message = AsyncMock()
    state = AsyncMock()
    await navigator_flow.start_price_dialog(message, state)
    state.set_state.assert_awaited_once_with(navigator_flow.NavStates.waiting_drug)
    _, kwargs = message.answer.call_args
    cancel = kwargs["reply_markup"].inline_keyboard[0][0]
    assert cancel.callback_data == callbacks.CANCEL_DIALOG
