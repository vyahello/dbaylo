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
from dbaylo.companion import checkin, goals
from dbaylo.companion.conversation import generate_reply
from dbaylo.db import get_session
from dbaylo.labs.intake import ensure_user

router = Router(name="companion")


class GoalStates(StatesGroup):
    waiting_for_goal = State()


class CheckinStates(StatesGroup):
    waiting_for_answer = State()


def _telegram_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


# --- Goals ----------------------------------------------------------------------


@router.message(Command("goal"))
async def cmd_goal(message: Message, command: CommandObject, state: FSMContext) -> None:
    text = (command.args or "").strip()
    if not text:
        await state.set_state(GoalStates.waiting_for_goal)
        await message.answer(locale.GOAL_ASK_TEXT)
        return
    await _save_goal(message, text)


@router.message(GoalStates.waiting_for_goal, F.text)
async def on_goal_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _save_goal(message, (message.text or "").strip())


async def _save_goal(message: Message, text: str) -> None:
    tg_id = _telegram_id(message)
    if tg_id is None or not text:
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg_id)
        result = await goals.set_goal(session, user=user, text=text)
        await session.commit()
    await message.answer(result.message)


@router.message(Command("goals"))
async def cmd_goals(message: Message) -> None:
    tg_id = _telegram_id(message)
    if tg_id is None:
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg_id)
        text = await goals.list_goals(session, user=user)
    await message.answer(text)


# --- Daily check-in -------------------------------------------------------------


@router.message(Command("checkin"))
async def cmd_checkin(message: Message, state: FSMContext) -> None:
    await state.set_state(CheckinStates.waiting_for_answer)
    await message.answer(checkin.build_prompt())


@router.message(CheckinStates.waiting_for_answer, F.text)
async def on_checkin_answer(message: Message, state: FSMContext) -> None:
    await state.clear()
    tg_id = _telegram_id(message)
    if tg_id is None:
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg_id)
        result = await checkin.process_checkin(session, user=user, text=message.text or "")
        await session.commit()
    await message.answer(result.message)


# --- Free-text companion chat (only when no FSM flow is active) ------------------


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_free_text(message: Message) -> None:
    reply = await generate_reply(message.text or "")
    await message.answer(reply.text)
