"""Tier 1.3 navigator FSM: the typed answer is gated exactly like the command arg.

Being in the navigator state is NOT a trusted bypass — a symptom typed into the drug
field short-circuits to triage (no fetch), and a blank answer saves/searches nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from dbaylo import locale
from dbaylo.bot import navigator_flow


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
