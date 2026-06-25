"""Proactive-behavior commands: problems (active concerns), medications, reminders.

Thin aiogram handlers over the ``companion`` coordinator. The live
:class:`ReminderScheduler` is injected from ``dispatcher["reminder_scheduler"]`` so
creating/resolving a problem or adding/removing a medication updates the running
schedule immediately (no restart).
"""

from __future__ import annotations

import contextlib
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.bot.keyboards import cancel_keyboard, remove_button_row
from dbaylo.companion import callbacks, concerns, medications, proactive, reminders
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.db import get_session
from dbaylo.db.models import Medication, Reminder
from dbaylo.labs.intake import ensure_user

router = Router(name="proactive")


class ProblemStates(StatesGroup):
    waiting_name = State()
    waiting_rename = State()


class MedStates(StatesGroup):
    waiting_name = State()
    waiting_times = State()


def _telegram_id(event: Message | CallbackQuery) -> int | None:
    return event.from_user.id if event.from_user else None


def _fmt_when(when: datetime | None) -> str:
    return when.strftime("%Y-%m-%d %H:%M") if when is not None else locale.REMINDER_NEXT_UNKNOWN


# --- Problems (active concerns) -------------------------------------------------


async def start_problem_dialog(message: Message, state: FSMContext) -> None:
    """Enter the add-problem dialog (from /problem or the menu) — always cancellable."""
    await state.set_state(ProblemStates.waiting_name)
    await message.answer(locale.PROBLEM_ASK_TEXT, reply_markup=cancel_keyboard())


@router.message(Command("problem"))
async def cmd_problem(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    reminder_scheduler: ReminderScheduler,
) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await start_problem_dialog(message, state)
        return
    await state.clear()
    await _add_problem(message, arg, reminder_scheduler)


