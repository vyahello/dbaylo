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
from datetime import date

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.bot import consult_flow
from dbaylo.bot.formatting import answer_chunked, render_companion_html
from dbaylo.bot.keyboards import cancel_keyboard
from dbaylo.bot.typing import keep_typing
from dbaylo.companion import callbacks, checkin, goals, health, intake
from dbaylo.companion.conversation import generate_reply
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.db import get_session
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
    """The goals MASTER: short subject buttons — suggestions (🎯) then adopted goals (📌). A tap
    opens that goal's detail (full title + the indicator's history) where the action lives. Short
    labels here so a long 'Привести … до норми' is never cut off on mobile — detail has it all."""
    suggestions = await goals.propose_goals(session, user_id, today=date.today())
    current = await goals.list_active_goals(session, user_id=user_id)
    lines: list[str] = [locale.GOAL_MASTER_HEADER]
    kb: list[list[InlineKeyboardButton]] = []
    if suggestions:
        lines.append(locale.GOAL_MASTER_SUGGEST_LABEL)
        for index, sug in enumerate(suggestions):
            kb.append(
                [
                    InlineKeyboardButton(
                        text=locale.BTN_GOAL_VIEW_SUG.format(subject=_short_goal(sug.subject)),
                        callback_data=callbacks.goal_view_sug(index),
                    )
                ]
            )
    if current:
        lines.append(locale.GOAL_MASTER_MINE_LABEL)
        for goal in current:
            subject = goals.target_subject(goal.target or "") or (goal.target or "")
            kb.append(
                [
                    InlineKeyboardButton(
                        text=locale.BTN_GOAL_VIEW.format(subject=_short_goal(subject)),
                        callback_data=callbacks.goal_view(goal.id),
                    )
                ]
            )
    if not suggestions and not current:
        lines = [locale.GOAL_ALL_SET]
    kb.append(
        [InlineKeyboardButton(text=locale.BTN_GOAL_OWN, callback_data=callbacks.MENU_GOAL_NEW)]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


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
    """A suggestion's detail: full title + (for a data goal) why now + the indicator history, with a
    🎯 Взяти ціль action. ``None`` when the index no longer resolves (caller falls back)."""
    suggestions = await goals.propose_goals(session, user_id, today=date.today())
    if not 0 <= index < len(suggestions):
        return None
    sug = suggestions[index]
    lines = [locale.GOAL_DETAIL_SUG_TITLE.format(goal=html.escape(sug.text)), ""]
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
    lines = [locale.GOAL_DETAIL_MINE_TITLE.format(goal=html.escape(target)), ""]
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
async def on_goal_adopt(callback: CallbackQuery) -> None:
    """🎯 Взяти ціль (from a suggestion detail): adopt by index (re-derived), guardrail vets it,
    then back to the master (the suggestion is now an adopted goal)."""
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
            await session.commit()
            toast = locale.GOAL_ADOPTED_TOAST if result.saved else locale.GOAL_NOT_ADOPTED
    await callback.answer(toast)
    await _edit_to_master(callback)


@router.callback_query(F.data.startswith(callbacks.GOAL_ACHIEVE + ":"))
async def on_goal_achieve(callback: CallbackQuery) -> None:
    """✅ Mark a goal achieved (from its detail), then back to the master."""
    goal_id = callbacks.parse_goal_achieve(callback.data or "")
    tg = callback.from_user.id if callback.from_user else None
    if goal_id is None or tg is None:
        await callback.answer()
        return
    toast = ""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        if await goals.achieve_goal(session, goal_id=goal_id, user_id=user.id) is not None:
            await session.commit()
            toast = locale.GOAL_ACHIEVED_TOAST
    await callback.answer(toast)
    await _edit_to_master(callback)


@router.callback_query(F.data.startswith(callbacks.GOAL_REMOVE + ":"))
async def on_goal_remove(callback: CallbackQuery) -> None:
    """🗑 Drop a goal (undo an accidental adopt, from its detail), then back to the master."""
    goal_id = callbacks.parse_goal_remove(callback.data or "")
    tg = callback.from_user.id if callback.from_user else None
    if goal_id is None or tg is None:
        await callback.answer()
        return
    toast = ""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        if await goals.remove_goal(session, goal_id=goal_id, user_id=user.id) is not None:
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
    prompt = checkin.build_prompt()
    if tg is not None:
        async with get_session() as session:
            user = await ensure_user(session, telegram_id=tg)
            context = await checkin.grounded_context(session, user_id=user.id, today=date.today())
        if context:
            async with keep_typing(message):
                prompt = await checkin.build_grounded_prompt(context)
    await message.answer(prompt, reply_markup=cancel_keyboard())


@router.message(Command("checkin"))
async def cmd_checkin(message: Message, state: FSMContext) -> None:
    await start_checkin_dialog(message, state)


@router.message(CheckinStates.waiting_for_answer, F.text)
async def on_checkin_answer(message: Message, state: FSMContext) -> None:
    await state.clear()
    tg_id = _telegram_id(message)
    if tg_id is None:
        return
    if not (message.text or "").strip():
        await message.answer(locale.NOTHING_SAVED)  # blank answer -> no empty check-in row
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg_id)
        result = await checkin.process_checkin(session, user=user, text=message.text or "")
        await session.commit()
    await message.answer(result.message)


