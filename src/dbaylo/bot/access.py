"""Owner lock — the single access-control gate for the whole bot.

Registered as an **outer** middleware on the dispatcher's update observer, so it runs
before any router, handler, FSM step, the safety gate, or ``ensure_user`` — for
*every* update type (messages, commands, photo/PDF, callback queries, …). Any update
whose sender is not the configured owner is refused with one polite Ukrainian reply
and dropped; no handler runs, so **no stranger row is ever created**.

Fail-closed: ``owner_id == 0`` (unset) means nobody matches → the bot refuses
everyone. This is personal medical data; an unset owner must never mean "open".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from dbaylo import locale

# Update fields that carry a sender; checked in order to find ``from_user``.
_USER_EVENT_FIELDS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "callback_query",
    "inline_query",
    "chosen_inline_result",
    "shipping_query",
    "pre_checkout_query",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "message_reaction",
    "poll_answer",
)


def from_user_id(update: Update) -> int | None:
    """Return the Telegram user id behind an update, or ``None`` if it has no sender."""
    for field in _USER_EVENT_FIELDS:
        event = getattr(update, field, None)
        if event is not None:
            user = getattr(event, "from_user", None)
            return user.id if user is not None else None
    return None


class OwnerOnlyMiddleware(BaseMiddleware):
    """Reject every update not sent by ``owner_id`` before any handler runs."""

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update = event if isinstance(event, Update) else None
        user_id = from_user_id(update) if update is not None else None
        if user_id is None or user_id != self.owner_id:
            if update is not None:
                await self._refuse(update)
            return None  # drop: no handler, no gate, no ensure_user, no DB row
        return await handler(event, data)

    async def _refuse(self, update: Update) -> None:
        """Send exactly one polite refusal; nothing else."""
        message = getattr(update, "message", None) or getattr(update, "edited_message", None)
        if message is not None:
            await message.answer(locale.PRIVATE_BOT)
            return
        callback = getattr(update, "callback_query", None)
        if callback is not None:
            await callback.answer(locale.PRIVATE_BOT, show_alert=True)
