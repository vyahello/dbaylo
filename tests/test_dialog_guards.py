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


def _sug(text, subject, series_key=""):
    from dbaylo.companion.goals import GoalSuggestion

    return GoalSuggestion(text=text, subject=subject, series_key=series_key)


async def test_goals_master_lists_short_subjects_that_open_details(monkeypatch) -> None:
    # The master is short SUBJECT buttons (long "Привести … до норми" would be cut off on mobile);
    # a tap opens the detail. Suggestions -> goal_view_sug, adopted goals -> goal_view.
    from dbaylo.companion import callbacks as cb

    monkeypatch.setattr(companion_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        companion_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(
        companion_flow.goals,
        "propose_goals",
        AsyncMock(return_value=[_sug("Привести Глюкоза до норми", "Глюкоза", "blood\x1fглюкоза")]),
    )
    monkeypatch.setattr(
        companion_flow.goals,
        "list_active_goals",
        AsyncMock(return_value=[SimpleNamespace(id=3, target="Налагодити режим сну")]),
    )
    monkeypatch.setattr(companion_flow.goals, "target_subject", lambda t: "")
    message = AsyncMock()
    await companion_flow.open_goals_screen(message, telegram_id=4242)
    flat = [
        b for row in message.answer.call_args.kwargs["reply_markup"].inline_keyboard for b in row
    ]
    datas = [b.callback_data for b in flat]
    assert cb.goal_view_sug(0) in datas  # a suggestion opens its detail
    assert cb.goal_view(3) in datas  # an adopted goal opens its detail
    assert cb.MENU_GOAL_NEW in datas
    sug_btn = next(b for b in flat if b.callback_data == cb.goal_view_sug(0))
    assert "Глюкоза" in sug_btn.text  # short subject on the button, not the long full sentence


async def test_suggestion_detail_shows_history_and_adopt(monkeypatch) -> None:
    from dbaylo.companion import callbacks as cb
    from dbaylo.companion.health import HistoryPoint

    monkeypatch.setattr(companion_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        companion_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(
        companion_flow.goals,
        "propose_goals",
        AsyncMock(return_value=[_sug("Привести Глюкоза до норми", "Глюкоза", "blood\x1fглюкоза")]),
    )
    finding = SimpleNamespace(
        kind="high", value="7 ммоль/л", ref="3.9–6.1", series_key="blood\x1fг"
    )
    monkeypatch.setattr(companion_flow.goals, "goal_analyte", AsyncMock(return_value=finding))
    monkeypatch.setattr(
        companion_flow.health,
        "indicator_history",
        AsyncMock(
            return_value=[
                HistoryPoint(date=None, value="7 ммоль/л", ref="3.9–6.1", out_of_range=True)
            ]
        ),
    )
    callback = _goal_cb(cb.goal_view_sug(0))
    await companion_flow.on_goal_view_sug(callback)
    text = callback.message.edit_text.call_args.args[0]
    datas = [
        b.callback_data
        for row in callback.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert "Привести Глюкоза до норми" in text  # the FULL title (not cut off) is in the detail
    assert "7 ммоль/л" in text  # the indicator history is shown ("коли були проблеми")
    assert cb.goal_adopt(0) in datas and cb.GOAL_BACK in datas  # adopt + back live in the detail


async def test_goal_detail_shows_achieve_remove(monkeypatch) -> None:
    from dbaylo.companion import callbacks as cb

    monkeypatch.setattr(companion_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        companion_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(
        companion_flow.goals,
        "list_active_goals",
        AsyncMock(return_value=[SimpleNamespace(id=3, target="Налагодити режим сну")]),
    )
    monkeypatch.setattr(companion_flow.goals, "goal_analyte", AsyncMock(return_value=None))
    callback = _goal_cb(cb.goal_view(3))
    await companion_flow.on_goal_view(callback)
    datas = [
        b.callback_data
        for row in callback.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    # The adopted goal's detail is where it's resolved/removed — achieve + remove + back.
    assert cb.goal_achieve(3) in datas and cb.goal_remove(3) in datas and cb.GOAL_BACK in datas


def _goal_cb(data):
    callback = AsyncMock()
    callback.data = data
    callback.from_user = SimpleNamespace(id=4242)
    callback.message = AsyncMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    return callback


async def test_on_goal_adopt_sets_the_goal_by_index(monkeypatch) -> None:
    from dbaylo.companion import callbacks as cb

    monkeypatch.setattr(companion_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        companion_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(
        companion_flow.goals,
        "propose_goals",
        AsyncMock(return_value=[_sug("Привести Глюкоза до норми", "Глюкоза")]),
    )
    set_goal = AsyncMock(return_value=SimpleNamespace(saved=True))
    monkeypatch.setattr(companion_flow.goals, "set_goal", set_goal)
    monkeypatch.setattr(companion_flow.goals, "list_active_goals", AsyncMock(return_value=[]))

    callback = _goal_cb(cb.goal_adopt(0))
    await companion_flow.on_goal_adopt(callback)
    set_goal.assert_awaited_once()
    assert (
        set_goal.await_args.kwargs["text"] == "Привести Глюкоза до норми"
    )  # the .text, by index 0
    callback.message.edit_text.assert_awaited()  # back to the master in place


async def test_on_goal_achieve_and_remove(monkeypatch) -> None:
    from dbaylo.companion import callbacks as cb

    monkeypatch.setattr(companion_flow, "get_session", _fake_session)
    monkeypatch.setattr(
        companion_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=7))
    )
    monkeypatch.setattr(companion_flow.goals, "propose_goals", AsyncMock(return_value=[]))
    monkeypatch.setattr(companion_flow.goals, "list_active_goals", AsyncMock(return_value=[]))
    achieve = AsyncMock(return_value=SimpleNamespace(id=3))
    remove = AsyncMock(return_value=SimpleNamespace(id=3))
    monkeypatch.setattr(companion_flow.goals, "achieve_goal", achieve)
    monkeypatch.setattr(companion_flow.goals, "remove_goal", remove)

    def _cb(data):
        callback = AsyncMock()
        callback.data = data
        callback.from_user = SimpleNamespace(id=4242)
        callback.message = AsyncMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        return callback

    await companion_flow.on_goal_achieve(_cb(cb.goal_achieve(3)))
    assert achieve.await_args.kwargs["goal_id"] == 3
    await companion_flow.on_goal_remove(_cb(cb.goal_remove(3)))
    assert remove.await_args.kwargs["goal_id"] == 3  # 🗑 undoes an accidental adopt
