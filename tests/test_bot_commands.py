"""The native "/" command menu (set_my_commands) must stay in sync with the handlers.

These tests are the guard that answers "is every command discoverable without typing it?":
every registered ``Command(...)`` handler must have an entry in ``locale.BOT_COMMANDS`` (so it
shows in Telegram's "/" menu), and the menu must not list a command that has no handler.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BotCommand

from dbaylo import locale
from dbaylo.bot import (
    companion_flow,
    consult_flow,
    handlers,
    history_flow,
    lab_flow,
    menu_flow,
    navigator_flow,
    proactive_flow,
)
from dbaylo.bot.app import apply_bot_commands

# The same routers build_dispatcher registers. We walk them directly (not a Dispatcher) because
# they are module singletons: build_dispatcher can only attach them once per process, and the
# suite already does that in test_bot_handlers.
_ROUTERS: tuple[Router, ...] = (
    handlers.router,
    menu_flow.router,
    lab_flow.router,
    navigator_flow.router,
    proactive_flow.router,
    history_flow.router,
    companion_flow.router,
    consult_flow.router,
)

# /reports is a deliberate alias of /history; one "/" menu entry is enough for the pair.
_ALIASES = {"reports"}


def _iter_routers(router: Router) -> Iterator[Router]:
    yield router
    for sub in router.sub_routers:
        yield from _iter_routers(sub)


def _registered_commands() -> set[str]:
    """Every string command name across all message-handler ``Command`` filters."""
    found: set[str] = set()
    for root in _ROUTERS:
        for router in _iter_routers(root):
            for handler in router.message.handlers:
                for flt in handler.filters:
                    callback = getattr(flt, "callback", flt)
                    if isinstance(callback, Command):
                        found.update(c for c in callback.commands if isinstance(c, str))
    return found


def _menu_commands() -> set[str]:
    return {name for name, _ in locale.BOT_COMMANDS}


def test_every_command_handler_appears_in_the_native_menu() -> None:
    missing = _registered_commands() - _menu_commands() - _ALIASES
    assert not missing, f"commands with a handler but no /menu entry: {missing}"


def test_native_menu_lists_no_phantom_command() -> None:
    extra = _menu_commands() - _registered_commands()
    assert not extra, f"/menu lists commands that have no handler: {extra}"


def test_bot_commands_are_well_formed_for_telegram() -> None:
    names = [name for name, _ in locale.BOT_COMMANDS]
    assert len(names) == len(set(names)), "duplicate command in BOT_COMMANDS"
    for name, desc in locale.BOT_COMMANDS:
        # Telegram: lowercase latin letters, digits and underscores, 1-32 chars.
        assert 1 <= len(name) <= 32
        assert name.isascii() and name.islower() and name.replace("_", "").isalnum()
        assert 1 <= len(desc) <= 256


@pytest.mark.asyncio
async def test_apply_bot_commands_registers_the_full_palette() -> None:
    bot = AsyncMock()
    await apply_bot_commands(bot)
    bot.set_my_commands.assert_awaited_once()
    (sent,), _ = bot.set_my_commands.call_args
    assert all(isinstance(c, BotCommand) for c in sent)
    assert [c.command for c in sent] == [name for name, _ in locale.BOT_COMMANDS]
