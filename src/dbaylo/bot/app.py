"""Bot and Dispatcher factory + a long-polling entrypoint for local dev.

Production uses the webhook entrypoint in ``dbaylo.web``; long polling here keeps
local iteration simple. Both share the same Dispatcher built by ``build_dispatcher``.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher

from dbaylo.bot import lab_flow
from dbaylo.bot.handlers import router
from dbaylo.config import get_settings


def build_dispatcher() -> Dispatcher:
    """Build a Dispatcher with all routers registered."""
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    dispatcher.include_router(lab_flow.router)
    return dispatcher


def build_bot(token: str | None = None) -> Bot:
    """Build a Bot from the given token (falls back to configured BOT_TOKEN)."""
    resolved = token or get_settings().bot_token
    if not resolved:
        raise RuntimeError("BOT_TOKEN is not set; cannot start the bot.")
    return Bot(token=resolved)


async def _run_polling() -> None:
    bot = build_bot()
    dispatcher = build_dispatcher()
    await dispatcher.start_polling(bot)


def run() -> None:
    """Console-script entrypoint: start the bot via long polling."""
    asyncio.run(_run_polling())


if __name__ == "__main__":
    run()
