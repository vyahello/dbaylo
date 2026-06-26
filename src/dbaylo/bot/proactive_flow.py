"""Proactive-behavior commands: problems (active concerns), medications, reminders.

Thin aiogram handlers over the ``companion`` coordinator. The live
:class:`ReminderScheduler` is injected from ``dispatcher["reminder_scheduler"]`` so
creating/resolving a problem or adding/removing a medication updates the running
schedule immediately (no restart).
"""

from __future__ import annotations

import contextlib
import html
from datetime import date, datetime

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.bot.keyboards import cancel_keyboard, remove_button_row
from dbaylo.companion import (
    callbacks,
    concerns,
    goals,
    grouping,
    health,
    medications,
    proactive,
    reminders,
)
from dbaylo.companion.health import HealthFinding
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


_WATCH_CAT = "watch"  # the on-the-edge pseudo-category key (shares the category-detail plumbing)

_PROBLEM_LINE = {
    "high": locale.PROBLEM_LINE_HIGH,
    "low": locale.PROBLEM_LINE_LOW,
    "watch": locale.PROBLEM_LINE_WATCH,
    "flag": locale.PROBLEM_LINE_FLAG,
}


def _short(name: str, limit: int = 26) -> str:
    """Trim a name for an inline button (so a long analyte still fits + stays distinct)."""
    name = name.strip()
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def _finding_line(finding: HealthFinding, *, name: str) -> str:
    template = _PROBLEM_LINE.get(finding.kind, locale.PROBLEM_LINE_FLAG)
    return template.format(name=name, value=finding.value, ref=finding.ref)


def _split_proposals(
    proposals: list[HealthFinding],
) -> tuple[list[tuple[int, HealthFinding]], list[tuple[int, HealthFinding]]]:
    """Partition the flat proposal list into (current-out-of-range, watch), keeping each finding's
    ORIGINAL flat index — track/dismiss re-derive the same flat list and address by that index."""
    current = [(i, f) for i, f in enumerate(proposals) if f.kind != "watch"]
    watch = [(i, f) for i, f in enumerate(proposals) if f.kind == "watch"]
    return current, watch


