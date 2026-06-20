"""Tier 1.3 button menu: keyboard layout, section screens, and delegating callbacks.

The menu is a UI layer — handlers route to the *reused* flow helpers and never embed
domain logic. Tests call the handlers directly with mocks and assert delegation +
the inline action wiring.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardMarkup, Message

from dbaylo import locale
from dbaylo.bot import menu_flow
from dbaylo.bot.keyboards import cancel_keyboard, main_menu_keyboard, section_keyboard
from dbaylo.companion import callbacks


def _cb_datas(markup: InlineKeyboardMarkup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _callback(data: str | None = None) -> AsyncMock:
    callback = AsyncMock()
    callback.data = data
    callback.from_user = SimpleNamespace(id=4242)
    callback.message = AsyncMock(spec=Message)  # spec => passes isinstance(_, Message)
    callback.message.answer = AsyncMock()  # spec doesn't mark .answer awaitable; do it here
    return callback


# --- Keyboards ------------------------------------------------------------------


def test_main_menu_keyboard_layout() -> None:
    kb = main_menu_keyboard()
    labels = [b.text for row in kb.keyboard for b in row]
    assert labels == [
        locale.MENU_LABS,
        locale.MENU_GOALS,
        locale.MENU_PROBLEMS,
        locale.MENU_MEDS,
        locale.MENU_REMINDERS,
        locale.MENU_PRICES,
        locale.MENU_CHECKIN,
        locale.MENU_HELP,
    ]
    assert kb.is_persistent and kb.resize_keyboard
    assert len(kb.keyboard) == 4 and len(kb.keyboard[-1]) == 2  # two-per-row, checkin+help last


def test_menu_labels_set_is_the_keyboard_labels() -> None:
    assert {
        locale.MENU_LABS,
        locale.MENU_GOALS,
        locale.MENU_PROBLEMS,
        locale.MENU_MEDS,
        locale.MENU_REMINDERS,
        locale.MENU_PRICES,
        locale.MENU_CHECKIN,
        locale.MENU_HELP,
    } == locale.MENU_LABELS


def test_cancel_keyboard_carries_the_shared_cancel_callback() -> None:
    assert _cb_datas(cancel_keyboard()) == [callbacks.CANCEL_DIALOG]


def test_section_keyboard_one_button_per_row() -> None:
    kb = section_keyboard(("a", "x"), ("b", "y"))
    assert [len(row) for row in kb.inline_keyboard] == [1, 1]
    assert _cb_datas(kb) == ["x", "y"]


# --- Section screens (reply-keyboard label taps) --------------------------------


async def test_menu_labs_offers_history() -> None:
    message = AsyncMock()
    await menu_flow.menu_labs(message)
    _, kwargs = message.answer.call_args
    assert _cb_datas(kwargs["reply_markup"]) == [callbacks.MENU_OPEN_HISTORY]


async def test_menu_goals_offers_list_and_new() -> None:
    message = AsyncMock()
    await menu_flow.menu_goals(message)
    _, kwargs = message.answer.call_args
    assert _cb_datas(kwargs["reply_markup"]) == [callbacks.MENU_GOALS_LIST, callbacks.MENU_GOAL_NEW]


async def test_menu_prices_offers_price_and_coverage() -> None:
    message = AsyncMock()
    await menu_flow.menu_prices(message)
    _, kwargs = message.answer.call_args
    assert _cb_datas(kwargs["reply_markup"]) == [callbacks.MENU_PRICE, callbacks.MENU_COVERAGE]


async def test_menu_checkin_starts_the_checkin_dialog(monkeypatch) -> None:
    seen = {}

    async def fake(message, state):
        seen["args"] = (message, state)

    monkeypatch.setattr(menu_flow.companion_flow, "start_checkin_dialog", fake)
    message = AsyncMock()
    state = object()
    await menu_flow.menu_checkin(message, state)
    assert seen["args"] == (message, state)


async def test_menu_reminders_delegates_to_open_reminders(monkeypatch) -> None:
    seen = {}

    async def fake(message, telegram_id, scheduler):
        seen["args"] = (message, telegram_id, scheduler)

    monkeypatch.setattr(menu_flow.proactive_flow, "open_reminders", fake)
    message = AsyncMock()
    message.from_user = SimpleNamespace(id=4242)
    scheduler = object()
    await menu_flow.menu_reminders(message, scheduler)
    assert seen["args"] == (message, 4242, scheduler)


# --- Section inline-button callbacks (delegate to reused helpers) ---------------


async def test_cb_open_history_delegates(monkeypatch) -> None:
    seen = {}

    async def fake(message, telegram_id, raw=""):
        seen["args"] = (message, telegram_id)

    monkeypatch.setattr(menu_flow.history_flow, "render_history", fake)
    callback = _callback(callbacks.MENU_OPEN_HISTORY)
    await menu_flow.cb_open_history(callback)
    assert seen["args"] == (callback.message, 4242)
    callback.answer.assert_awaited_once()


async def test_cb_goal_new_starts_the_dialog(monkeypatch) -> None:
    seen = {}

    async def fake(message, state):
        seen["args"] = (message, state)

    monkeypatch.setattr(menu_flow.companion_flow, "start_goal_dialog", fake)
    callback = _callback(callbacks.MENU_GOAL_NEW)
    state = AsyncMock()
    await menu_flow.cb_goal_new(callback, state)
    assert seen["args"] == (callback.message, state)
    callback.answer.assert_awaited_once()


async def test_cb_med_new_starts_the_dialog(monkeypatch) -> None:
    seen = {}

    async def fake(message, state):
        seen["args"] = (message, state)

    monkeypatch.setattr(menu_flow.proactive_flow, "start_medication_dialog", fake)
    callback = _callback(callbacks.MENU_MED_NEW)
    state = AsyncMock()
    await menu_flow.cb_med_new(callback, state)
    assert seen["args"] == (callback.message, state)


async def test_cb_price_starts_the_dialog(monkeypatch) -> None:
    seen = {}

    async def fake(message, state):
        seen["args"] = (message, state)

    monkeypatch.setattr(menu_flow.navigator_flow, "start_price_dialog", fake)
    callback = _callback(callbacks.MENU_PRICE)
    state = AsyncMock()
    await menu_flow.cb_price(callback, state)
    assert seen["args"] == (callback.message, state)


# --- The single shared cancel ---------------------------------------------------


async def test_cancel_dialog_clears_any_state_and_saves_nothing() -> None:
    callback = _callback(callbacks.CANCEL_DIALOG)
    state = AsyncMock()
    await menu_flow.cb_cancel_dialog(callback, state)
    state.clear.assert_awaited_once()
    callback.message.answer.assert_awaited_once_with(locale.DIALOG_CANCELLED)
    callback.answer.assert_awaited_once()
