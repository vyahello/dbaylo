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
    # 💊 Ціна ліків proposes the user's OWN meds for a one-tap price + ✏️ type-another.
    monkeypatch.setattr(navigator_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        navigator_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(
        navigator_flow,
        "_unique_meds",
        AsyncMock(
            return_value=[SimpleNamespace(name="Метформін"), SimpleNamespace(name="Аспірин")]
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


async def test_price_options_fall_back_to_typing_without_meds(monkeypatch) -> None:
    monkeypatch.setattr(navigator_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        navigator_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(navigator_flow, "_unique_meds", AsyncMock(return_value=[]))
    started = {}

    async def fake_start(message, state):
        started["called"] = True

    monkeypatch.setattr(navigator_flow, "start_price_dialog", fake_start)
    await navigator_flow.open_price_options(AsyncMock(), AsyncMock(), telegram_id=4242)
    assert started.get("called")  # no meds -> the type-a-drug dialog


async def test_price_med_tap_prices_by_index_with_the_llm_fallback(monkeypatch) -> None:
    # Tapping a proposed med runs the gated price lookup WITH the LLM re-parse fallback on.
    monkeypatch.setattr(navigator_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        navigator_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(
        navigator_flow, "_unique_meds", AsyncMock(return_value=[SimpleNamespace(name="Метформін")])
    )
    run_price = AsyncMock(return_value=SimpleNamespace(text="ціни…"))
    monkeypatch.setattr(navigator_flow, "run_price", run_price)
    callback = AsyncMock()
    callback.data = callbacks.price_med(0)
    callback.from_user = SimpleNamespace(id=4242)
    callback.message = AsyncMock(spec=Message)
    callback.message.answer = AsyncMock()
    await navigator_flow.on_price_med(callback, AsyncMock())
    callback.answer.assert_awaited()  # ack first (the fetch is slow)
    assert run_price.await_args.args[0] == "Метформін"
    assert run_price.await_args.kwargs["use_llm_fallback"] is True  # fallback ON


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


async def test_start_price_dialog_is_cancellable() -> None:
    from dbaylo.companion import callbacks

    message = AsyncMock()
    state = AsyncMock()
    await navigator_flow.start_price_dialog(message, state)
    state.set_state.assert_awaited_once_with(navigator_flow.NavStates.waiting_drug)
    _, kwargs = message.answer.call_args
    cancel = kwargs["reply_markup"].inline_keyboard[0][0]
    assert cancel.callback_data == callbacks.CANCEL_DIALOG
