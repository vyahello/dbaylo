"""The global command-cancels-state middleware.

A /command must abort an in-progress FSM dialog before any handler resolves, and must
never be consumed as the dialog's text answer. The middleware clears the FSM context
and syncs ``raw_state`` (which StateFilter reads from the data for *this* update).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from dbaylo.bot.state_reset import CommandStateResetMiddleware, is_command


def _msg(text: str | None) -> SimpleNamespace:
    return SimpleNamespace(text=text)


def test_is_command() -> None:
    assert is_command(_msg("/goals"))
    assert is_command(_msg("/goal більше спати"))
    assert not is_command(_msg("більше води"))
    assert not is_command(_msg(None))  # e.g. a photo / document has no text


async def test_command_clears_state_and_raw_state() -> None:
    mw = CommandStateResetMiddleware()
    handler = AsyncMock(return_value="ok")
    state = AsyncMock()
    data = {"state": state, "raw_state": "GoalStates:waiting_for_goal"}

    result = await mw(handler, _msg("/goals"), data)

    state.clear.assert_awaited_once()
    assert data["raw_state"] is None  # so the dialog's text-state handler no longer matches
    handler.assert_awaited_once()
    assert result == "ok"


async def test_non_command_passes_through_untouched() -> None:
    mw = CommandStateResetMiddleware()
    handler = AsyncMock()
    state = AsyncMock()
    data = {"state": state, "raw_state": "GoalStates:waiting_for_goal"}

    await mw(handler, _msg("більше рухатися"), data)

    state.clear.assert_not_awaited()
    assert data["raw_state"] == "GoalStates:waiting_for_goal"
    handler.assert_awaited_once()


async def test_command_without_active_state_is_a_noop() -> None:
    mw = CommandStateResetMiddleware()
    handler = AsyncMock()
    state = AsyncMock()
    data = {"state": state, "raw_state": None}

    await mw(handler, _msg("/goal"), data)

    state.clear.assert_not_awaited()
    handler.assert_awaited_once()
