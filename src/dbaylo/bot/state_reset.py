"""Global rule: a /command (or a menu-button tap) always aborts an in-progress dialog.

Registered as an **outer** middleware on the dispatcher's *message* observer, so it
runs before any router resolves a handler — and only for messages, never for callback
queries (inline buttons carry their own CANCEL_DIALOG; a callback must not wipe the
state its handler needs).

Without it, sending a command while a dialog waits for text (e.g. ``/goals`` while
``GoalStates.waiting_for_goal`` is active) is consumed by the state's ``F.text``
handler — the command text is "saved" as the answer, creating phantom records. The
same trap applies to the Tier 1.3 reply keyboard: tapping "🎯 Цілі" mid-dialog would
otherwise be saved as the answer. So a *reset trigger* is a command **or** an exact
menu label; on either we clear the FSM state first and the dialog's text handler no
longer matches.

We also reset ``raw_state`` in the middleware data: :class:`aiogram.filters.StateFilter`
resolves against ``raw_state`` (captured once by the FSM middleware), not a live read,
so clearing the context alone would not change which handler matches this same update.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from dbaylo.locale import MENU_LABELS


def is_command(event: TelegramObject) -> bool:
    """True if the message is a bot command (its text starts with ``/``)."""
    text = getattr(event, "text", None)
    return isinstance(text, str) and text.startswith("/")


def is_menu_label(event: TelegramObject) -> bool:
    """True if the message text is exactly one of the persistent menu labels."""
    text = getattr(event, "text", None)
    return isinstance(text, str) and text in MENU_LABELS


class CommandStateResetMiddleware(BaseMiddleware):
    """Clear any active FSM dialog when a command or menu-label message arrives."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (is_command(event) or is_menu_label(event)) and data.get("raw_state") is not None:
            state = data.get("state")
            if state is not None:
                await state.clear()
                data["raw_state"] = None  # keep StateFilter resolution consistent this update
        return await handler(event, data)
