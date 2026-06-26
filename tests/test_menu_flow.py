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


async def test_menu_health_hub_offers_analyses_problems_goals_checkin() -> None:
    # 🎯 Мої цілі is its own hub destination (a full goals screen), alongside analyses / problems /
    # check-in.
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
        callbacks.MENU_MED_PHOTO,  # 📷 read a prescription photo leads the hub
        callbacks.MENU_MED_LIST,
        callbacks.MENU_MED_NEW,
        callbacks.MENU_OPEN_REMINDERS,
    ]


async def test_cb_med_photo_starts_the_prescription_dialog(monkeypatch) -> None:
    seen = {}

    async def fake(message, state):
        seen["args"] = (message, state)

    monkeypatch.setattr(menu_flow.prescription_flow, "start_prescription_dialog", fake)
    callback = _callback(callbacks.MENU_MED_PHOTO)
    state = object()
    await menu_flow.cb_med_photo(callback, state)
    assert seen["args"] == (callback.message, state)


async def test_cb_open_analyses_posts_the_labs_hub() -> None:
    callback = _callback(callbacks.MENU_OPEN_ANALYSES)
    await menu_flow.cb_open_analyses(callback)
    _, kwargs = callback.message.answer.call_args
    assert _cb_datas(kwargs["reply_markup"]) == [callbacks.MENU_OPEN_HISTORY, callbacks.DYN_OPEN]
    callback.answer.assert_awaited_once()


async def test_cb_open_goals_delegates_to_the_agent_screen(monkeypatch) -> None:
    # 🎯 Цілі now opens the agent-driven screen (it suggests goals), not a list/new sub-menu.
    seen = {}

    async def fake(message, telegram_id):
        seen["args"] = (message, telegram_id)

    monkeypatch.setattr(menu_flow.companion_flow, "open_goals_screen", fake)
    callback = _callback(callbacks.MENU_OPEN_GOALS)
    await menu_flow.cb_open_goals(callback)
    assert seen["args"] == (callback.message, 4242)


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


async def test_cb_open_checkin_acks_the_tap_before_the_slow_prompt(monkeypatch) -> None:
    # The grounded prompt is a multi-second LLM call; the tap must be acknowledged FIRST so the
    # button doesn't spin (read as a hang) for the whole wait.
    order = []

    async def slow_start(message, state, *, telegram_id):
        order.append("start")

    callback = _callback(callbacks.MENU_OPEN_CHECKIN)
    callback.answer = AsyncMock(side_effect=lambda *a, **k: order.append("answer"))
    monkeypatch.setattr(menu_flow.companion_flow, "start_checkin_dialog", slow_start)
    await menu_flow.cb_open_checkin(callback, object())
    assert order == ["answer", "start"]  # ack first, then the slow grounded prompt


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


async def test_menu_goals_legacy_label_opens_the_goals_screen(monkeypatch) -> None:
    # The legacy 🎯 Цілі label (cached keyboard) opens the goals screen, same as the hub button.
    seen = {}

    async def fake(message, telegram_id):
        seen["args"] = (message, telegram_id)

    monkeypatch.setattr(menu_flow.companion_flow, "open_goals_screen", fake)
    message = AsyncMock()
    message.from_user = SimpleNamespace(id=4242)
    await menu_flow.menu_goals(message)
    assert seen["args"] == (message, 4242)


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


