"""Tier 1.3 button menu: keyboard layout, section screens, and delegating callbacks.

The menu is a UI layer — handlers route to the *reused* flow helpers and never embed
domain logic. Tests call the handlers directly with mocks and assert delegation +
the inline action wiring.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from dbaylo import locale
from dbaylo.bot import menu_flow, proactive_flow
from dbaylo.bot.keyboards import (
    cancel_keyboard,
    clear_inline_keyboard,
    main_menu_keyboard,
    remove_button_row,
    section_keyboard,
)
from dbaylo.companion import callbacks


def _cb_datas(markup: InlineKeyboardMarkup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _callback(data: str | None = None) -> AsyncMock:
    callback = AsyncMock()
    callback.data = data
    callback.from_user = SimpleNamespace(id=4242)
    callback.message = AsyncMock(spec=Message)  # spec => passes isinstance(_, Message)
    callback.message.answer = AsyncMock()  # spec doesn't mark .answer awaitable; do it here
    callback.message.edit_reply_markup = AsyncMock()  # one-shot handlers clear the keyboard
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
        locale.MENU_MEMORY,
        locale.MENU_CHECKIN,
        locale.MENU_HELP,
    ]
    assert kb.is_persistent and kb.resize_keyboard
    assert len(kb.keyboard) == 5 and len(kb.keyboard[-1]) == 1  # memory+checkin, then help alone


def test_menu_labels_set_is_the_keyboard_labels() -> None:
    assert {
        locale.MENU_LABS,
        locale.MENU_GOALS,
        locale.MENU_PROBLEMS,
        locale.MENU_MEDS,
        locale.MENU_REMINDERS,
        locale.MENU_PRICES,
        locale.MENU_MEMORY,
        locale.MENU_CHECKIN,
        locale.MENU_HELP,
    } == locale.MENU_LABELS


def test_cancel_keyboard_carries_the_shared_cancel_callback() -> None:
    assert _cb_datas(cancel_keyboard()) == [callbacks.CANCEL_DIALOG]


async def test_clear_inline_keyboard_removes_the_buttons() -> None:
    callback = _callback()
    await clear_inline_keyboard(callback)
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)


async def test_clear_inline_keyboard_swallows_telegram_errors() -> None:
    callback = _callback()
    callback.message.edit_reply_markup = AsyncMock(
        side_effect=TelegramBadRequest(method=SimpleNamespace(), message="message can't be edited")
    )
    await clear_inline_keyboard(callback)  # a stale/uneditable message must not raise


async def test_clear_inline_keyboard_noop_without_a_message() -> None:
    callback = AsyncMock()
    callback.message = None
    await clear_inline_keyboard(callback)  # nothing to clear, must not crash


async def test_remove_button_row_drops_only_the_tapped_row() -> None:
    callback = _callback("prob_resolve:2")
    callback.message.reply_markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="A", callback_data="prob_resolve:1")],
            [InlineKeyboardButton(text="B", callback_data="prob_resolve:2")],
            [InlineKeyboardButton(text="C", callback_data="prob_resolve:3")],
        ]
    )
    await remove_button_row(callback)
    _, kwargs = callback.message.edit_reply_markup.call_args
    remaining = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert remaining == ["prob_resolve:1", "prob_resolve:3"]  # only the tapped row removed


async def test_remove_button_row_clears_when_last_row_goes() -> None:
    callback = _callback("prob_resolve:1")
    callback.message.reply_markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="A", callback_data="prob_resolve:1")]]
    )
    await remove_button_row(callback)
    _, kwargs = callback.message.edit_reply_markup.call_args
    assert kwargs["reply_markup"] is None  # nothing left -> keyboard cleared


def test_section_keyboard_one_button_per_row() -> None:
    kb = section_keyboard(("a", "x"), ("b", "y"))
    assert [len(row) for row in kb.inline_keyboard] == [1, 1]
    assert _cb_datas(kb) == ["x", "y"]


# --- Section screens (reply-keyboard label taps) --------------------------------


async def test_menu_labs_splits_history_and_dynamics() -> None:
    # "Аналізи" offers TWO distinct destinations as separate buttons: the saved-reports
    # history and the cross-lab dynamics browser.
    message = AsyncMock()
    await menu_flow.menu_labs(message)
    _, kwargs = message.answer.call_args
    assert _cb_datas(kwargs["reply_markup"]) == [callbacks.MENU_OPEN_HISTORY, callbacks.DYN_OPEN]


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


async def test_menu_memory_delegates_to_open_memory_view(monkeypatch) -> None:
    seen = {}

    async def fake(message, telegram_id):
        seen["args"] = (message, telegram_id)

    monkeypatch.setattr(menu_flow.consult_flow, "open_memory_view", fake)
    message = AsyncMock()
    message.from_user = SimpleNamespace(id=4242)
    await menu_flow.menu_memory(message)
    assert seen["args"] == (message, 4242)


async def test_open_problems_is_one_message_with_a_row_per_concern(monkeypatch) -> None:
    # No flood: the problems list is ONE message, one [✅ name][✏️] row per concern (so resolving
    # removes just that row), instead of a separate message per concern.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_session():
        yield object()

    concerns = [SimpleNamespace(id=1, name="Болить спина"), SimpleNamespace(id=2, name="Тиск")]
    monkeypatch.setattr(proactive_flow, "get_session", fake_session)
    monkeypatch.setattr(
        proactive_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(proactive_flow.concerns, "list_active", AsyncMock(return_value=concerns))
    message = AsyncMock()
    await proactive_flow.open_problems(message, telegram_id=4242)
    message.answer.assert_awaited_once()  # ONE message, not one per concern
    _, kwargs = message.answer.call_args
    rows = kwargs["reply_markup"].inline_keyboard
    assert len(rows) == 2  # one row per concern
    datas = [b.callback_data for row in rows for b in row]
    assert callbacks.problem_resolve(1) in datas and callbacks.problem_resolve(2) in datas
    assert "Болить спина" in rows[0][0].text  # the name is IN the resolve button


# --- Section inline-button callbacks (delegate to reused helpers) ---------------


async def test_cb_open_history_edits_in_place(monkeypatch) -> None:
    # "Переглянути історію" edits the hub message into the list in place (so the list's ◀ Назад
    # returns to the hub in the same message).
    seen = {}

    async def fake(callback, telegram_id):
        seen["args"] = (callback, telegram_id)

    monkeypatch.setattr(menu_flow.history_flow, "open_history_in_place", fake)
    callback = _callback(callbacks.MENU_OPEN_HISTORY)
    await menu_flow.cb_open_history(callback)
    assert seen["args"] == (callback, 4242)


async def test_cb_open_labs_returns_to_the_hub() -> None:
    # The list's ◀ Назад re-renders the two-button "Аналізи" hub by editing in place.
    callback = _callback(callbacks.MENU_OPEN_LABS)
    callback.message.edit_text = AsyncMock()
    await menu_flow.cb_open_labs(callback)
    _, kwargs = callback.message.edit_text.call_args
    assert _cb_datas(kwargs["reply_markup"]) == [callbacks.MENU_OPEN_HISTORY, callbacks.DYN_OPEN]
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
