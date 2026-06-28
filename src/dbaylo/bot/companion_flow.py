"""Companion bot flow (aiogram 3): goals, the real check-in, and free-text chat.

Thin handlers over the companion logic:

* ``/goal [text]`` — set a wellness goal (guardrailed before it is accepted).
* ``/goals`` — list goals.
* ``/checkin`` — send the gentle prompt, then parse the reply (symptoms -> triage).
* free text (no active FSM state) — companion chat, routed through the safety cores.

DB access reuses :func:`dbaylo.labs.intake.ensure_user`. The free-text handler is
``StateFilter(None)`` so it never steals a turn from the lab-edit or check-in FSM.
"""

from __future__ import annotations

import contextlib
import html
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.bot import consult_flow, navigator_flow
from dbaylo.bot.formatting import answer_chunked, render_companion_html
from dbaylo.bot.keyboards import cancel_keyboard
from dbaylo.bot.typing import keep_typing
from dbaylo.companion import (
    callbacks,
    checkin,
    consult_memory,
    goals,
    grouping,
    health,
    intake,
    proactive,
)
from dbaylo.companion.conversation import Turn, generate_reply
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.config import get_settings
from dbaylo.db import get_session
from dbaylo.db.models import GoalStatus
from dbaylo.labs.intake import ensure_user
from dbaylo.safety import GateSource, screen

router = Router(name="companion")


class GoalStates(StatesGroup):
    waiting_for_goal = State()


class CheckinStates(StatesGroup):
    waiting_for_answer = State()


class IntakeStates(StatesGroup):
    in_progress = State()


def _telegram_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


# --- Goals ----------------------------------------------------------------------


async def start_goal_dialog(message: Message, state: FSMContext) -> None:
    """Enter the goal dialog (from /goal or the menu) — always cancellable."""
    await state.set_state(GoalStates.waiting_for_goal)
    await message.answer(locale.GOAL_ASK_TEXT, reply_markup=cancel_keyboard())


@router.message(Command("goal"))
async def cmd_goal(message: Message, command: CommandObject, state: FSMContext) -> None:
    text = (command.args or "").strip()
    if not text:
        await start_goal_dialog(message, state)
        return
    await _save_goal(message, text)


@router.message(GoalStates.waiting_for_goal, F.text)
async def on_goal_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _save_goal(message, (message.text or "").strip())


async def _save_goal(message: Message, text: str) -> None:
    tg_id = _telegram_id(message)
    if tg_id is None:
        return
    if not text.strip():
        await message.answer(locale.NOTHING_SAVED)  # blank input -> never a phantom goal
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg_id)
        result = await goals.set_goal(session, user=user, text=text)
        await session.commit()
    await message.answer(result.message)