def _category_counts(current: list[tuple[int, HealthFinding]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _i, f in current:
        counts[f.category] = counts.get(f.category, 0) + 1
    return counts


async def _problems_top(session: AsyncSession, *, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """The grouped top level: one button per clinical category that has something out of range, then
    📈 на межі, ✅ вже відстежую, 🙈 приховані, ➕ своя проблема. A digest, never a wall."""
    proposals = await health.propose_problems(session, user_id, today=date.today())
    current, watch = _split_proposals(proposals)
    counts = _category_counts(current)
    active = await concerns.list_active(session, user_id=user_id)
    active_goals = await goals.list_active_goals(session, user_id=user_id)
    # Only dismissals that are STILL off — a waved-off finding that returned to range is not shown
    # (restoring it would do nothing), so 🙈 Приховані appears only with something real to restore.
    dismissed = await health.list_relevant_dismissed(session, user_id, today=date.today())

    kb: list[list[InlineKeyboardButton]] = []
    for cat in grouping.CATEGORY_ORDER:
        n = counts.get(cat, 0)
        if not n:
            continue
        label = locale.CATEGORY_NAMES.get(cat, cat)
        kb.append(
            [
                InlineKeyboardButton(
                    text=locale.BTN_PROBLEM_CATEGORY.format(label=label, n=n),
                    callback_data=callbacks.problem_category(cat),
                )
            ]
        )
    if watch:
        kb.append(
            [
                InlineKeyboardButton(
                    text=locale.BTN_PROBLEM_WATCH.format(n=len(watch)),
                    callback_data=callbacks.problem_category(_WATCH_CAT),
                )
            ]
        )
    if active:
        kb.append(
            [
                InlineKeyboardButton(
                    text=locale.BTN_PROBLEM_TRACKED.format(n=len(active)),
                    callback_data=callbacks.PROBLEM_TRACKED,
                )
            ]
        )
    # Goals folded into the same screen (they proposed the same findings as the problems): one
    # 🎯 Мої цілі group → the goals view. Always shown so a goal can be added even with none yet.
    kb.append(
        [
            InlineKeyboardButton(
                text=locale.BTN_PROBLEM_GOALS.format(n=len(active_goals)),
                callback_data=callbacks.MENU_OPEN_GOALS,
            )
        ]
    )
    if dismissed:
        kb.append(
            [
                InlineKeyboardButton(
                    text=locale.BTN_PROBLEM_DISMISSED.format(n=len(dismissed)),
                    callback_data=callbacks.PROBLEM_DISMISSED,
                )
            ]
        )
    kb.append(
        [
            InlineKeyboardButton(
                text=locale.BTN_PROBLEM_ADD_MANUAL, callback_data=callbacks.MENU_PROB_NEW
            )
        ]
    )
    if counts:
        text = locale.PROBLEM_GROUP_HEADER
    elif watch or active:
        text = locale.PROBLEM_GROUP_NOTHING_OFF
    else:
        text = locale.PROBLEM_ALL_CLEAR
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


async def _category_detail(
    session: AsyncSession, *, user_id: int, category: str
) -> tuple[str, InlineKeyboardMarkup] | None:
    """One category's out-of-range findings (or the watch list when ``category`` is ``watch``), each
    a 👁 track / ✖ dismiss row. ``None`` when the group is now empty (caller falls back to top)."""
    proposals = await health.propose_problems(session, user_id, today=date.today())
    current, watch = _split_proposals(proposals)
    if category == _WATCH_CAT:
        items, header, qualify = watch, locale.PROBLEM_WATCH_HEADER, True
    else:
        items = [(i, f) for i, f in current if f.category == category]
        label = locale.CATEGORY_NAMES.get(category, category)
        header, qualify = locale.PROBLEM_CAT_HEADER.format(label=label), False
    if not items:
        return None
    # In a single-category detail the header already says the specimen, so show the bare name; the
    # watch list can mix specimens, so qualify there (Еритроцити (сеча)).
    lines = [header, ""]
    kb: list[list[InlineKeyboardButton]] = []
    for index, finding in items:
        shown = finding.display_name if qualify else finding.name
        lines.append(_finding_line(finding, name=shown))
        kb.append(
            [
                InlineKeyboardButton(
                    text=locale.BTN_PROBLEM_TRACK.format(name=_short(shown)),
                    callback_data=callbacks.problem_track(category, index),
                ),
                InlineKeyboardButton(
                    text=locale.BTN_PROBLEM_DISMISS,
                    callback_data=callbacks.problem_dismiss(category, index),
                ),
            ]
        )
    kb.append(
        [InlineKeyboardButton(text=locale.BTN_PROBLEM_BACK, callback_data=callbacks.PROBLEM_BACK)]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


async def _tracked_detail(
    session: AsyncSession, *, user_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    """The already-tracked concerns: ✅ resolve / ✏️ rename each, then back."""
    active = await concerns.list_active(session, user_id=user_id)
    kb: list[list[InlineKeyboardButton]] = []
    for condition in active:
        kb.append(
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
        )
    kb.append(
        [InlineKeyboardButton(text=locale.BTN_PROBLEM_BACK, callback_data=callbacks.PROBLEM_BACK)]
    )
    return locale.PROBLEM_TRACKED_HEADER, InlineKeyboardMarkup(inline_keyboard=kb)


async def _dismissed_detail(
    session: AsyncSession, *, user_id: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    """The still-off waved-off findings, each with ↩️ to restore it. ``None`` when none remain."""
    dismissed = await health.list_relevant_dismissed(session, user_id, today=date.today())
    if not dismissed:
        return None
    kb: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=locale.BTN_PROBLEM_RESTORE.format(name=condition.name),
                callback_data=callbacks.problem_restore(condition.id),
            )
        ]
        for condition in dismissed
    ]
    kb.append(
        [InlineKeyboardButton(text=locale.BTN_PROBLEM_BACK, callback_data=callbacks.PROBLEM_BACK)]
    )
    return locale.PROBLEM_DISMISSED_HEADER, InlineKeyboardMarkup(inline_keyboard=kb)


async def open_problems(message: Message, telegram_id: int) -> None:
    """The agent's read of your problems, grouped by category (drill into one), plus what you
    already track and what you waved off. Propose-then-confirm — the agent never decides."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        text, keyboard = await _problems_top(session, user_id=user.id)
    await message.answer(text, reply_markup=keyboard)


async def _edit_to_top(callback: CallbackQuery, *, note: str = "") -> None:
    """Re-render the grouped top level in place (after an action / «Назад»). ``note`` is a
    persistent confirmation line prepended when the acted-on group became empty and we fell here."""
    tg = _telegram_id(callback)
    if tg is None or not isinstance(callback.message, Message):
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        text, keyboard = await _problems_top(session, user_id=user.id)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(note + text, reply_markup=keyboard)


async def _edit_to_detail(
    callback: CallbackQuery,
    builder: str,
    *,
    category: str = "",
    note: str = "",
) -> None:
    """Edit the message into a detail view; fall back to the top level when the detail is empty.
    ``note`` is a persistent confirmation line prepended above the view (after a 👁/✖ tap)."""
    tg = _telegram_id(callback)
    if tg is None or not isinstance(callback.message, Message):
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        if builder == "category":
            built = await _category_detail(session, user_id=user.id, category=category)
        elif builder == "dismissed":
            built = await _dismissed_detail(session, user_id=user.id)
        else:  # tracked
            built = await _tracked_detail(session, user_id=user.id)
    if built is None:
        await _edit_to_top(callback, note=note)
        return
    text, keyboard = built
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(note + text, reply_markup=keyboard)


@router.callback_query(F.data == callbacks.PROBLEM_BACK)
async def on_problem_back(callback: CallbackQuery) -> None:
    await _edit_to_top(callback)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.PROBLEM_CAT + ":"))
async def on_problem_category(callback: CallbackQuery) -> None:
    """Open one category's (or the watch) out-of-range detail."""
    category = callbacks.parse_problem_category(callback.data or "")
    if category is None:
        await callback.answer()
        return
    await _edit_to_detail(callback, "category", category=category)
    await callback.answer()


@router.callback_query(F.data == callbacks.PROBLEM_TRACKED)
async def on_problem_tracked(callback: CallbackQuery) -> None:
    await _edit_to_detail(callback, "tracked")
    await callback.answer()


@router.callback_query(F.data == callbacks.PROBLEM_DISMISSED)
async def on_problem_dismissed(callback: CallbackQuery) -> None:
    await _edit_to_detail(callback, "dismissed")
    await callback.answer()


async def _act_on_proposal(
    callback: CallbackQuery,
    *,
    category: str,
    index: int,
    scheduler: ReminderScheduler,
    track: bool,
) -> tuple[str, str]:
    """Track (👁) or dismiss (✖) the proposal at ``index`` in the freshly-derived flat list. Returns
    ``(toast, note)`` — a brief toast AND a persistent line prepended to the re-rendered view so the
    user SEES what happened and where the finding went. The DISPLAY name (specimen-qualified) is
    persisted so a urine/blood twin is never confused."""
    tg = _telegram_id(callback)
    if tg is None:
        return "", ""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        proposals = await health.propose_problems(session, user.id, today=date.today())
        if not 0 <= index < len(proposals):
            return "", ""
        name = proposals[index].display_name
        if track:
            await proactive.add_problem(session, user=user, name=name, scheduler=scheduler)
            toast, note = locale.PROBLEM_TRACK_TOAST, locale.PROBLEM_TRACK_NOTE.format(name=name)
        else:
            await proactive.dismiss_problem(session, user=user, name=name, scheduler=scheduler)
            toast, note = (
                locale.PROBLEM_DISMISS_TOAST,
                locale.PROBLEM_DISMISS_NOTE.format(name=name),
            )
        await session.commit()
    return toast, note


@router.callback_query(F.data.startswith(callbacks.PROBLEM_TRACK + ":"))
async def on_problem_track(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    """Track an AI-proposed finding (👁): create the concern + schedule the daily check-in, then
    re-render the same detail (or the top when that group is now empty), led by a confirmation."""
    parsed = callbacks.parse_problem_track(callback.data or "")
    if parsed is None:
        await callback.answer()
        return
    category, index = parsed
    toast, note = await _act_on_proposal(
        callback, category=category, index=index, scheduler=reminder_scheduler, track=True
    )
    await callback.answer(toast)
    await _edit_to_detail(callback, "category", category=category, note=note)


@router.callback_query(F.data.startswith(callbacks.PROBLEM_DISMISS + ":"))
async def on_problem_dismiss(
    callback: CallbackQuery, reminder_scheduler: ReminderScheduler
) -> None:
    """Wave off an AI-proposed finding (✖): remember it DISMISSED (reversible from 🙈 Приховані),
    then re-render the detail (or the top when that group is now empty), led by a confirmation."""
    parsed = callbacks.parse_problem_dismiss(callback.data or "")
    if parsed is None:
        await callback.answer()
        return
    category, index = parsed
    toast, note = await _act_on_proposal(
        callback, category=category, index=index, scheduler=reminder_scheduler, track=False
    )
    await callback.answer(toast)
    await _edit_to_detail(callback, "category", category=category, note=note)


@router.callback_query(F.data.startswith(callbacks.PROBLEM_RESTORE + ":"))
async def on_problem_restore(
    callback: CallbackQuery, reminder_scheduler: ReminderScheduler
) -> None:
    """↩️ Restore a wrongly-waved-off finding: drop its dismissal so it's proposed again."""
    condition_id = callbacks.parse_problem_restore(callback.data or "")
    tg = _telegram_id(callback)
    if condition_id is None or tg is None:
        await callback.answer()
        return
    toast = ""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        restored = await proactive.restore_problem(
            session, user_id=user.id, condition_id=condition_id, scheduler=reminder_scheduler
        )
        await session.commit()
        if restored is not None:
            toast = locale.PROBLEM_RESTORE_TOAST
    await callback.answer(toast)
    await _edit_to_detail(callback, "dismissed")


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


async def _live_medications(session: AsyncSession, *, user_id: int) -> list[Medication]:
    """The medications with at least one ACTIVE reminder (a turned-off med leaves the list, though
    its record + dose are kept), in their reminder order."""
    rows = await reminders.active_reminders_for_user(session, user_id=user_id)
    order: list[int] = []
    seen: set[int] = set()
    for reminder in rows:
        mid = reminder.medication_id
        if reminder.type == reminders.TYPE_MEDICATION and mid is not None and mid not in seen:
            seen.add(mid)
            order.append(mid)
    meds = {m.id: m for m in await medications.list_medications(session, user_id=user_id)}
    return [meds[mid] for mid in order if mid in meds]


async def _medications_payload(
    session: AsyncSession, *, user_id: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    """The medications MASTER: one short `💊 <name>` button per live medication — a tap OPENS its
    card (read), it never destructively turns it off (that's a deliberate button in the card)."""
    live = await _live_medications(session, user_id=user_id)
    if not live:
        return locale.MED_LIST_EMPTY, None
    rows = [
        [
            InlineKeyboardButton(
                text=locale.BTN_MED_VIEW.format(name=_short(med.name)),
                callback_data=callbacks.medication_view(med.id, "m"),
            )
        ]
        for med in live
    ]
    return locale.MED_LIST_HEADER, InlineKeyboardMarkup(inline_keyboard=rows)


async def open_medications(message: Message, telegram_id: int) -> None:
    """The medications list (💊 Список ліків) — short names, tap one to read its card."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        text, keyboard = await _medications_payload(session, user_id=user.id)
    await message.answer(text, reply_markup=keyboard)


async def _edit_to_meds(callback: CallbackQuery) -> None:
    """Edit the current message back into the (refreshed) medications list."""
    tg = _telegram_id(callback)
    if tg is None or not isinstance(callback.message, Message):
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        text, keyboard = await _medications_payload(session, user_id=user.id)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(F.data == callbacks.MED_LIST_BACK)
async def on_med_list_back(callback: CallbackQuery) -> None:
    await _edit_to_meds(callback)
    await callback.answer()


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
    reminder's card; medications collapse to one row (their card turns off all times). The daily
    check-in is AGENT-managed, so it is surfaced as an INFO line above the list (never a deletable
    row — deleting it would just be re-created from the active concerns)."""
    rows = await reminders.active_reminders_for_user(session, user_id=user_id)
    meds = {m.id: m for m in await medications.list_medications(session, user_id=user_id)}
    next_run = {job.id: job.next_run for job in scheduler.list_jobs()}

    checkin = next((r for r in rows if r.type == reminders.TYPE_CHECKIN), None)
    manageable = [r for r in rows if r.type != reminders.TYPE_CHECKIN]
    info = (
        locale.REMINDER_CHECKIN_MANAGED.format(
            when=_fmt_when(next_run.get(f"reminder:{checkin.id}"))
        )
        if checkin is not None
        else ""
    )

    if not manageable:  # only the agent-managed check-in (or nothing at all)
        if info:
            return f"{info}\n\n{locale.REMINDERS_NONE_MANUAL}", None
        return locale.REMINDERS_EMPTY, None

    kb_rows: list[list[InlineKeyboardButton]] = []
    shown_medications: set[int] = set()
    for reminder in manageable:
        when = _fmt_when(next_run.get(f"reminder:{reminder.id}"))
        if reminder.type == reminders.TYPE_MEDICATION and reminder.medication_id is not None:
            if reminder.medication_id in shown_medications:
                continue  # one row per medication; its card turns off all its times
            shown_medications.add(reminder.medication_id)
            label = _reminder_label(reminder, meds.get(reminder.medication_id), when)
            data = callbacks.medication_view(reminder.medication_id, "r")
        else:
            label = _reminder_label(reminder, None, when)
            data = callbacks.reminder_view(reminder.id)
        kb_rows.append([InlineKeyboardButton(text=label, callback_data=data)])
    header = f"{info}\n\n{locale.REMINDERS_HEADER}" if info else locale.REMINDERS_HEADER
    return header, InlineKeyboardMarkup(inline_keyboard=kb_rows)


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


def _med_card(med: Medication, *, when: str) -> str:
    """The medication card (escaped HTML): name · dose (a record) · times · next run · hint."""
    lines = [locale.MED_CARD_TITLE.format(name=html.escape(med.name))]
    if med.dose:
        lines.append(locale.MED_CARD_DOSE.format(dose=html.escape(med.dose)))
    lines.append(locale.MED_CARD_TIMES.format(times=html.escape(med.schedule or "?")))
    lines.append(locale.MED_CARD_NEXT.format(when=when))
    lines.append("")
    lines.append(locale.MED_CARD_HINT)
    return "\n".join(lines)


def _med_card_keyboard(medication_id: int, origin: str) -> InlineKeyboardMarkup:
    """The med card's actions: turn the reminders off (deliberate), or back to the list it came from
    (the 💊 meds list or the 🔔 reminders list)."""
    back = callbacks.MED_LIST_BACK if origin == "m" else callbacks.REMINDERS_BACK
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=locale.BTN_MED_TURN_OFF,
                    callback_data=callbacks.medication_off(medication_id, origin),
                ),
                InlineKeyboardButton(text=locale.BTN_REMINDER_BACK, callback_data=back),
            ]
        ]
    )


@router.callback_query(F.data.startswith(callbacks.MEDICATION_VIEW + ":"))
async def on_medication_view(
    callback: CallbackQuery, reminder_scheduler: ReminderScheduler
) -> None:
    """Open a medication's card (read): name · DOSE (record) · times · next run. Turning the
    reminders off is a deliberate button — not this tap."""
    parsed = callbacks.parse_medication_view(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    medication_id, origin = parsed
    next_run = {job.id: job.next_run for job in reminder_scheduler.list_jobs()}
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        med = await session.get(Medication, medication_id)
        rems = [
            r
            for r in await reminders.active_reminders_for_user(session, user_id=user.id)
            if r.medication_id == medication_id
        ]
    if med is None or not rems:  # gone meanwhile — show the fresh list it came from
        await (
            _edit_to_meds(callback)
            if origin == "m"
            else _edit_to_list(callback, reminder_scheduler)
        )
        await callback.answer()
        return
    times = [w for r in rems if (w := next_run.get(f"reminder:{r.id}")) is not None]
    when = _fmt_when(min(times) if times else None)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(
            _med_card(med, when=when),
            reply_markup=_med_card_keyboard(medication_id, origin),
            parse_mode=ParseMode.HTML,
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
    """🔕 Turn a medication's reminders off from its card (deliberate) — the Medication row + dose
    stay (a record, not a wipe), then return to the list the card was opened from."""
    parsed = callbacks.parse_medication_off(callback.data or "")
    if parsed is None:
        await callback.answer()
        return
    medication_id, origin = parsed
    async with get_session() as session:
        await proactive.turn_off_medication(
            session, medication_id=medication_id, scheduler=reminder_scheduler
        )
        await session.commit()
    await callback.answer(locale.MED_TURNED_OFF_TOAST)
    if origin == "m":
        await _edit_to_meds(callback)
    else:
        await _edit_to_list(callback, reminder_scheduler)
