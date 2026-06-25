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
    # ~5 agent-driven sections: 🩺 Моє здоровʼя leads alone, then care+prices, then memory+help.
    kb = main_menu_keyboard()
    labels = [b.text for row in kb.keyboard for b in row]
    assert labels == [
        locale.MENU_HEALTH,
        locale.MENU_CARE,
        locale.MENU_PRICES,
        locale.MENU_MEMORY,
        locale.MENU_HELP,
    ]
    assert kb.is_persistent and kb.resize_keyboard
    assert len(kb.keyboard) == 3 and len(kb.keyboard[0]) == 1  # Моє здоровʼя on its own row


def test_menu_labels_covers_keyboard_and_legacy_labels() -> None:
    # MENU_LABELS resets a dialog on a tap: it must include every CURRENT keyboard label and the
    # legacy ones (so a cached old keyboard still aborts a dialog too).
    keyboard = {b.text for row in main_menu_keyboard().keyboard for b in row}
    assert keyboard <= locale.MENU_LABELS  # every keyboard label is a reset trigger
    assert locale.MENU_HEALTH in locale.MENU_LABELS and locale.MENU_CARE in locale.MENU_LABELS
    assert locale.MENU_LABS in locale.MENU_LABELS  # a legacy label still resets a dialog


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


# --- The ~5-section hubs (🩺 Моє здоровʼя · 💊 Ліки й нагадування) ----------------


async def test_menu_health_hub_offers_the_four_destinations() -> None:
    message = AsyncMock()
    await menu_flow.menu_health(message)
    _, kwargs = message.answer.call_args
    assert _cb_datas(kwargs["reply_markup"]) == [
        callbacks.MENU_OPEN_ANALYSES,
        callbacks.MENU_PROB_LIST,
        callbacks.MENU_OPEN_GOALS,
        callbacks.MENU_OPEN_CHECKIN,
    ]


async def test_menu_care_hub_bundles_meds_and_reminders() -> None:
    message = AsyncMock()
    await menu_flow.menu_care(message)
    _, kwargs = message.answer.call_args
    assert _cb_datas(kwargs["reply_markup"]) == [
        callbacks.MENU_MED_LIST,
        callbacks.MENU_MED_NEW,
        callbacks.MENU_OPEN_REMINDERS,
    ]


async def test_cb_open_analyses_posts_the_labs_hub() -> None:
    callback = _callback(callbacks.MENU_OPEN_ANALYSES)
    await menu_flow.cb_open_analyses(callback)
    _, kwargs = callback.message.answer.call_args
    assert _cb_datas(kwargs["reply_markup"]) == [callbacks.MENU_OPEN_HISTORY, callbacks.DYN_OPEN]
    callback.answer.assert_awaited_once()


async def test_cb_open_goals_posts_the_goals_section() -> None:
    callback = _callback(callbacks.MENU_OPEN_GOALS)
    await menu_flow.cb_open_goals(callback)
    _, kwargs = callback.message.answer.call_args
    assert _cb_datas(kwargs["reply_markup"]) == [callbacks.MENU_GOALS_LIST, callbacks.MENU_GOAL_NEW]


async def test_cb_open_checkin_passes_the_owner_tg(monkeypatch) -> None:
    # The prompt is sent on a callback message (from_user = bot), so the owner's tg is threaded
    # explicitly — otherwise the grounded check-in loads the wrong user.
    seen = {}

    async def fake(message, state, *, telegram_id):
        seen["args"] = (message, state, telegram_id)

    monkeypatch.setattr(menu_flow.companion_flow, "start_checkin_dialog", fake)
    callback = _callback(callbacks.MENU_OPEN_CHECKIN)
    state = object()
    await menu_flow.cb_open_checkin(callback, state)
    assert seen["args"] == (callback.message, state, 4242)


async def test_cb_open_reminders_delegates_with_owner_tg(monkeypatch) -> None:
    seen = {}

    async def fake(message, telegram_id, scheduler):
        seen["args"] = (message, telegram_id, scheduler)

    monkeypatch.setattr(menu_flow.proactive_flow, "open_reminders", fake)
    callback = _callback(callbacks.MENU_OPEN_REMINDERS)
    scheduler = object()
    await menu_flow.cb_open_reminders(callback, scheduler)
    assert seen["args"] == (callback.message, 4242, scheduler)


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