def _short_goal(text: str, limit: int = 32) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def _goals_master(session: AsyncSession, *, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """The goals screen (🩺 Моє здоровʼя → 🎯 Мої цілі — its own hub button): SUGGESTS goals
    from your problems ("Привести X до норми") + generic wellness ones (🎯), then your adopted goals
    (📌), then a 🗄 archive of closed goals you can restore. A tap opens a goal's detail (action
    there)."""
    suggestions = await goals.propose_goals(session, user_id, today=date.today())
    current = await goals.list_active_goals(session, user_id=user_id)
    closed = await goals.list_closed_goals(session, user_id=user_id)
    lines: list[str] = [locale.GOAL_MASTER_HEADER]
    kb: list[list[InlineKeyboardButton]] = []
    if suggestions:
        lines.append("")
        lines.append(locale.GOAL_MASTER_SUGGEST_LABEL)
        for index, sug in enumerate(suggestions):
            prefix = grouping.category_emoji(sug.subject)  # 🩸/🔬/⚗️ — which аналіз it touches
            lines.append(locale.GOAL_MASTER_ITEM_LINE.format(goal=f"{prefix}{sug.text}"))
            kb.append(
                [
                    InlineKeyboardButton(
                        text=locale.BTN_GOAL_VIEW_SUG.format(
                            subject=f"{prefix}{_short_goal(sug.subject)}"
                        ),
                        callback_data=callbacks.goal_view_sug(index),
                    )
                ]
            )
    if current:
        lines.append("")
        lines.append(locale.GOAL_MASTER_MINE_LABEL)
        for goal in current:
            subject = goals.target_subject(goal.target or "") or (goal.target or "")
            prefix = grouping.category_emoji(subject)
            lines.append(locale.GOAL_MASTER_ITEM_LINE.format(goal=f"{prefix}{goal.target or ''}"))
            kb.append(
                [
                    InlineKeyboardButton(
                        text=locale.BTN_GOAL_VIEW.format(subject=f"{prefix}{_short_goal(subject)}"),
                        callback_data=callbacks.goal_view(goal.id),
                    )
                ]
            )
    if not suggestions and not current:
        lines = [locale.GOAL_ALL_SET]
    if closed:  # the archive of achieved/abandoned goals — review & restore
        kb.append(
            [
                InlineKeyboardButton(
                    text=locale.BTN_GOAL_ARCHIVE.format(n=len(closed)),
                    callback_data=callbacks.GOAL_ARCHIVE,
                )
            ]
        )
    kb.append(
        [InlineKeyboardButton(text=locale.BTN_GOAL_OWN, callback_data=callbacks.MENU_GOAL_NEW)]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


async def _goals_archive(
    session: AsyncSession, *, user_id: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    """The 🗄 archive of CLOSED goals (achieved 🎉 / abandoned 🗑), each `[↩️ subject]` → restore to
    ACTIVE. ``None`` when none — so the «🗄 Закриті» button shows only with something inside."""
    closed = await goals.list_closed_goals(session, user_id=user_id)
    if not closed:
        return None
    kb: list[list[InlineKeyboardButton]] = []
    for goal in closed:
        mark = (
            locale.GOAL_ARCHIVE_MARK_ACHIEVED
            if goal.status == GoalStatus.ACHIEVED
            else locale.GOAL_ARCHIVE_MARK_ABANDONED
        )
        subject = goals.target_subject(goal.target or "") or (goal.target or "")
        prefix = grouping.category_emoji(subject)
        kb.append(
            [
                InlineKeyboardButton(
                    text=locale.BTN_GOAL_REOPEN.format(
                        mark=mark, subject=f"{prefix}{_short_goal(subject)}"
                    ),
                    callback_data=callbacks.goal_reopen(goal.id),
                )
            ]
        )
    kb.append([InlineKeyboardButton(text=locale.BTN_GOAL_BACK, callback_data=callbacks.GOAL_BACK)])
    return locale.GOAL_ARCHIVE_HEADER, InlineKeyboardMarkup(inline_keyboard=kb)


def _direction_word(finding: health.HealthFinding) -> str:
    return {
        "high": locale.GOAL_DETAIL_DIR_HIGH,
        "low": locale.GOAL_DETAIL_DIR_LOW,
    }.get(finding.kind, locale.GOAL_DETAIL_DIR_OOR)


async def _history_lines(session: AsyncSession, user_id: int, *, series_key: str) -> list[str]:
    """The analyte's 'when was it out of range' timeline as escaped Ukrainian lines (or a note)."""
    history = await health.indicator_history(session, user_id, series_key=series_key)
    if not history:
        return [locale.GOAL_DETAIL_NO_HISTORY]
    out = [locale.GOAL_DETAIL_HISTORY_HEADER]
    for point in history:
        day = point.date.isoformat() if point.date else "?"
        mark = locale.GOAL_HISTORY_MARK_OOR if point.out_of_range else locale.GOAL_HISTORY_MARK_OK
        out.append(
            locale.GOAL_HISTORY_LINE.format(
                date=day, value=html.escape(point.value), ref=html.escape(point.ref), mark=mark
            )
        )
    return out


def _detail_keyboard(*buttons: tuple[str, str]) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text=text, callback_data=data) for text, data in buttons]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            row,
            [InlineKeyboardButton(text=locale.BTN_GOAL_BACK, callback_data=callbacks.GOAL_BACK)],
        ]
    )


