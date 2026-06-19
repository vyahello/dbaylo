"""Global rule: a /command always aborts an in-progress FSM dialog.

Registered as an **outer** middleware on the dispatcher's *message* observer, so it
runs before any router resolves a handler. Without it, sending a command while a
dialog is waiting for text (e.g. ``/goals`` while ``GoalStates.waiting_for_goal`` is
active) is consumed by the state's ``F.text`` handler — the command text is "saved"
as the answer, creating phantom records. Here we detect a command and clear the FSM
state first, so the dialog's text-state handler no longer matches and the command
runs fresh.

We also reset ``raw_state`` in the middleware data: :class:`aiogram.filters.StateFilter`
resolves against ``raw_state`` (captured once by the FSM middleware), not a live read,
so clearing the context alone would not change which handler matches this same update.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


def is_command(event: TelegramObject) -> bool:
    """True if the message is a bot command (its text starts with ``/``)."""
    text = getattr(event, "text", None)
    return isinstance(text, str) and text.startswith("/")


class CommandStateResetMiddleware(BaseMiddleware):
    """Clear any active FSM dialog when a command arrives (commands never become input)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if is_command(event) and data.get("raw_state") is not None:
            state = data.get("state")
            if state is not None:
                await state.clear()
                data["raw_state"] = None  # keep StateFilter resolution consistent this update
        return await handler(event, data)
