"""Proactive-behavior commands: problems (active concerns), medications, reminders.

Thin aiogram handlers over the ``companion`` coordinator. The live
:class:`ReminderScheduler` is injected from ``dispatcher["reminder_scheduler"]`` so
creating/resolving a problem or adding/removing a medication updates the running
schedule immediately (no restart).
"""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from dbaylo import locale
from dbaylo.bot.keyboards import cancel_keyboard
from dbaylo.companion import callbacks, concerns, medications, proactive, reminders
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.db import get_session
from dbaylo.db.models import Reminder
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


def _off_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=locale.BTN_REMINDER_OFF, callback_data=callback_data)]
        ]
    )


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
    """List active concerns with their resolve/rename buttons (from /problems or menu)."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        active = await concerns.list_active(session, user_id=user.id)
    if not active:
        await message.answer(locale.PROBLEM_LIST_EMPTY)
        return
    await message.answer(locale.PROBLEM_LIST_HEADER)
    for condition in active:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=locale.BTN_PROBLEM_RESOLVED,
                        callback_data=callbacks.problem_resolve(condition.id),
                    ),
                    InlineKeyboardButton(
                        text=locale.BTN_PROBLEM_RENAME,
                        callback_data=callbacks.problem_rename(condition.id),
                    ),
                ]
            ]
        )
        await message.answer(f"• {condition.name}", reply_markup=keyboard)


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
    if not isinstance(condition_id, int):
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
    """List the user's medications, each with the existing per-medication turn-off."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        meds = await medications.list_medications(session, user_id=user.id)
    if not meds:
        await message.answer(locale.MED_LIST_EMPTY)
        return
    await message.answer(locale.MED_LIST_HEADER)
    for med in meds:
        text = locale.MED_LIST_ITEM.format(name=med.name, times=med.schedule or "?")
        await message.answer(text, reply_markup=_off_keyboard(callbacks.medication_off(med.id)))


# --- Reminder management --------------------------------------------------------


async def open_reminders(message: Message, telegram_id: int, scheduler: ReminderScheduler) -> None:
    """List active reminders with per-item turn-off (from /reminders or the menu)."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        rows = await reminders.active_reminders_for_user(session, user_id=user.id)
        meds = {m.id: m for m in await medications.list_medications(session, user_id=user.id)}
    if not rows:
        await message.answer(locale.REMINDERS_EMPTY)
        return

    next_run = {job.id: job.next_run for job in scheduler.list_jobs()}
    await message.answer(locale.REMINDERS_HEADER)
    shown_medications: set[int] = set()
    for reminder in rows:
        when = _fmt_when(next_run.get(f"reminder:{reminder.id}"))
        if reminder.type == reminders.TYPE_MEDICATION and reminder.medication_id is not None:
            if reminder.medication_id in shown_medications:
                continue  # one row per medication; its turn-off removes all its jobs
            shown_medications.add(reminder.medication_id)
            med = meds.get(reminder.medication_id)
            text = locale.REMINDER_ITEM_MEDICATION.format(
                name=med.name if med else (reminder.payload or "?"),
                times=med.schedule if med else "?",
                when=when,
            )
            keyboard = _off_keyboard(callbacks.medication_off(reminder.medication_id))
        elif reminder.type == reminders.TYPE_CHECKIN:
            text = locale.REMINDER_ITEM_CHECKIN.format(when=when)
            keyboard = _off_keyboard(callbacks.reminder_off(reminder.id))
        else:  # repeat_lab
            text = locale.REMINDER_ITEM_REPEAT_LAB.format(name=reminder.payload or "?", when=when)
            keyboard = _off_keyboard(callbacks.reminder_off(reminder.id))
        await message.answer(text, reply_markup=keyboard)


@router.message(Command("reminders"))
async def cmd_reminders(message: Message, reminder_scheduler: ReminderScheduler) -> None:
    tg = _telegram_id(message)
    if tg is None:
        return
    await open_reminders(message, tg, reminder_scheduler)


@router.callback_query(F.data.startswith(callbacks.REMINDER_OFF + ":"))
async def on_reminder_off(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    reminder_id = callbacks.parse_reminder_off(callback.data or "")
    if reminder_id is None:
        await callback.answer()
        return
    async with get_session() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is not None:
            await proactive.turn_off_reminder(
                session, reminder=reminder, scheduler=reminder_scheduler
            )
            await session.commit()
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.REMINDER_TURNED_OFF)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.MEDICATION_OFF + ":"))
async def on_medication_off(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    medication_id = callbacks.parse_medication_off(callback.data or "")
    if medication_id is None:
        await callback.answer()
        return
    async with get_session() as session:
        await proactive.turn_off_medication(
            session, medication_id=medication_id, scheduler=reminder_scheduler
        )
        await session.commit()
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.REMINDER_TURNED_OFF)
    await callback.answer()
