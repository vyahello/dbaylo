"""Bot skeleton tests: handlers reply, and the dispatcher wires up cleanly."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dbaylo.bot.app import build_dispatcher
from dbaylo.bot.handlers import (
    CHECKIN_STUB_TEXT,
    HELP_TEXT,
    START_TEXT,
    cmd_checkin,
    cmd_help,
    cmd_start,
)
from dbaylo.triage.safety import contains_dose_directive, contains_forbidden_reassurance


@pytest.mark.parametrize(
    ("handler", "expected"),
    [(cmd_start, START_TEXT), (cmd_help, HELP_TEXT), (cmd_checkin, CHECKIN_STUB_TEXT)],
)
async def test_handler_replies_expected_text(handler, expected: str) -> None:
    message = AsyncMock()
    await handler(message)
    message.answer.assert_awaited_once_with(expected)


def test_build_dispatcher_registers_router() -> None:
    dispatcher = build_dispatcher()
    assert dispatcher.sub_routers, "expected the commands router to be registered"


@pytest.mark.parametrize("text", [START_TEXT, HELP_TEXT, CHECKIN_STUB_TEXT])
def test_bot_copy_is_safe(text: str) -> None:
    # Even the friendly skeleton copy must obey the safety rails.
    assert contains_forbidden_reassurance(text) is None
    assert contains_dose_directive(text) is None


@pytest.mark.parametrize("text", [START_TEXT, HELP_TEXT, CHECKIN_STUB_TEXT])
def test_bot_copy_carries_disclaimer(text: str) -> None:
    assert "не лікар" in text.lower()
