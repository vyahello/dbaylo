"""Tier 1.3 — the button menu: the bot's face over Tier 1.1/1.2.

Two kinds of handler, **no domain logic of its own**:

* **Reply-keyboard label taps** (plain text messages) — matched by *exact* equality
  and ``StateFilter(None)``, registered before the history-NL and companion handlers so
  a tap routes deterministically and never leaks into chat. A label opens a section: a
  short message with inline action buttons. (Mid-dialog, a label first aborts the dialog
  via ``CommandStateResetMiddleware`` — so the section opens fresh, nothing is saved.)
* **Section inline-button callbacks** — delegate to the reusable helpers exposed by each
  flow module (the commands are aliases over the same helpers). The single
  ``CANCEL_DIALOG`` callback clears whatever FSM dialog is active and saves nothing.

This module imports the flow modules but none import it (no cycle); it reaches no LLM
and no escalation entry point (the AST choke-point stays green).
"""

from __future__ import annotations

import contextlib

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from dbaylo import locale
from dbaylo.bot import companion_flow, consult_flow, history_flow, navigator_flow, proactive_flow
from dbaylo.bot.keyboards import clear_inline_keyboard, section_keyboard
from dbaylo.companion import callbacks
from dbaylo.companion.scheduler import ReminderScheduler

router = Router(name="menu")


def _owner_tg(event: Message | CallbackQuery) -> int | None:
    return event.from_user.id if event.from_user else None


# --- Reply-keyboard label taps -> section screens -------------------------------


def _labs_hub() -> tuple[str, InlineKeyboardMarkup]:
    """The "Аналізи" hub: two distinct destinations as their own buttons — the saved-reports
    history and the cross-lab dynamics browser. (A new analysis is added by sending a photo/PDF.)"""
    return locale.MENU_LABS_INTRO, section_keyboard(
        (locale.BTN_MENU_HISTORY, callbacks.MENU_OPEN_HISTORY),
        (locale.BTN_DYN_BROWSE, callbacks.DYN_OPEN),
    )


def _goals_section() -> tuple[str, InlineKeyboardMarkup]:
    """The goals section: list my goals / add a new one."""
    return locale.MENU_GOALS_INTRO, section_keyboard(
        (locale.BTN_MENU_GOALS_LIST, callbacks.MENU_GOALS_LIST),
        (locale.BTN_MENU_GOAL_NEW, callbacks.MENU_GOAL_NEW),
    )


@router.message(StateFilter(None), F.text == locale.MENU_HEALTH)
async def menu_health(message: Message) -> None:
    """🩺 Моє здоровʼя — the agent's health hub: analyses · problems · goals · check-in."""
    await message.answer(
        locale.MENU_HEALTH_INTRO,
        reply_markup=section_keyboard(
            (locale.BTN_MENU_ANALYSES, callbacks.MENU_OPEN_ANALYSES),
            (locale.BTN_MENU_PROBLEMS, callbacks.MENU_PROB_LIST),
            (locale.BTN_MENU_GOALS, callbacks.MENU_OPEN_GOALS),
            (locale.BTN_MENU_CHECKIN, callbacks.MENU_OPEN_CHECKIN),
        ),
    )


@router.message(StateFilter(None), F.text == locale.MENU_CARE)
async def menu_care(message: Message) -> None:
    """💊 Ліки й нагадування — medications (list / add) + the reminders list, in one hub."""
    await message.answer(
        locale.MENU_CARE_INTRO,
        reply_markup=section_keyboard(
            (locale.BTN_MENU_MED_LIST, callbacks.MENU_MED_LIST),
            (locale.BTN_MENU_MED_NEW, callbacks.MENU_MED_NEW),
            (locale.BTN_MENU_REMINDERS, callbacks.MENU_OPEN_REMINDERS),
        ),
    )


@router.message(StateFilter(None), F.text == locale.MENU_LABS)
async def menu_labs(message: Message) -> None:
    # Legacy label (now reached via 🩺 Моє здоровʼя → Аналізи); kept for a cached old keyboard.
    text, keyboard = _labs_hub()
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == callbacks.MENU_OPEN_LABS)
async def cb_open_labs(callback: CallbackQuery) -> None:
    """Back to the "Аналізи" hub — edits the message in place (the history list's ◀ Назад)."""
    if isinstance(callback.message, Message):
        text, keyboard = _labs_hub()
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.message(StateFilter(None), F.text == locale.MENU_GOALS)
async def menu_goals(message: Message) -> None:
    # Legacy label (now reached via 🩺 Моє здоровʼя → Цілі); kept for a cached old keyboard.
    text, keyboard = _goals_section()
    await message.answer(text, reply_markup=keyboard)


@router.message(StateFilter(None), F.text == locale.MENU_PROBLEMS)
async def menu_problems(message: Message) -> None:
    # Straight to the agent's read — it shows what IT sees off (one-tap track/dismiss) instead of a
    # sub-menu that asks the user to type problems by hand.
    tg = _owner_tg(message)
    if tg is not None:
        await proactive_flow.open_problems(message, tg)


@router.message(StateFilter(None), F.text == locale.MENU_MEDS)
async def menu_meds(message: Message) -> None:
    await message.answer(
        locale.MENU_MEDS_INTRO,
        reply_markup=section_keyboard(
            (locale.BTN_MENU_MED_LIST, callbacks.MENU_MED_LIST),
            (locale.BTN_MENU_MED_NEW, callbacks.MENU_MED_NEW),
        ),
    )


