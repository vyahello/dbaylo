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
from dbaylo.companion import callbacks, checkin, goals, intake
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


async def open_goals(message: Message, telegram_id: int) -> None:
    """Render the user's goals as plain text (from /goals — a read-only list)."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        text = await goals.list_goals(session, user=user)
    await message.answer(text)


def _short_goal(text: str, limit: int = 40) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def _goals_screen(session: AsyncSession, *, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """The agent's goals screen: suggested goals (one-tap adopt) + the user's current goals + a
    manual-add fallback. The suggester is deterministic; each adopt re-runs the guardrail."""
    suggestions = await goals.propose_goals(session, user_id, today=date.today())
    current = await goals.active_goal_texts(session, user_id=user_id)
    lines: list[str] = []
    kb: list[list[InlineKeyboardButton]] = []
    if suggestions:
        lines.append(locale.GOAL_PROPOSE_HEADER)
        for index, text in enumerate(suggestions):
            kb.append(
                [
                    InlineKeyboardButton(
                        text=locale.BTN_GOAL_ADOPT.format(goal=_short_goal(text)),
                        callback_data=callbacks.goal_adopt(index),
                    )
                ]
            )
    if current:
        if lines:
            lines.append("")
        lines.append(locale.GOAL_LIST_HEADER)
        lines.extend(f"• {text}" for text in current)
    if not suggestions and not current:
        lines.append(locale.GOAL_ALL_SET)
    kb.append(
        [InlineKeyboardButton(text=locale.BTN_GOAL_OWN, callback_data=callbacks.MENU_GOAL_NEW)]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


async def open_goals_screen(message: Message, telegram_id: int) -> None:
    """The agent-driven goals screen (the 🎯 Цілі entry): proposes goals from the data, one-tap
    adopt, plus the current goals and a manual fallback."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        text, keyboard = await _goals_screen(session, user_id=user.id)
    await message.answer(text, reply_markup=keyboard)


async def _edit_goals(callback: CallbackQuery) -> None:
    """Re-render the goals screen in place (after an adopt) — no message spam."""
    tg = callback.from_user.id if callback.from_user else None
    if tg is None or not isinstance(callback.message, Message):
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        text, keyboard = await _goals_screen(session, user_id=user.id)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith(callbacks.GOAL_ADOPT + ":"))
async def on_goal_adopt(callback: CallbackQuery) -> None:
    """Adopt an AI-suggested goal by its index (re-derived on tap); the guardrail still vets it."""
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
            result = await goals.set_goal(session, user=user, text=suggestions[index])
            await session.commit()
            toast = locale.GOAL_ADOPTED_TOAST if result.saved else locale.GOAL_NOT_ADOPTED
    await callback.answer(toast)
    await _edit_goals(callback)


@router.message(Command("goals"))
async def cmd_goals(message: Message) -> None:
    tg_id = _telegram_id(message)
    if tg_id is None:
        return
    await open_goals(message, tg_id)


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
