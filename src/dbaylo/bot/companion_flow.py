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

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from dbaylo import locale
from dbaylo.bot.keyboards import cancel_keyboard
from dbaylo.bot.typing import keep_typing
from dbaylo.companion import checkin, goals, intake
from dbaylo.companion.conversation import generate_reply
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
    """Render the user's goals (from /goals or the menu)."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        text = await goals.list_goals(session, user=user)
    await message.answer(text)


@router.message(Command("goals"))
async def cmd_goals(message: Message) -> None:
    tg_id = _telegram_id(message)
    if tg_id is None:
        return
    await open_goals(message, tg_id)


# --- Daily check-in -------------------------------------------------------------


async def start_checkin_dialog(message: Message, state: FSMContext) -> None:
    """Begin a check-in: ask the prompt and wait for the answer. Reused by /checkin and
    the menu's 📝 Чек-ін button so both share one entry point."""
    await state.set_state(CheckinStates.waiting_for_answer)
    await message.answer(checkin.build_prompt(), reply_markup=cancel_keyboard())


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


async def _run_intake_turn(
    message: Message, state: FSMContext, transcript: list[dict[str, str]]
) -> None:
    async with keep_typing(message):  # 'typing…' stays up for the whole LLM call, not ~5 s
        reply = await intake.advance(transcript)
    transcript.append({"role": "assistant", "text": reply.text})
    if reply.done:
        await state.clear()
    else:
        await state.set_state(IntakeStates.in_progress)
        await state.update_data(intake=transcript)
    await message.answer(reply.text)


@router.message(IntakeStates.in_progress, F.text & ~F.text.startswith("/"))
async def on_intake_turn(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    transcript = list(data.get("intake") or [])
    transcript.append({"role": "user", "text": message.text or ""})
    await _run_intake_turn(message, state, transcript)


# --- Free-text companion chat (only when no FSM flow is active) ------------------


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_free_text(message: Message, state: FSMContext) -> None:
    text = message.text or ""
    decision = screen(text)
    # The wellness guardrail (disordered eating / unsafe goals) owns its own response.
    if decision.source is GateSource.GUARDRAIL:
        await message.answer(decision.message)
        return
    # A red-flag symptom OR a broader physical complaint starts the guided intake; the
    # deterministic triage stays the escalation backstop inside the intake.
    if decision.source is GateSource.TRIAGE or intake.looks_like_complaint(text):
        await _run_intake_turn(message, state, [{"role": "user", "text": text}])
        return
    # Otherwise — ordinary companion chat.
    async with keep_typing(message):
        reply = await generate_reply(text)
    await message.answer(reply.text)