@router.message(StateFilter(None), F.text == locale.MENU_REMINDERS)
async def menu_reminders(message: Message, reminder_scheduler: ReminderScheduler) -> None:
    tg = _owner_tg(message)
    if tg is not None:
        await proactive_flow.open_reminders(message, tg, reminder_scheduler)


@router.message(StateFilter(None), F.text == locale.MENU_PRICES)
async def menu_prices(message: Message) -> None:
    await message.answer(
        locale.MENU_PRICES_INTRO,
        reply_markup=section_keyboard(
            (locale.BTN_MENU_PRICE, callbacks.MENU_PRICE),
            (locale.BTN_MENU_COVERAGE, callbacks.MENU_COVERAGE),
        ),
    )


@router.message(StateFilter(None), F.text == locale.MENU_MEMORY)
async def menu_memory(message: Message) -> None:
    tg = _owner_tg(message)
    if tg is not None:
        await consult_flow.open_memory_view(message, tg)


@router.message(StateFilter(None), F.text == locale.MENU_CHECKIN)
async def menu_checkin(message: Message, state: FSMContext) -> None:
    await companion_flow.start_checkin_dialog(message, state)


@router.message(StateFilter(None), F.text == locale.MENU_HELP)
async def menu_help(message: Message) -> None:
    await message.answer(locale.HELP_TEXT)


# --- Section inline actions -> reused flow helpers ------------------------------


# Hub destinations -> post the leaf section/dialog as a NEW message (the hub stays above).
@router.callback_query(F.data == callbacks.MENU_OPEN_ANALYSES)
async def cb_open_analyses(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        text, keyboard = _labs_hub()
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == callbacks.MENU_OPEN_GOALS)
async def cb_open_goals(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        text, keyboard = _goals_section()
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == callbacks.MENU_OPEN_CHECKIN)
async def cb_open_checkin(callback: CallbackQuery, state: FSMContext) -> None:
    # The prompt is answered on a callback message (from_user is the bot), so pass the owner's tg
    # explicitly — otherwise the grounded check-in can't load the right user.
    tg = _owner_tg(callback)
    if isinstance(callback.message, Message):
        await companion_flow.start_checkin_dialog(callback.message, state, telegram_id=tg)
    await callback.answer()


@router.callback_query(F.data == callbacks.MENU_OPEN_REMINDERS)
async def cb_open_reminders(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    tg = _owner_tg(callback)
    if isinstance(callback.message, Message) and tg is not None:
        await proactive_flow.open_reminders(callback.message, tg, reminder_scheduler)
    await callback.answer()


@router.callback_query(F.data == callbacks.MENU_OPEN_HISTORY)
async def cb_open_history(callback: CallbackQuery) -> None:
    # Edit the hub message into the report list in place, so the list's ◀ Назад edits back to the
    # hub — one tidy message (same master-detail pattern as the list <-> report card).
    tg = _owner_tg(callback)
    if tg is not None:
        await history_flow.open_history_in_place(callback, tg)
    else:
        await callback.answer()


@router.callback_query(F.data == callbacks.MENU_GOALS_LIST)
async def cb_goals_list(callback: CallbackQuery) -> None:
    tg = _owner_tg(callback)
    if isinstance(callback.message, Message) and tg is not None:
        await companion_flow.open_goals(callback.message, tg)
    await callback.answer()


@router.callback_query(F.data == callbacks.MENU_GOAL_NEW)
async def cb_goal_new(callback: CallbackQuery, state: FSMContext) -> None:
    if isinstance(callback.message, Message):
        await companion_flow.start_goal_dialog(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == callbacks.MENU_PROB_LIST)
async def cb_prob_list(callback: CallbackQuery) -> None:
    tg = _owner_tg(callback)
    if isinstance(callback.message, Message) and tg is not None:
        await proactive_flow.open_problems(callback.message, tg)
    await callback.answer()


@router.callback_query(F.data == callbacks.MENU_PROB_NEW)
async def cb_prob_new(callback: CallbackQuery, state: FSMContext) -> None:
    if isinstance(callback.message, Message):
        await proactive_flow.start_problem_dialog(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == callbacks.MENU_MED_LIST)
async def cb_med_list(callback: CallbackQuery) -> None:
    tg = _owner_tg(callback)
    if isinstance(callback.message, Message) and tg is not None:
        await proactive_flow.open_medications(callback.message, tg)
    await callback.answer()


@router.callback_query(F.data == callbacks.MENU_MED_NEW)
async def cb_med_new(callback: CallbackQuery, state: FSMContext) -> None:
    if isinstance(callback.message, Message):
        await proactive_flow.start_medication_dialog(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == callbacks.MENU_PRICE)
async def cb_price(callback: CallbackQuery, state: FSMContext) -> None:
    if isinstance(callback.message, Message):
        await navigator_flow.start_price_dialog(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == callbacks.MENU_COVERAGE)
async def cb_coverage(callback: CallbackQuery, state: FSMContext) -> None:
    if isinstance(callback.message, Message):
        await navigator_flow.start_coverage_dialog(callback.message, state)
    await callback.answer()


# --- The one shared dialog cancel (clears any active FSM, saves nothing) ---------


@router.callback_query(F.data == callbacks.CANCEL_DIALOG)
async def cb_cancel_dialog(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_inline_keyboard(callback)  # consume the [Скасувати] so it can't fire twice
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.DIALOG_CANCELLED)
    await callback.answer()
