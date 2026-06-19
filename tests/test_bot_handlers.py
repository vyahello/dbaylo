"""Bot skeleton tests: handlers reply, and the dispatcher wires up cleanly."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dbaylo.bot.app import build_dispatcher, make_sender
from dbaylo.bot.handlers import cmd_help, cmd_start
from dbaylo.locale import HELP_TEXT, START_TEXT
from dbaylo.triage.safety import contains_dose_directive, contains_forbidden_reassurance


@pytest.mark.parametrize(
    ("handler", "expected"),
    [(cmd_start, START_TEXT), (cmd_help, HELP_TEXT)],
)
async def test_handler_replies_expected_text(handler, expected: str) -> None:
    message = AsyncMock()
    await handler(message)
    message.answer.assert_awaited_once_with(expected)


def test_build_dispatcher_registers_routers() -> None:
    dispatcher = build_dispatcher()
    # commands + lab_flow + navigator + companion.
    assert len(dispatcher.sub_routers) == 4


async def test_make_sender_forwards_to_the_bot() -> None:
    # The reminder scheduler delivers via this adapter -> bot.send_message.
    bot = AsyncMock()
    sender = make_sender(bot)
    await sender(123456, "🔔 нагадування")
    bot.send_message.assert_awaited_once_with(123456, "🔔 нагадування")


@pytest.mark.parametrize("text", [START_TEXT, HELP_TEXT])
def test_bot_copy_is_safe(text: str) -> None:
    # Even the friendly skeleton copy must obey the safety rails.
    assert contains_forbidden_reassurance(text) is None
    assert contains_dose_directive(text) is None


@pytest.mark.parametrize("text", [START_TEXT, HELP_TEXT])
def test_bot_copy_carries_disclaimer(text: str) -> None:
    assert "не лікар" in text.lower()
