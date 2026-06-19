"""Bot and Dispatcher factory + the long-polling entrypoint.

This is the entrypoint the ``dbaylo-bot`` service runs (and what local dev uses).
It also starts the reminder scheduler in-process, so reminders / the daily check-in
fire from the same service — no separate process. The webhook entrypoint in
``dbaylo.web`` shares the same Dispatcher built by ``build_dispatcher``.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher

from dbaylo.bot import companion_flow, lab_flow, navigator_flow
from dbaylo.bot.access import OwnerOnlyMiddleware
from dbaylo.bot.handlers import router
from dbaylo.companion.scheduler import Sender, build_scheduler
from dbaylo.config import get_settings


def build_dispatcher(owner_id: int | None = None) -> Dispatcher:
    """Build a Dispatcher with the owner lock and all routers registered.

    The owner lock is an **outer** update middleware, so it runs before any router
    or handler (fail-closed: an unset ``owner_id`` of 0 refuses everyone). Router
    order: commands first, then lab intake (documents/photos + its edit FSM), then
    the navigator commands (/price, /coverage), then the companion — whose free-text
    catch-all is ``StateFilter(None)`` so it never steals a turn from an FSM flow.
    """
    resolved_owner = owner_id if owner_id is not None else get_settings().owner_telegram_id
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(OwnerOnlyMiddleware(resolved_owner))
    dispatcher.include_router(router)
    dispatcher.include_router(lab_flow.router)
    dispatcher.include_router(navigator_flow.router)
    dispatcher.include_router(companion_flow.router)
    return dispatcher


def build_bot(token: str | None = None) -> Bot:
    """Build a Bot from the given token (falls back to configured BOT_TOKEN)."""
    resolved = token or get_settings().bot_token
    if not resolved:
        raise RuntimeError("BOT_TOKEN is not set; cannot start the bot.")
    return Bot(token=resolved)


def make_sender(bot: Bot) -> Sender:
    """A reminder sender that delivers a message to a Telegram user via ``bot``."""

    async def sender(telegram_id: int, text: str) -> None:
        await bot.send_message(telegram_id, text)

    return sender


async def _run_polling() -> None:
    bot = build_bot()
    dispatcher = build_dispatcher()
    # Reminders run inside the bot process (one service, shared event loop): build
    # the scheduler from the Reminder rows and start it alongside long polling.
    scheduler = await build_scheduler(sender=make_sender(bot))
    scheduler.start()
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)


def run() -> None:
    """Console-script entrypoint: start the bot via long polling."""
    asyncio.run(_run_polling())


if __name__ == "__main__":
    run()