@router.message(ProblemStates.waiting_name, F.text)
async def on_problem_name(
    message: Message, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
    await state.clear()
    await _add_problem(message, (message.text or "").strip(), reminder_scheduler)


async def _add_problem(message: Message, name: str, scheduler: ReminderScheduler) -> None:
    tg = _telegram_id(message)
    if tg is None:
        return
    if not name.strip():
        # Blank input -> never a phantom concern (and never an unwanted check-in).
        await message.answer(locale.NOTHING_SAVED)
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        await proactive.add_problem(session, user=user, name=name, scheduler=scheduler)
        await session.commit()
    await message.answer(locale.PROBLEM_ADDED)


async def open_problems(message: Message, telegram_id: int) -> None:
    """List active concerns in ONE message — a row per concern (✅ resolve · ✏️ rename) — instead of
    a separate message each. Resolving removes just that row (the name lives in the button)."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        active = await concerns.list_active(session, user_id=user.id)
    if not active:
        await message.answer(locale.PROBLEM_LIST_EMPTY)
        return
    rows = [
        [
            InlineKeyboardButton(
                text=locale.BTN_PROBLEM_RESOLVED_NAMED.format(name=condition.name),
                callback_data=callbacks.problem_resolve(condition.id),
            ),
            InlineKeyboardButton(
                text=locale.BTN_PROBLEM_RENAME_SHORT,
                callback_data=callbacks.problem_rename(condition.id),
            ),
        ]
        for condition in active
    ]
    await message.answer(
        locale.PROBLEM_LIST_HEADER, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.message(Command("problems"))
async def cmd_problems(message: Message) -> None:
    tg = _telegram_id(message)
    if tg is None:
        return
    await open_problems(message, tg)


@router.callback_query(F.data.startswith(callbacks.PROBLEM_RESOLVE + ":"))
async def on_problem_resolve(
    callback: CallbackQuery, reminder_scheduler: ReminderScheduler
) -> None:
    condition_id = callbacks.parse_problem_resolve(callback.data or "")
    tg = _telegram_id(callback)
    if condition_id is None or tg is None:
        await callback.answer()
        return
    # Consume just THIS concern's button: a batched check-in review packs several concerns into
    # one message (one row each), so the other concerns must stay tappable.
    await remove_button_row(callback)
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        await proactive.resolve_problem(
            session, user_id=user.id, condition_id=condition_id, scheduler=reminder_scheduler
        )
        await session.commit()
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.PROBLEM_RESOLVED)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.PROBLEM_RENAME + ":"))
async def on_problem_rename(callback: CallbackQuery, state: FSMContext) -> None:
    condition_id = callbacks.parse_problem_rename(callback.data or "")
    if condition_id is None:
        await callback.answer()
        return
    await state.set_state(ProblemStates.waiting_rename)
    await state.update_data(condition_id=condition_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.PROBLEM_ASK_RENAME, reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(ProblemStates.waiting_rename, F.text)
async def on_problem_new_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    condition_id = data.get("condition_id")
    await state.clear()
    if not isinstance(condition_id, int):  # state lost (e.g. a restart mid-rename) — say so
        await message.answer(locale.NOTHING_SAVED)
        return
    async with get_session() as session:
        await concerns.rename(session, condition_id, (message.text or "").strip())
        await session.commit()
    await message.answer(locale.PROBLEM_RENAMED)


# --- Medications ----------------------------------------------------------------


async def start_medication_dialog(message: Message, state: FSMContext) -> None:
    """Enter the add-medication dialog (from /medication or the menu) — cancellable."""
    await state.set_state(MedStates.waiting_name)
    await message.answer(locale.MED_ASK_NAME, reply_markup=cancel_keyboard())


@router.message(Command("medication"))
async def cmd_medication(message: Message, state: FSMContext) -> None:
    await start_medication_dialog(message, state)


@router.message(MedStates.waiting_name, F.text)
async def on_medication_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await state.clear()  # blank name -> abort, create nothing
        await message.answer(locale.NOTHING_SAVED)
        return
    await state.update_data(med_name=name)
    await state.set_state(MedStates.waiting_times)
    await message.answer(locale.MED_ASK_TIMES, reply_markup=cancel_keyboard())


@router.message(MedStates.waiting_times, F.text)
async def on_medication_times(
    message: Message, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
    times = medications.parse_times(message.text or "")
    if not times:
        await message.answer(locale.MED_BAD_TIMES)  # stay in state, ask again
        return
    data = await state.get_data()
    name = str(data.get("med_name", "")).strip()
    await state.clear()
    tg = _telegram_id(message)
    if tg is None or not name:
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        await proactive.add_medication(
            session, user=user, name=name, times=times, scheduler=reminder_scheduler
        )
        await session.commit()
    pretty = ", ".join(t.strftime("%H:%M") for t in times)
    await message.answer(locale.MED_ADDED.format(name=name, times=pretty))


async def open_medications(message: Message, telegram_id: int) -> None:
    """List the user's medications in ONE message — a tappable turn-off per medication (the name is
    in the button, so turning one off removes just that row) instead of a message each."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        meds = await medications.list_medications(session, user_id=user.id)
    if not meds:
        await message.answer(locale.MED_LIST_EMPTY)
        return
    rows = [
        [
            InlineKeyboardButton(
                text=locale.BTN_MED_OFF_NAMED.format(name=med.name, times=med.schedule or "?"),
                callback_data=callbacks.medication_off(med.id),
            )
        ]
        for med in meds
    ]
    await message.answer(
        locale.MED_LIST_HEADER, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


# --- Reminder management --------------------------------------------------------


def _reminder_label(reminder: Reminder, med: object | None, when: str) -> str:
    """The one-line description of a reminder (icon · what · next run) — shown in the list and as
    the card's title. A tap on this in the list OPENS it (read), it no longer deletes."""
    if reminder.type == reminders.TYPE_MEDICATION:
        name = getattr(med, "name", None) or reminder.payload or "?"
        times = getattr(med, "schedule", None) or "?"
        return locale.REMINDER_ITEM_MEDICATION.format(name=name, times=times, when=when)
    if reminder.type == reminders.TYPE_CHECKIN:
        return locale.REMINDER_ITEM_CHECKIN.format(when=when)
    if reminder.type == reminders.TYPE_REPEAT_LAB:
        return locale.REMINDER_ITEM_REPEAT_LAB.format(name=reminder.payload or "?", when=when)
    return locale.REMINDER_ITEM_CONSULT.format(name=reminder.payload or "?", when=when)


async def _reminders_payload(
    session: AsyncSession, *, user_id: int, scheduler: ReminderScheduler
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Build the reminders list (header + one button per reminder). Tapping a button opens that
    reminder's card; medications collapse to one row (their card turns off all times)."""
    rows = await reminders.active_reminders_for_user(session, user_id=user_id)
    meds = {m.id: m for m in await medications.list_medications(session, user_id=user_id)}
    if not rows:
        return locale.REMINDERS_EMPTY, None
    next_run = {job.id: job.next_run for job in scheduler.list_jobs()}
    kb_rows: list[list[InlineKeyboardButton]] = []
    shown_medications: set[int] = set()
    for reminder in rows:
        when = _fmt_when(next_run.get(f"reminder:{reminder.id}"))
        if reminder.type == reminders.TYPE_MEDICATION and reminder.medication_id is not None:
            if reminder.medication_id in shown_medications:
                continue  # one row per medication; its card turns off all its times
            shown_medications.add(reminder.medication_id)
            label = _reminder_label(reminder, meds.get(reminder.medication_id), when)
            data = callbacks.medication_view(reminder.medication_id)
        else:
            label = _reminder_label(reminder, None, when)
            data = callbacks.reminder_view(reminder.id)
        kb_rows.append([InlineKeyboardButton(text=label, callback_data=data)])
    return locale.REMINDERS_HEADER, InlineKeyboardMarkup(inline_keyboard=kb_rows)


def _card_keyboard(delete_data: str) -> InlineKeyboardMarkup:
    """A reminder card's actions: delete it (deliberate) or go back to the list."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=locale.BTN_REMINDER_DELETE, callback_data=delete_data),
                InlineKeyboardButton(
                    text=locale.BTN_REMINDER_BACK, callback_data=callbacks.REMINDERS_BACK
                ),
            ]
        ]
    )


def _render_card(label: str, when: str) -> str:
    return f"{label}\n{locale.REMINDER_CARD_NEXT.format(when=when)}\n\n{locale.REMINDER_CARD_HINT}"


async def open_reminders(message: Message, telegram_id: int, scheduler: ReminderScheduler) -> None:
    """List active reminders (from /reminders or the menu). A tap opens the reminder to read it."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        text, keyboard = await _reminders_payload(session, user_id=user.id, scheduler=scheduler)
    await message.answer(text, reply_markup=keyboard)


async def _edit_to_list(callback: CallbackQuery, scheduler: ReminderScheduler) -> None:
    """Edit the current message back into the (refreshed) reminders list."""
    tg = _telegram_id(callback)
    if tg is None or not isinstance(callback.message, Message):
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        text, keyboard = await _reminders_payload(session, user_id=user.id, scheduler=scheduler)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(text, reply_markup=keyboard)


@router.message(Command("reminders"))
async def cmd_reminders(message: Message, reminder_scheduler: ReminderScheduler) -> None:
    tg = _telegram_id(message)
    if tg is None:
        return
    await open_reminders(message, tg, reminder_scheduler)


@router.callback_query(F.data == callbacks.REMINDERS_BACK)
async def on_reminders_back(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    await _edit_to_list(callback, reminder_scheduler)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.REMINDER_VIEW + ":"))
async def on_reminder_view(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    """Open a reminder's card (read it) — turning it off is a deliberate button, not this tap."""
    reminder_id = callbacks.parse_reminder_view(callback.data or "")
    if reminder_id is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    next_run = {job.id: job.next_run for job in reminder_scheduler.list_jobs()}
    async with get_session() as session:
        reminder = await session.get(Reminder, reminder_id)
    if reminder is None or not reminder.active:
        await _edit_to_list(callback, reminder_scheduler)  # gone meanwhile — show the fresh list
        await callback.answer()
        return
    when = _fmt_when(next_run.get(f"reminder:{reminder.id}"))
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(
            _render_card(_reminder_label(reminder, None, when), when),
            reply_markup=_card_keyboard(callbacks.reminder_off(reminder.id)),
        )
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.MEDICATION_VIEW + ":"))
async def on_medication_view(
    callback: CallbackQuery, reminder_scheduler: ReminderScheduler
) -> None:
    medication_id = callbacks.parse_medication_view(callback.data or "")
    tg = _telegram_id(callback)
    if medication_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    next_run = {job.id: job.next_run for job in reminder_scheduler.list_jobs()}
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        med = await session.get(Medication, medication_id)
        rems = [
            r
            for r in await reminders.active_reminders_for_user(session, user_id=user.id)
            if r.medication_id == medication_id
        ]
    if med is None or not rems:
        await _edit_to_list(callback, reminder_scheduler)  # gone meanwhile — show the fresh list
        await callback.answer()
        return
    times = [w for r in rems if (w := next_run.get(f"reminder:{r.id}")) is not None]
    when = _fmt_when(min(times) if times else None)
    label = locale.REMINDER_ITEM_MEDICATION.format(name=med.name, times=med.schedule, when=when)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(
            _render_card(label, when),
            reply_markup=_card_keyboard(callbacks.medication_delete(medication_id)),
        )
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.REMINDER_OFF + ":"))
async def on_reminder_delete(
    callback: CallbackQuery, reminder_scheduler: ReminderScheduler
) -> None:
    """Deliberate DELETE from a reminder's card (a turned-off one can't be turned back on, so we
    remove it), then refresh the list in place."""
    reminder_id = callbacks.parse_reminder_off(callback.data or "")
    if reminder_id is None:
        await callback.answer()
        return
    async with get_session() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is not None:
            await proactive.delete_reminder(
                session, reminder=reminder, scheduler=reminder_scheduler
            )
            await session.commit()
    await callback.answer(locale.REMINDER_DELETED)
    await _edit_to_list(callback, reminder_scheduler)


