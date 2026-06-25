"""Blank input never creates a record (the dialog says so instead).

Defence-in-depth alongside the command-cancel middleware: even a genuinely blank /
whitespace answer must not write a phantom goal, concern, medication, or check-in.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.bot import companion_flow, proactive_flow
from dbaylo.db.models import CheckIn, Condition, Goal


def _message(text: str) -> AsyncMock:
    message = AsyncMock()
    message.text = text
    message.from_user = SimpleNamespace(id=4242, full_name="Owner")
    message.chat = SimpleNamespace(id=4242)
    return message


def _guard_session(monkeypatch, target: str, async_session: AsyncSession) -> dict[str, bool]:
    """Patch get_session in ``target`` module and record whether it was entered."""
    flag = {"opened": False}

    @asynccontextmanager
    async def _fake():
        flag["opened"] = True
        yield async_session

    monkeypatch.setattr(f"dbaylo.bot.{target}.get_session", _fake)
    return flag


async def test_blank_goal_saves_nothing(monkeypatch, async_session: AsyncSession) -> None:
    flag = _guard_session(monkeypatch, "companion_flow", async_session)
    message = _message("   ")
    await companion_flow._save_goal(message, "   ")
    message.answer.assert_awaited_once_with(locale.NOTHING_SAVED)
    assert not flag["opened"]  # never reached the DB
    assert (await async_session.scalars(select(Goal))).first() is None


async def test_blank_checkin_writes_no_row(monkeypatch, async_session: AsyncSession) -> None:
    flag = _guard_session(monkeypatch, "companion_flow", async_session)
    message = _message("  ")
    state = AsyncMock()
    await companion_flow.on_checkin_answer(message, state)
    state.clear.assert_awaited_once()
    message.answer.assert_awaited_once_with(locale.NOTHING_SAVED)
    assert not flag["opened"]
    assert (await async_session.scalars(select(CheckIn))).first() is None


async def test_blank_problem_creates_no_concern(monkeypatch, async_session: AsyncSession) -> None:
    flag = _guard_session(monkeypatch, "proactive_flow", async_session)
    message = _message("")
    scheduler = AsyncMock()
    await proactive_flow._add_problem(message, "   ", scheduler)
    message.answer.assert_awaited_once_with(locale.NOTHING_SAVED)
    assert not flag["opened"]
    assert (await async_session.scalars(select(Condition))).first() is None
    scheduler.schedule.assert_not_called()  # no check-in scheduled either


async def test_blank_medication_name_aborts() -> None:
    message = _message("   ")
    state = AsyncMock()
    await proactive_flow.on_medication_name(message, state)
    state.clear.assert_awaited_once()
    state.set_state.assert_not_awaited()  # never advances to "ask times"
    message.answer.assert_awaited_once_with(locale.NOTHING_SAVED)


# --- "Цілі = the agent suggests" (the AI-driven goals screen) --------------------


@asynccontextmanager
async def _fake_session():
    yield AsyncMock()  # has an awaitable .commit()


async def test_open_goals_screen_proposes_goals_with_adopt_buttons(monkeypatch) -> None:
    from dbaylo.companion import callbacks as cb

    monkeypatch.setattr(companion_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        companion_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(
        companion_flow.goals,
        "propose_goals",
        AsyncMock(return_value=["Привести Глюкоза до норми", "Налагодити режим сну"]),
    )
    monkeypatch.setattr(companion_flow.goals, "active_goal_texts", AsyncMock(return_value=[]))
    message = AsyncMock()
    await companion_flow.open_goals_screen(message, telegram_id=4242)
    message.answer.assert_awaited_once()
    rows = message.answer.call_args.kwargs["reply_markup"].inline_keyboard
    datas = [b.callback_data for row in rows for b in row]
    assert (
        cb.goal_adopt(0) in datas and cb.goal_adopt(1) in datas
    )  # one adopt button per suggestion
    assert cb.MENU_GOAL_NEW in datas  # manual "➕ Своя ціль" fallback


async def test_on_goal_adopt_sets_the_goal_by_index(monkeypatch) -> None:
    from dbaylo.companion import callbacks as cb

    monkeypatch.setattr(companion_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        companion_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(
        companion_flow.goals,
        "propose_goals",
        AsyncMock(return_value=["Привести Глюкоза до норми"]),
    )
    monkeypatch.setattr(companion_flow.goals, "active_goal_texts", AsyncMock(return_value=[]))
    set_goal = AsyncMock(return_value=SimpleNamespace(saved=True))
    monkeypatch.setattr(companion_flow.goals, "set_goal", set_goal)

    callback = AsyncMock()
    callback.data = cb.goal_adopt(0)
    callback.from_user = SimpleNamespace(id=4242)
    callback.message = AsyncMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    await companion_flow.on_goal_adopt(callback)
    set_goal.assert_awaited_once()
    assert set_goal.await_args.kwargs["text"] == "Привести Глюкоза до норми"  # adopted by index 0
    callback.message.edit_text.assert_awaited()  # refreshed in place, no new message