async def test_open_problems_proposes_findings_and_lists_tracked(monkeypatch) -> None:
    # The agent's read in ONE message: AI-proposed findings (👁 track / ✖ dismiss), then the
    # already-tracked concerns (✅ resolve / ✏️), then a manual-add fallback.
    from contextlib import asynccontextmanager

    from dbaylo.companion.health import HealthFinding

    @asynccontextmanager
    async def fake_session():
        yield object()

    proposals = [
        HealthFinding(
            name="Гемоглобін (HGB)",
            value="169 г/л",
            ref="130–160",
            flag_text="above",
            direction="LEFT_RANGE",
            last_date=None,
            n_points=2,
            kind="high",
        )
    ]
    concerns = [SimpleNamespace(id=1, name="Болить спина")]
    monkeypatch.setattr(proactive_flow, "get_session", fake_session)
    monkeypatch.setattr(
        proactive_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(
        proactive_flow.health, "propose_problems", AsyncMock(return_value=proposals)
    )
    monkeypatch.setattr(proactive_flow.concerns, "list_active", AsyncMock(return_value=concerns))
    message = AsyncMock()
    await proactive_flow.open_problems(message, telegram_id=4242)
    message.answer.assert_awaited_once()  # ONE message, not one per finding/concern
    text = message.answer.call_args.args[0]
    rows = message.answer.call_args.kwargs["reply_markup"].inline_keyboard
    datas = [b.callback_data for row in rows for b in row]
    # The proposed finding gets a track+dismiss pair; the tracked concern keeps resolve+rename.
    assert callbacks.problem_track(0) in datas and callbacks.problem_dismiss(0) in datas
    assert callbacks.problem_resolve(1) in datas
    assert callbacks.MENU_PROB_NEW in datas  # manual "➕ Своя проблема" fallback is always there
    assert "Гемоглобін (HGB)" in text  # the finding is described, value + norm, in the body


def _proposal_finding(name: str = "Глюкоза"):
    from dbaylo.companion.health import HealthFinding

    return HealthFinding(
        name=name,
        value="7 ммоль/л",
        ref="3.9–6.1",
        flag_text="above",
        direction="LEFT_RANGE",
        last_date=None,
        n_points=1,
        kind="high",
    )


def _patch_problems(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_session():
        yield AsyncMock()  # has an awaitable .commit()

    monkeypatch.setattr(proactive_flow, "get_session", fake_session)
    monkeypatch.setattr(
        proactive_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(
        proactive_flow.health,
        "propose_problems",
        AsyncMock(return_value=[_proposal_finding()]),
    )
    monkeypatch.setattr(proactive_flow.concerns, "list_active", AsyncMock(return_value=[]))


async def test_problem_track_creates_concern_by_index_and_refreshes(monkeypatch) -> None:
    # 👁 on a proposed finding tracks it (by its index in the freshly-derived list) and re-renders
    # the screen in place — no message spam.
    _patch_problems(monkeypatch)
    add = AsyncMock()
    monkeypatch.setattr(proactive_flow.proactive, "add_problem", add)
    callback = _callback(callbacks.problem_track(0))
    callback.message.edit_text = AsyncMock()
    await proactive_flow.on_problem_track(callback, reminder_scheduler=object())
    add.assert_awaited_once()
    assert add.await_args.kwargs["name"] == "Глюкоза"  # tracked the finding at index 0
    callback.message.edit_text.assert_awaited()  # edit-in-place refresh


async def test_problem_dismiss_waves_off_by_index_and_refreshes(monkeypatch) -> None:
    # ✖ remembers the finding as dismissed (so it stops being proposed) and refreshes in place.
    _patch_problems(monkeypatch)
    off = AsyncMock()
    monkeypatch.setattr(proactive_flow.proactive, "dismiss_problem", off)
    callback = _callback(callbacks.problem_dismiss(0))
    callback.message.edit_text = AsyncMock()
    await proactive_flow.on_problem_dismiss(callback, reminder_scheduler=object())
    off.assert_awaited_once()
    assert off.await_args.kwargs["name"] == "Глюкоза"
    callback.message.edit_text.assert_awaited()


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