@router.callback_query(F.data.startswith(callbacks.MEDICATION_DELETE + ":"))
async def on_medication_delete(
    callback: CallbackQuery, reminder_scheduler: ReminderScheduler
) -> None:
    """Delete a medication's reminders from its reminder card, then refresh the list in place."""
    medication_id = callbacks.parse_medication_delete(callback.data or "")
    if medication_id is None:
        await callback.answer()
        return
    async with get_session() as session:
        await proactive.delete_medication_reminders(
            session, medication_id=medication_id, scheduler=reminder_scheduler
        )
        await session.commit()
    await callback.answer(locale.REMINDER_DELETED)
    await _edit_to_list(callback, reminder_scheduler)


@router.callback_query(F.data.startswith(callbacks.MEDICATION_OFF + ":"))
async def on_medication_off(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    """Turn a medication's reminders off from the /medication list (its own message) — drop just the
    tapped row; the Medication record stays. (The reminder CARD uses MEDICATION_DELETE instead.)"""
    medication_id = callbacks.parse_medication_off(callback.data or "")
    if medication_id is None:
        await callback.answer()
        return
    await remove_button_row(callback)  # consume just this medication's row in the /medication list
    async with get_session() as session:
        await proactive.turn_off_medication(
            session, medication_id=medication_id, scheduler=reminder_scheduler
        )
        await session.commit()
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.REMINDER_TURNED_OFF)
    await callback.answer()