# --- Symptom intake (history-taking) --------------------------------------------


async def _health_context(message: Message) -> str:
    """The user's grounded picture — labs (profile + current/resolved out-of-range indicators) AND
    recent check-in STATE memory (how they've been) — so general chat / the symptom interview answer
    based on THEIR real data and remember their state. ``""`` when there's nothing to ground in."""
    tg = _telegram_id(message)
    if tg is None:
        return ""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        return await checkin.grounded_context(session, user_id=user.id, today=date.today())


async def _run_intake_turn(
    message: Message, state: FSMContext, transcript: list[dict[str, str]], *, context: str = ""
) -> None:
    async with keep_typing(message):  # 'typing…' stays up for the whole LLM call, not ~5 s
        reply = await intake.advance(transcript, context=context)
    transcript.append({"role": "assistant", "text": reply.text})
    if reply.done:
        await state.clear()
    else:
        await state.set_state(IntakeStates.in_progress)
        await state.update_data(intake=transcript)
    await answer_chunked(message, render_companion_html(reply.text), parse_mode=ParseMode.HTML)


@router.message(IntakeStates.in_progress, F.text & ~F.text.startswith("/"))
async def on_intake_turn(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    transcript = list(data.get("intake") or [])
    transcript.append({"role": "user", "text": message.text or ""})
    await _run_intake_turn(message, state, transcript, context=await _health_context(message))


# --- Free-text companion chat (only when no FSM flow is active) ------------------


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_free_text(
    message: Message, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
    text = message.text or ""
    decision = screen(text)
    # The wellness guardrail (disordered eating / unsafe goals) owns its own response.
    if decision.source is GateSource.GUARDRAIL:
        await message.answer(decision.message)
        return
    # Ground the reply in the user's real health picture (tracked problems + recent analyses) so
    # Дбайло answers like an assistant who knows them — '' when there's nothing -> stays general.
    context = await _health_context(message)
    # A red-flag symptom OR a broader physical complaint starts the guided intake; the
    # deterministic triage stays the escalation backstop inside the intake.
    if decision.source is GateSource.TRIAGE or intake.looks_like_complaint(text):
        await _run_intake_turn(message, state, [{"role": "user", "text": text}], context=context)
        return
    # If the user just opened a chart/indicator and is now writing about it (no «Запитати Дбайло»
    # tap), answer IN that grounded context instead of the contextless companion.
    if await consult_flow.start_primed_consult(message, state, scheduler=reminder_scheduler):
        return
    # Otherwise — ordinary companion chat, grounded in the profile when relevant.
    async with keep_typing(message):
        reply = await generate_reply(text, context=context)
    await answer_chunked(message, render_companion_html(reply.text), parse_mode=ParseMode.HTML)