async def _suggestion_detail(
    session: AsyncSession, user_id: int, index: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    """A suggestion's detail: full title + a 🎯 Взяти ціль action. ``None`` when the index no longer
    resolves (caller falls back). Only wellness suggestions are shown now (findings live in
    ⚕️ Проблеми)."""
    suggestions = await goals.propose_goals(session, user_id, today=date.today())
    if not 0 <= index < len(suggestions):
        return None
    sug = suggestions[index]
    prefix = grouping.category_emoji(sug.subject)
    lines = [locale.GOAL_DETAIL_SUG_TITLE.format(goal=f"{prefix}{html.escape(sug.text)}"), ""]
    if sug.series_key:
        finding = await goals.goal_analyte(session, user_id, target=sug.text, today=date.today())
        if finding is not None:
            lines.append(
                locale.GOAL_DETAIL_CURRENT.format(
                    value=html.escape(finding.value),
                    ref=html.escape(finding.ref),
                    direction=_direction_word(finding),
                )
            )
            lines.append("")
        lines.extend(await _history_lines(session, user_id, series_key=sug.series_key))
    else:
        lines.append(locale.GOAL_DETAIL_GENERIC)
    kb = _detail_keyboard((locale.BTN_GOAL_ADOPT_DETAIL, callbacks.goal_adopt(index)))
    return "\n".join(lines), kb


async def _goal_detail(
    session: AsyncSession, user_id: int, goal_id: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    """An adopted goal's detail: full title + the indicator history, with ✅ Досягнута / 🗑 Прибрати.
    ``None`` when the goal no longer exists."""
    goals_active = await goals.list_active_goals(session, user_id=user_id)
    goal = next((g for g in goals_active if g.id == goal_id), None)
    if goal is None:
        return None
    target = goal.target or ""
    subject = goals.target_subject(target) or target
    prefix = grouping.category_emoji(subject)
    lines = [locale.GOAL_DETAIL_MINE_TITLE.format(goal=f"{prefix}{html.escape(target)}"), ""]
    finding = await goals.goal_analyte(session, user_id, target=target, today=date.today())
    if finding is not None and finding.series_key:
        lines.extend(await _history_lines(session, user_id, series_key=finding.series_key))
    else:
        lines.append(locale.GOAL_DETAIL_GENERIC)
    kb = _detail_keyboard(
        (locale.BTN_GOAL_ACHIEVE_DETAIL, callbacks.goal_achieve(goal_id)),
        (locale.BTN_GOAL_REMOVE_DETAIL, callbacks.goal_remove(goal_id)),
    )
    return "\n".join(lines), kb


async def open_goals_screen(message: Message, telegram_id: int) -> None:
    """The agent-driven goals master (the 🎯 Цілі entry): short subjects, tap one for its detail."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        text, keyboard = await _goals_master(session, user_id=user.id)
    await message.answer(text, reply_markup=keyboard)


async def _edit_to_master(callback: CallbackQuery) -> None:
    """Re-render the goals master in place (after an action / «Назад»)."""
    tg = callback.from_user.id if callback.from_user else None
    if tg is None or not isinstance(callback.message, Message):
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        text, keyboard = await _goals_master(session, user_id=user.id)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(text, reply_markup=keyboard)


async def _edit_to_detail(
    callback: CallbackQuery, built: tuple[str, InlineKeyboardMarkup] | None
) -> None:
    if not isinstance(callback.message, Message):
        return
    if built is None:
        await _edit_to_master(callback)
        return
    text, keyboard = built
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == callbacks.GOAL_BACK)
async def on_goal_back(callback: CallbackQuery) -> None:
    await _edit_to_master(callback)
    await callback.answer()


@router.callback_query(F.data == callbacks.GOAL_ARCHIVE)
async def on_goal_archive(callback: CallbackQuery) -> None:
    """🗄 Закриті цілі — open the closed-goals archive (each re-openable)."""
    tg = callback.from_user.id if callback.from_user else None
    if tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        built = await _goals_archive(session, user_id=user.id)
    await _edit_to_detail(callback, built)  # None -> back to master (archive emptied meanwhile)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.GOAL_REOPEN + ":"))
async def on_goal_reopen(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    """↩️ Restore a closed goal → ACTIVE (check-in reconciled), then re-render the archive (or back
    to the master when it is now empty)."""
    goal_id = callbacks.parse_goal_reopen(callback.data or "")
    tg = callback.from_user.id if callback.from_user else None
    if goal_id is None or tg is None:
        await callback.answer()
        return
    toast = ""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        if await goals.reactivate_goal(session, goal_id=goal_id, user_id=user.id) is not None:
            await proactive.reconcile_checkin(session, user=user, scheduler=reminder_scheduler)
            toast = locale.GOAL_REOPEN_TOAST
        await session.commit()
        built = await _goals_archive(session, user_id=user.id)
    await callback.answer(toast)
    await _edit_to_detail(callback, built)


@router.callback_query(F.data.startswith(callbacks.GOAL_VIEW_SUG + ":"))
async def on_goal_view_sug(callback: CallbackQuery) -> None:
    """Open a suggestion's detail (full title + indicator history + 🎯 Взяти ціль)."""
    index = callbacks.parse_goal_view_sug(callback.data or "")
    tg = callback.from_user.id if callback.from_user else None
    if index is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        built = await _suggestion_detail(session, user.id, index)
    await _edit_to_detail(callback, built)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.GOAL_VIEW + ":"))