async def test_menu_help_is_actionable_not_a_command_list() -> None:
    # ❓ Довідка is agent-framed (send photos / chat / tap sections) with inline quick-jumps into
    # the agent screens — not a wall of "/" commands.
    message = AsyncMock()
    await menu_flow.menu_help(message)
    text = message.answer.call_args.args[0]
    assert "/goal" not in text and "/medication" not in text  # no slash-command list
    assert "Надсилай фото" in text  # the agent paradigm is explained
    datas = [
        b.callback_data
        for row in message.answer.call_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    # The quick-jumps go straight into the agent screens (reusing the existing leaf callbacks).
    assert callbacks.MENU_OPEN_ANALYSES in datas and callbacks.MENU_PROB_LIST in datas
    assert callbacks.MENU_OPEN_GOALS in datas and callbacks.MENU_OPEN_CHECKIN in datas
    assert callbacks.MENU_MED_LIST in datas and callbacks.MENU_PRICE in datas
    assert callbacks.MENU_OPEN_MEMORY in datas


async def test_cb_open_memory_opens_the_memory_view(monkeypatch) -> None:
    seen = {}

    async def fake(message, tg):
        seen["args"] = (message, tg)

    monkeypatch.setattr(menu_flow.consult_flow, "open_memory_view", fake)
    callback = _callback(callbacks.MENU_OPEN_MEMORY)
    await menu_flow.cb_open_memory(callback)
    assert seen["args"] == (callback.message, 4242)


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


def _finding(name, *, category="blood", specimen="blood", kind="high"):
    from dbaylo.companion.health import HealthFinding

    return HealthFinding(
        name=name,
        value="169 г/л",
        ref="130–160",
        flag_text="above",
        direction="LEFT_RANGE",
        last_date=None,
        n_points=2,
        kind=kind,
        category=category,
        specimen=specimen,
    )


async def test_open_problems_groups_by_category(monkeypatch) -> None:
    # The grouped top level: a button per clinical category that has something off, then ✅ tracked,
    # then ➕ manual — a digest, NOT a wall of every finding.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_session():
        yield object()

    proposals = [
        _finding("Гемоглобін (HGB)", category="blood"),
        _finding("Лейкоцити", category="urine", specimen="urine"),
        _finding("Холестерин", category="biochem", kind="watch"),  # on the edge -> 📈 group
    ]
    monkeypatch.setattr(proactive_flow, "get_session", fake_session)
    monkeypatch.setattr(
        proactive_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(
        proactive_flow.health, "propose_problems", AsyncMock(return_value=proposals)
    )
    monkeypatch.setattr(
        proactive_flow.concerns,
        "list_active",
        AsyncMock(return_value=[SimpleNamespace(id=1, name="Болить спина")]),
    )
    monkeypatch.setattr(proactive_flow.concerns, "list_resolved", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        proactive_flow.health, "list_relevant_dismissed", AsyncMock(return_value=[])
    )
    message = AsyncMock()
    await proactive_flow.open_problems(message, telegram_id=4242)
    message.answer.assert_awaited_once()  # ONE digest message
    datas = [
        b.callback_data
        for row in message.answer.call_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert callbacks.problem_category("blood") in datas  # one button per off-category
    assert callbacks.problem_category("urine") in datas
    assert callbacks.problem_category("watch") in datas  # the on-the-edge group, separated
    assert callbacks.PROBLEM_TRACKED in datas  # the tracked concerns are behind their own button
    assert callbacks.MENU_OPEN_GOALS not in datas  # goals are their OWN hub button now
    assert callbacks.MENU_PROB_NEW in datas  # manual fallback always present
    # The top level is a digest — individual findings/resolve buttons are NOT dumped here.
    assert not any(str(d).startswith(callbacks.PROBLEM_TRACK + ":") for d in datas)


async def test_category_detail_lists_findings_with_track_dismiss(monkeypatch) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_session():
        yield object()

    proposals = [_finding("Гемоглобін (HGB)", category="blood")]
    monkeypatch.setattr(proactive_flow, "get_session", fake_session)
    monkeypatch.setattr(
        proactive_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(
        proactive_flow.health, "propose_problems", AsyncMock(return_value=proposals)
    )
    callback = _callback(callbacks.problem_category("blood"))
    callback.message.edit_text = AsyncMock()
    await proactive_flow.on_problem_category(callback)
    callback.message.edit_text.assert_awaited_once()
    text = callback.message.edit_text.call_args.args[0]
    datas = [
        b.callback_data
        for row in callback.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert "Гемоглобін (HGB)" in text  # the finding is described here, in its category
    assert callbacks.problem_track("blood", 0) in datas  # 👁 / ✖ carry (category, flat index)
    assert callbacks.problem_dismiss("blood", 0) in datas
    assert callbacks.PROBLEM_BACK in datas  # ◀ back to the grouped top
    # The track button names the finding so stacked rows aren't all identical "Відстежувати".
    track_labels = [
        b.text
        for row in callback.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
        for b in row
        if b.callback_data == callbacks.problem_track("blood", 0)
    ]
    assert track_labels and "Гемоглобін" in track_labels[0]


def _patch_problems(monkeypatch, finding=None):
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
        AsyncMock(return_value=[finding or _finding("Глюкоза", category="biochem")]),
    )
    monkeypatch.setattr(proactive_flow.concerns, "list_active", AsyncMock(return_value=[]))
    monkeypatch.setattr(proactive_flow.concerns, "list_resolved", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        proactive_flow.health, "list_relevant_dismissed", AsyncMock(return_value=[])
    )


async def test_problem_track_creates_concern_by_index_and_refreshes(monkeypatch) -> None:
    # 👁 on a proposed finding tracks it (by its flat index) and re-renders the same detail in place.
    _patch_problems(monkeypatch)
    add = AsyncMock()
    monkeypatch.setattr(proactive_flow.proactive, "add_problem", add)
    callback = _callback(callbacks.problem_track("biochem", 0))
    callback.message.edit_text = AsyncMock()
    await proactive_flow.on_problem_track(callback, reminder_scheduler=object())
    add.assert_awaited_once()
    assert add.await_args.kwargs["name"] == "Глюкоза"  # tracked the finding at index 0
    callback.message.edit_text.assert_awaited()  # edit-in-place refresh


async def test_problem_track_persists_specimen_qualified_name(monkeypatch) -> None:
    # A urine finding is tracked under its specimen-qualified name, so a blood twin isn't confused.
    _patch_problems(monkeypatch, finding=_finding("Еритроцити", category="urine", specimen="urine"))
    add = AsyncMock()
    monkeypatch.setattr(proactive_flow.proactive, "add_problem", add)
    callback = _callback(callbacks.problem_track("urine", 0))
    callback.message.edit_text = AsyncMock()
    await proactive_flow.on_problem_track(callback, reminder_scheduler=object())
    assert add.await_args.kwargs["name"] == "Еритроцити (сеча)"  # disambiguated on persist


async def test_problem_dismiss_waves_off_by_index_and_refreshes(monkeypatch) -> None:
    # ✖ remembers the finding as dismissed (so it stops being proposed) and refreshes in place.
    _patch_problems(monkeypatch)
    off = AsyncMock()
    monkeypatch.setattr(proactive_flow.proactive, "dismiss_problem", off)
    callback = _callback(callbacks.problem_dismiss("biochem", 0))
    callback.message.edit_text = AsyncMock()
    await proactive_flow.on_problem_dismiss(callback, reminder_scheduler=object())
    off.assert_awaited_once()
    assert off.await_args.kwargs["name"] == "Глюкоза"
    callback.message.edit_text.assert_awaited()


def test_stored_concern_shows_its_clinical_group() -> None:
    # Під наглядом / Відкладені / Вирішені list STORED concern names — they must show which аналіз
    # group each belongs to (🩸/🔬/⚗️/🧫), re-derived from the name; a custom concern gets no tag.
    p = proactive_flow._category_prefix
    assert p("Базофіли").startswith("🩸")  # blood cell
    assert p("Білок загальний").startswith("⚗️")  # biochemistry (blood-derived)
    assert p("Нирковий епітелій (сеча)").startswith("🔬")  # urine (the «(сеча)» tag)
    assert p("Аналіз крові: Швидкість осідання").startswith("🩸")  # the «крові» prefix
    assert p("Клітини сперматогенезу (%)").startswith("🧫")  # semen
    assert p("Болить спина") == ""  # a custom non-lab concern -> no group tag


async def test_problem_restore_undismisses(monkeypatch) -> None:
    # ↩️ from the «Приховані» list restores a wrongly-waved-off finding (an undo for ✖).
    _patch_problems(monkeypatch)
    restore = AsyncMock(return_value=SimpleNamespace(id=5, name="Глюкоза"))
    monkeypatch.setattr(proactive_flow.proactive, "restore_problem", restore)
    monkeypatch.setattr(
        proactive_flow.health, "list_relevant_dismissed", AsyncMock(return_value=[])
    )
    callback = _callback(callbacks.problem_restore(5))
    callback.message.edit_text = AsyncMock()
    await proactive_flow.on_problem_restore(callback, reminder_scheduler=object())
    restore.assert_awaited_once()
    assert restore.await_args.kwargs["condition_id"] == 5


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


async def test_cb_price_opens_the_meds_price_options(monkeypatch) -> None:
    # 💊 Ціна ліків now proposes the owner's own meds (one-tap), so the menu opens the options
    # screen with the owner's tg threaded (callback from_user would otherwise be the bot).
    seen = {}

    async def fake(message, state, *, telegram_id):
        seen["args"] = (message, state, telegram_id)

    monkeypatch.setattr(menu_flow.navigator_flow, "open_price_options", fake)
    callback = _callback(callbacks.MENU_PRICE)
    state = AsyncMock()
    await menu_flow.cb_price(callback, state)
    assert seen["args"] == (callback.message, state, 4242)


# --- The single shared cancel ---------------------------------------------------


async def test_cancel_dialog_clears_any_state_and_saves_nothing() -> None:
    callback = _callback(callbacks.CANCEL_DIALOG)
    state = AsyncMock()
    await menu_flow.cb_cancel_dialog(callback, state)
    state.clear.assert_awaited_once()
    callback.message.answer.assert_awaited_once_with(locale.DIALOG_CANCELLED)
    callback.answer.assert_awaited_once()
