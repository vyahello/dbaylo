"""Keep the Telegram 'typing…' indicator alive across a long LLM call.

A Telegram chat action expires after ~5 seconds, so a reply that takes 30 s+ leaves the user
staring at a vanished indicator and wondering if anything is happening. This re-sends 'typing'
every few seconds until the work is done, then stops the MOMENT the reply is sent — so the
indicator is visible exactly while Дбайло is actually thinking, not before and not after.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from aiogram.types import Message

_REFRESH_S = 4.0  # Telegram's 'typing' lasts ~5 s; refresh just under that so it never lapses.


@contextlib.asynccontextmanager
async def keep_typing(message: Message) -> AsyncIterator[None]:
    """Show 'typing…' continuously for the duration of the ``async with`` block. Best-effort — a
    failed chat action never breaks the turn, and the keep-alive is always cancelled on exit."""

    async def _loop() -> None:
        while True:
            with contextlib.suppress(Exception):
                await message.bot.send_chat_action(message.chat.id, "typing")  # type: ignore[union-attr]
            await asyncio.sleep(_REFRESH_S)

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