async def on_goal_view(callback: CallbackQuery) -> None:
    """Open an adopted goal's detail (full title + history + ✅ Досягнута / 🗑 Прибрати)."""
    goal_id = callbacks.parse_goal_view(callback.data or "")
    tg = callback.from_user.id if callback.from_user else None
    if goal_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        built = await _goal_detail(session, user.id, goal_id)
    await _edit_to_detail(callback, built)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.GOAL_ADOPT + ":"))
async def on_goal_adopt(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    """🎯 Взяти ціль (from a suggestion detail): adopt by index (re-derived), guardrail vets it,
    then back to the master (the suggestion is now an adopted goal). Adopting a goal turns ON the
    daily check-in so Дбайло proactively follows up on it."""
    index = callbacks.parse_goal_adopt(callback.data or "")
    tg = callback.from_user.id if callback.from_user else None
    if index is None or tg is None:
        await callback.answer()
        return
    toast = ""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        suggestions = await goals.propose_goals(session, user.id, today=date.today())
        if 0 <= index < len(suggestions):
            result = await goals.set_goal(session, user=user, text=suggestions[index].text)
            if result.saved:
                await proactive.reconcile_checkin(session, user=user, scheduler=reminder_scheduler)
            await session.commit()
            toast = locale.GOAL_ADOPTED_TOAST if result.saved else locale.GOAL_NOT_ADOPTED
    await callback.answer(toast)
    await _edit_to_master(callback)


@router.callback_query(F.data.startswith(callbacks.GOAL_ACHIEVE + ":"))
async def on_goal_achieve(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    """✅ Mark a goal achieved (from its detail), then back to the master. Reconcile so a
    now-pointless daily check-in retires when this was the last reason for it."""
    goal_id = callbacks.parse_goal_achieve(callback.data or "")
    tg = callback.from_user.id if callback.from_user else None
    if goal_id is None or tg is None:
        await callback.answer()
        return
    toast = ""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        if await goals.achieve_goal(session, goal_id=goal_id, user_id=user.id) is not None:
            await proactive.reconcile_checkin(session, user=user, scheduler=reminder_scheduler)
            await session.commit()
            toast = locale.GOAL_ACHIEVED_TOAST
    await callback.answer(toast)
    await _edit_to_master(callback)


@router.callback_query(F.data.startswith(callbacks.GOAL_REMOVE + ":"))
async def on_goal_remove(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    """🗑 Drop a goal (undo an accidental adopt, from its detail), then back to the master. Reconcile
    the check-in (it may have been the only reason for it)."""
    goal_id = callbacks.parse_goal_remove(callback.data or "")
    tg = callback.from_user.id if callback.from_user else None
    if goal_id is None or tg is None:
        await callback.answer()
        return
    toast = ""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        if await goals.remove_goal(session, goal_id=goal_id, user_id=user.id) is not None:
            await proactive.reconcile_checkin(session, user=user, scheduler=reminder_scheduler)
            await session.commit()
            toast = locale.GOAL_REMOVED_TOAST
    await callback.answer(toast)
    await _edit_to_master(callback)


@router.message(Command("goals"))
async def cmd_goals(message: Message) -> None:
    tg_id = _telegram_id(message)
    if tg_id is None:
        return
    await open_goals_screen(message, tg_id)  # the rich, manageable screen — same as the menu tap


# --- Daily check-in -------------------------------------------------------------


async def start_checkin_dialog(
    message: Message, state: FSMContext, *, telegram_id: int | None = None
) -> None:
    """Begin a check-in: ask a GROUNDED prompt (about the user's real concerns + recent state, like
    the proactive one) and wait for the answer. Reused by /checkin and the 📝 Чек-ін button. Pass
    ``telegram_id`` explicitly when the prompt is sent on a CALLBACK message (whose ``from_user`` is
    the bot, not the owner) so the grounding still loads the right user."""
    await state.set_state(CheckinStates.waiting_for_answer)
    tg = telegram_id if telegram_id is not None else _telegram_id(message)
    if tg is None:
        await message.answer(checkin.build_prompt(), reply_markup=cancel_keyboard())
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        # Same grounding as the scheduled 10:00 check-in (incl. the rotating tracked-concern focus),
        # so the 📝 button is not a poorer version of the automatic one.
        context = await checkin.full_checkin_context(session, user_id=user.id, today=date.today())
    if not context:  # nothing to ground in -> the gentle generic prompt, instantly
        await message.answer(checkin.build_prompt(), reply_markup=cancel_keyboard())
        return
    # The grounded prompt is a multi-second claude call. Tell the user what we're doing (a bare
    # "typing…" reads as "waiting for unknown") with a placeholder we then EDIT into the prompt.
    placeholder = await message.answer(locale.CHECKIN_ANALYZING)
    async with keep_typing(message):
        prompt = await checkin.build_grounded_prompt(context)
    try:
        await placeholder.edit_text(prompt, reply_markup=cancel_keyboard())
    except TelegramBadRequest:  # a stale/uneditable placeholder -> just send the prompt
        await message.answer(prompt, reply_markup=cancel_keyboard())


@router.message(Command("checkin"))
async def cmd_checkin(message: Message, state: FSMContext) -> None:
    await start_checkin_dialog(message, state)


@router.message(CheckinStates.waiting_for_answer, F.text)
async def on_checkin_answer(
    message: Message, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
    await state.clear()
    tg_id = _telegram_id(message)
    if tg_id is None:
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer(locale.NOTHING_SAVED)  # blank answer -> no empty check-in row
        return
    # Log the check-in state (sleep / mood / their own words -> state memory) — but silently.
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg_id)
        await checkin.process_checkin(session, user=user, text=text)
        await session.commit()
    # Then CONTINUE the conversation instead of dead-ending at "Занотував": route the answer through
    # the same engine as any free-text turn — a symptom/complaint opens the history-taking interview
    # (clarifying questions + triage + next steps), else a grounded companion reply. The check-in is
    # a real conversation starter, not a one-shot logger.
    await _engage_with_text(message, state, text, reminder_scheduler)


# --- Symptom intake (history-taking) --------------------------------------------


async def _grounded_context(message: Message, *, exclude: frozenset[str] = frozenset()) -> str:
    """The user's full grounded picture for a chat turn: labs (profile + current/resolved
    out-of-range indicators), recent check-in STATE memory (how they've been), AND a MEMORY of
    earlier conversations (``consult_memory``) — so general chat / the symptom interview answer from
    THEIR real data, remember their state, and carry continuity across sessions like the consult.
    ``exclude`` drops memory turns already in the live transcript (no mid-conversation duplication).
    ``""`` when there is nothing to ground in."""
    tg = _telegram_id(message)
    if tg is None:
        return ""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        grounded = await checkin.grounded_context(session, user_id=user.id, today=date.today())
        memory = await consult_memory.recall_block(session, user_id=user.id, exclude=exclude)
    return "\n\n".join(part for part in (grounded, memory) if part)


async def _run_intake_turn(
    message: Message, state: FSMContext, transcript: list[Turn], *, context: str = ""
) -> None:
    async with keep_typing(message):  # 'typing…' stays up for the whole LLM call, not ~5 s
        reply = await intake.advance(transcript, context=context)
    transcript.append({"role": "assistant", "text": reply.text})
    keyboard = None
    if reply.done:
        await state.clear()
        # When the interview wraps up, offer the next-step actions one tap away: 🔔 set a reminder
        # (re-test / appointment) · 🏥 where to do the exam — so "що робити далі" is actionable.
        keyboard = consult_flow.chat_affordance_keyboard()
    else:
        await state.set_state(IntakeStates.in_progress)
        await state.update_data(intake=transcript)
    await answer_chunked(
        message, render_companion_html(reply.text), parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


@router.message(IntakeStates.in_progress, F.text & ~F.text.startswith("/"))
async def on_intake_turn(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    transcript = list(data.get("intake") or [])
    transcript.append({"role": "user", "text": message.text or ""})
    exclude = frozenset(t["text"].strip() for t in transcript if t.get("text"))
    await _run_intake_turn(
        message, state, transcript, context=await _grounded_context(message, exclude=exclude)
    )


# --- Free-text companion chat (only when no FSM flow is active) ------------------
# General chat is a CONTINUOUS, grounded, memory-backed thread — not a cold one-shot. The recent
# back-and-forth lives in FSM data (``chat_transcript``) under the catch-all StateFilter(None), so
# it threads across free-text turns and is wiped on any /command or menu tap (the reset middleware
# clears FSM data too). A long gap starts a fresh thread; substantive exchanges are saved to durable
# memory so a later conversation remembers them — the same memory the consult uses.
_CHAT_TTL = timedelta(hours=6)  # a free-text turn after this gap starts a new conversation thread
_CHAT_KEEP_TURNS = 6  # recent exchanges (×2 messages) kept threading in FSM data

# Bare greetings / acknowledgements not worth persisting to durable memory (they would clutter the
# 🧠 Памʼять "Загальні розмови" group). In-session threading still keeps them for the moment.
_TRIVIAL_TURNS = frozenset(
    {
        "привіт",
        "вітаю",
        "дякую",
        "дяки",
        "дякс",
        "ок",
        "окей",
        "ага",
        "так",
        "ні",
        "добре",
        "хай",
        "пока",
        "бувай",
        "доброго ранку",
        "добрий ранок",
        "добрий день",
        "добрий вечір",
        "на добраніч",
        "до побачення",
    }
)


def _now() -> datetime:
    return datetime.now(ZoneInfo(get_settings().timezone))


def _thread_fresh(ts: object) -> bool:
    """Whether a stored chat thread is still recent enough to continue (else start fresh)."""
    if not isinstance(ts, str):
        return False
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return False
    return (_now() - when) <= _CHAT_TTL


def _worth_remembering(text: str) -> bool:
    """A general-chat turn worth saving to durable memory: not a bare greeting / one-word ack."""
    stripped = text.strip().casefold()
    return len(stripped) >= 5 and stripped not in _TRIVIAL_TURNS


async def _remember_general(tg: int, user_text: str, assistant_text: str) -> None:
    """Persist a substantive general-chat exchange to durable cross-session memory (best-effort) —
    the GENERAL bucket (no report/analyte anchor), the same store the consult recalls from."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        await consult_memory.record_turn(session, user_id=user.id, role="user", text=user_text)
        await consult_memory.record_turn(
            session, user_id=user.id, role="assistant", text=assistant_text
        )
        await session.commit()


async def _run_companion_turn(message: Message, state: FSMContext, text: str) -> None:
    """One ordinary companion turn: thread on the recent history, ground in the user's data +
    memory, answer in the SAME conversation, and remember the substantive exchange."""
    data = await state.get_data()
    history: list[Turn] = (
        list(data.get("chat_transcript") or []) if _thread_fresh(data.get("chat_ts")) else []
    )
    continuation = bool(history)  # the full disclaimer rides only the FIRST turn of a thread
    exclude = frozenset(t["text"].strip() for t in history if t.get("text"))
    context = await _grounded_context(message, exclude=exclude)
    async with keep_typing(message):  # 'typing…' covers the whole multi-second LLM call
        reply = await generate_reply(text, context=context, history=history)
    history = [
        *history,
        {"role": "user", "text": text},
        {"role": "assistant", "text": reply.text},
    ]
    await state.update_data(
        chat_transcript=history[-2 * _CHAT_KEEP_TURNS :], chat_ts=_now().isoformat()
    )
    tg = _telegram_id(message)
    substantive = _worth_remembering(text)
    if tg is not None and reply.source == "llm" and substantive:
        await _remember_general(tg, text, reply.text)
    # A substantive turn carries the proactive affordances (🔔 set a reminder / 🏥 where to do an
    # exam); a bare greeting/ack does not (#6). Tapping one enters a grounded general consult.
    keyboard = (
        consult_flow.chat_affordance_keyboard() if substantive and reply.source == "llm" else None
    )
    await answer_chunked(
        message,
        render_companion_html(reply.text, full_disclaimer=not continuation),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def _engage_with_text(
    message: Message, state: FSMContext, text: str, reminder_scheduler: ReminderScheduler
) -> None:
    """Route one free-text utterance into the right conversational engine. Shared by the catch-all
    free-text handler AND the check-in answer — so answering a check-in CONTINUES into a real
    conversation (clarifying questions, triage, next steps), never dead-ends at "saved"."""
    decision = screen(text)
    # The wellness guardrail (disordered eating / unsafe goals) owns its own response.
    if decision.source is GateSource.GUARDRAIL:
        await message.answer(decision.message)
        return
    # A red-flag symptom OR a broader physical complaint starts the guided intake; the
    # deterministic triage stays the escalation backstop inside the intake.
    if decision.source is GateSource.TRIAGE or intake.looks_like_complaint(text):
        context = await _grounded_context(message)
        await _run_intake_turn(message, state, [{"role": "user", "text": text}], context=context)
        return
    # Smart routing (#3): a QUESTION that names one of the user's own indicators ("чому залізо
    # низьке?") opens a focused, indicator-grounded consult about THAT analyte — the deep expert
    # answer + reminder/clinic affordances — instead of the general companion. Before the prime so a
    # named OTHER analyte overrides a stale chart prime; a generic "що скажеш?" has no match and
    # falls through to the prime.
    if await consult_flow.start_data_question_consult(message, state, scheduler=reminder_scheduler):
        return
    # If the user just opened a chart/indicator and is now writing about it (no «Запитати Дбайло»
    # tap), answer IN that grounded context instead of the contextless companion.
    if await consult_flow.start_primed_consult(message, state, scheduler=reminder_scheduler):
        return
    # A TYPED "нагадай мені…" / "запиши мене…" / "де зробити…" opens the reminder/clinic mini-flow
    # (entering a grounded general consult) so Дбайло ACTS on it — never just claims it will (#6).
    if await consult_flow.start_typed_affordance(message, state, scheduler=reminder_scheduler):
        return
    # A FREE-FORM price request ("знайди Но-шпа у Львові, ціни") — or a follow-up to a fresh price
    # thread ("а дешевше?") — is ACTED on via the price agent, which remembers the drug + city
    # across turns (a real conversation). The gate already cleared the text; the named-drug boundary
    # still refuses a symptom-based pick inside the pipeline.
    tg = message.from_user.id if message.from_user else None
    if await navigator_flow.maybe_handle_price(message, state, text, telegram_id=tg):
        return
    # Otherwise — ordinary companion chat: a continuous, grounded, memory-backed thread.
    await _run_companion_turn(message, state, text)


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_free_text(
    message: Message, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
    await _engage_with_text(message, state, message.text or "", reminder_scheduler)
