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

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from dbaylo import locale
from dbaylo.bot import companion_flow, history_flow, navigator_flow, proactive_flow
from dbaylo.bot.keyboards import clear_inline_keyboard, section_keyboard
from dbaylo.companion import callbacks
from dbaylo.companion.scheduler import ReminderScheduler

router = Router(name="menu")


def _owner_tg(event: Message | CallbackQuery) -> int | None:
    return event.from_user.id if event.from_user else None


# --- Reply-keyboard label taps -> section screens -------------------------------


@router.message(StateFilter(None), F.text == locale.MENU_LABS)
async def menu_labs(message: Message) -> None:
    await message.answer(
        locale.MENU_LABS_INTRO,
        reply_markup=section_keyboard((locale.BTN_MENU_HISTORY, callbacks.MENU_OPEN_HISTORY)),
    )


@router.message(StateFilter(None), F.text == locale.MENU_GOALS)
async def menu_goals(message: Message) -> None:
    await message.answer(
        locale.MENU_GOALS_INTRO,
        reply_markup=section_keyboard(
            (locale.BTN_MENU_GOALS_LIST, callbacks.MENU_GOALS_LIST),
            (locale.BTN_MENU_GOAL_NEW, callbacks.MENU_GOAL_NEW),
        ),
    )


@router.message(StateFilter(None), F.text == locale.MENU_PROBLEMS)
async def menu_problems(message: Message) -> None:
    await message.answer(
        locale.MENU_PROBLEMS_INTRO,
        reply_markup=section_keyboard(
            (locale.BTN_MENU_PROB_LIST, callbacks.MENU_PROB_LIST),
            (locale.BTN_MENU_PROB_NEW, callbacks.MENU_PROB_NEW),
        ),
    )


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


@router.message(StateFilter(None), F.text == locale.MENU_CHECKIN)
async def menu_checkin(message: Message, state: FSMContext) -> None:
    await companion_flow.start_checkin_dialog(message, state)


@router.message(StateFilter(None), F.text == locale.MENU_HELP)
async def menu_help(message: Message) -> None:
    await message.answer(locale.HELP_TEXT)


# --- Section inline actions -> reused flow helpers ------------------------------


@router.callback_query(F.data == callbacks.MENU_OPEN_HISTORY)
async def cb_open_history(callback: CallbackQuery) -> None:
    tg = _owner_tg(callback)
    if isinstance(callback.message, Message) and tg is not None:
        await history_flow.render_history(callback.message, tg)
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
