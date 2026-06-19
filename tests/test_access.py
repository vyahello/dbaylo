"""Owner lock: only the configured owner reaches a handler; everyone else is refused
before any handler / gate / ensure_user runs (so no stranger row is ever created)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Update

from dbaylo import locale
from dbaylo.bot.access import OwnerOnlyMiddleware, from_user_id

OWNER = 42


def _message_update(user_id: int) -> tuple[Update, AsyncMock]:
    msg = AsyncMock()
    msg.from_user = SimpleNamespace(id=user_id)
    return Update.model_construct(update_id=1, message=msg), msg


def _callback_update(user_id: int) -> tuple[Update, AsyncMock]:
    cb = AsyncMock()
    cb.from_user = SimpleNamespace(id=user_id)
    return Update.model_construct(update_id=1, callback_query=cb), cb


# --- from_user_id across entry points ------------------------------------------


def test_from_user_id_extracts_from_message() -> None:
    update, _ = _message_update(7)
    assert from_user_id(update) == 7


def test_from_user_id_extracts_from_callback() -> None:
    update, _ = _callback_update(9)
    assert from_user_id(update) == 9


def test_from_user_id_none_without_sender() -> None:
    assert from_user_id(Update.model_construct(update_id=1)) is None


# --- The middleware -------------------------------------------------------------


async def test_owner_passes_through_to_the_handler() -> None:
    mw = OwnerOnlyMiddleware(OWNER)
    handler = AsyncMock(return_value="handled")
    update, msg = _message_update(OWNER)
    result = await mw(handler, update, {})
    handler.assert_awaited_once_with(update, {})
    assert result == "handled"
    msg.answer.assert_not_awaited()


async def test_non_owner_message_is_refused_and_handler_never_runs() -> None:
    # Handler never runs => ensure_user (which only runs inside handlers) never runs
    # => no User/data row is created for a stranger.
    mw = OwnerOnlyMiddleware(OWNER)
    handler = AsyncMock()
    update, msg = _message_update(999)
    result = await mw(handler, update, {})
    handler.assert_not_awaited()
    assert result is None
    msg.answer.assert_awaited_once_with(locale.PRIVATE_BOT)


async def test_non_owner_callback_is_refused() -> None:
    mw = OwnerOnlyMiddleware(OWNER)
    handler = AsyncMock()
    update, cb = _callback_update(999)
    await mw(handler, update, {})
    handler.assert_not_awaited()
    cb.answer.assert_awaited_once_with(locale.PRIVATE_BOT, show_alert=True)


async def test_unset_owner_zero_refuses_everyone() -> None:
    # Fail-closed: an unset OWNER_TELEGRAM_ID (0) must lock the bot, not open it.
    mw = OwnerOnlyMiddleware(0)
    handler = AsyncMock()
    update, msg = _message_update(12345)
    await mw(handler, update, {})
    handler.assert_not_awaited()
    msg.answer.assert_awaited_once_with(locale.PRIVATE_BOT)


async def test_update_without_sender_is_dropped_silently() -> None:
    mw = OwnerOnlyMiddleware(OWNER)
    handler = AsyncMock()
    result = await mw(handler, Update.model_construct(update_id=1), {})
    handler.assert_not_awaited()
    assert result is None  # no crash, nothing sent


# Registration of the middleware on the dispatcher is asserted in
# tests/test_bot_handlers.py::test_build_dispatcher_registers_routers_and_owner_lock
# (build_dispatcher attaches singleton routers, so the suite builds it exactly once).
