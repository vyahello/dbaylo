"""Bot skeleton tests: handlers reply, and the dispatcher wires up cleanly."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.bot.app import build_dispatcher, make_dialog_reset, make_sender
from dbaylo.bot.handlers import cmd_help, cmd_start
from dbaylo.bot.keyboards import main_menu_keyboard
from dbaylo.db.models import User
from dbaylo.locale import HELP_TEXT, START_TEXT
from dbaylo.triage.safety import contains_dose_directive, contains_forbidden_reassurance


async def test_cmd_help_replies() -> None:
    message = AsyncMock()
    await cmd_help(message)
    message.answer.assert_awaited_once_with(HELP_TEXT, reply_markup=main_menu_keyboard())


async def test_cmd_start_replies_and_captures_the_user(
    monkeypatch, async_session: AsyncSession
) -> None:
    @asynccontextmanager
    async def _fake_session():
        yield async_session

    monkeypatch.setattr("dbaylo.bot.handlers.get_session", _fake_session)
    message = AsyncMock()
    message.from_user = SimpleNamespace(id=4242, full_name="Owner")
    await cmd_start(message)
    # /start shows the persistent menu keyboard so nothing must be typed blindly.
    message.answer.assert_awaited_once_with(START_TEXT, reply_markup=main_menu_keyboard())
    user = await async_session.scalar(select(User).where(User.telegram_id == 4242))
    assert user is not None  # chat_id captured on /start


def test_build_dispatcher_registers_routers_and_owner_lock() -> None:
    from dbaylo.bot.access import OwnerOnlyMiddleware
    from dbaylo.bot.state_reset import CommandStateResetMiddleware

    dispatcher = build_dispatcher()
    # commands + menu + prescription + lab_flow + navigator + proactive + history + consult +
    # companion. (prescription is registered before lab_flow so a prescription upload is handled
    # there while every other photo still reaches the lab pipeline.)
    assert len(dispatcher.sub_routers) == 9
    # The owner lock is an outer update middleware (runs before any router).
    assert any(isinstance(m, OwnerOnlyMiddleware) for m in dispatcher.update.outer_middleware)
    # A /command cancels any FSM dialog before handlers resolve (message-level outer mw).
    assert any(
        isinstance(m, CommandStateResetMiddleware) for m in dispatcher.message.outer_middleware
    )


async def test_make_sender_forwards_to_the_bot() -> None:
    # The reminder scheduler delivers via this adapter -> bot.send_message.
    bot = AsyncMock()
    sender = make_sender(bot)
    await sender(123456, "🔔 нагадування")
    bot.send_message.assert_awaited_once_with(123456, "🔔 нагадування", reply_markup=None)


async def test_make_dialog_reset_clears_state_and_data_for_the_user() -> None:
    # The check-in safety belt: clears the user's FSM state+data keyed by their telegram_id, so a
    # check-in reply is never eaten by a stale dialog (private chat: chat_id == user_id == tg).
    bot = SimpleNamespace(id=777)
    storage = AsyncMock()
    reset = make_dialog_reset(bot, storage)
    await reset(123456)
    key = storage.set_state.await_args.kwargs.get("key") or storage.set_state.await_args.args[0]
    assert key.bot_id == 777 and key.chat_id == 123456 and key.user_id == 123456
    storage.set_state.assert_awaited_once()
    storage.set_data.assert_awaited_once()


@pytest.mark.parametrize("text", [START_TEXT, HELP_TEXT])
def test_bot_copy_is_safe(text: str) -> None:
    # Even the friendly skeleton copy must obey the safety rails.
    assert contains_forbidden_reassurance(text) is None
    assert contains_dose_directive(text) is None


@pytest.mark.parametrize("text", [START_TEXT, HELP_TEXT])
def test_bot_copy_carries_disclaimer(text: str) -> None:
    assert "не лікар" in text.lower()
