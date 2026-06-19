"""Bot and Dispatcher factory + the long-polling entrypoint.

This is the entrypoint the ``dbaylo-bot`` service runs (and what local dev uses).
It also starts the reminder scheduler in-process, so reminders / the daily check-in
fire from the same service — no separate process. The webhook entrypoint in
``dbaylo.web`` shares the same Dispatcher built by ``build_dispatcher``.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from dbaylo.bot import (
    companion_flow,
    history_flow,
    lab_flow,
    menu_flow,
    navigator_flow,
    proactive_flow,
)
from dbaylo.bot.access import OwnerOnlyMiddleware
from dbaylo.bot.handlers import router
from dbaylo.bot.state_reset import CommandStateResetMiddleware
from dbaylo.companion.scheduler import Buttons, ReminderScheduler, Sender
from dbaylo.config import get_settings


def build_dispatcher(owner_id: int | None = None) -> Dispatcher:
    """Build a Dispatcher with the owner lock and all routers registered.

    The owner lock is an **outer** update middleware, so it runs before any router
    or handler (fail-closed: an unset ``owner_id`` of 0 refuses everyone). A message-
    level ``CommandStateResetMiddleware`` then aborts an in-progress dialog on a command
    or menu-label tap. Router order: commands first, then the button menu (its exact-
    label taps must win over later text handlers), then lab intake (documents/photos +
    its edit FSM), the navigator commands (/price, /coverage), proactive management, the
    history flow (it claims only free text that *looks* like a history request), and
    finally the companion — whose free-text catch-all is ``StateFilter(None)`` so it
    never steals a turn from an FSM flow.
    """
    resolved_owner = owner_id if owner_id is not None else get_settings().owner_telegram_id
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(OwnerOnlyMiddleware(resolved_owner))
    # Runs before any router resolves a handler: a /command aborts an in-progress FSM
    # dialog so it is never consumed as the dialog's text answer.
    dispatcher.message.outer_middleware(CommandStateResetMiddleware())
    dispatcher.include_router(router)
    # The menu is registered early so its exact-label taps win over the history-NL and
    # companion free-text handlers (a reply-keyboard tap is a plain text message).
    dispatcher.include_router(menu_flow.router)
    dispatcher.include_router(lab_flow.router)
    dispatcher.include_router(navigator_flow.router)
    dispatcher.include_router(proactive_flow.router)
    dispatcher.include_router(history_flow.router)
    dispatcher.include_router(companion_flow.router)
    return dispatcher


def build_bot(token: str | None = None) -> Bot:
    """Build a Bot from the given token (falls back to configured BOT_TOKEN)."""
    resolved = token or get_settings().bot_token
    if not resolved:
        raise RuntimeError("BOT_TOKEN is not set; cannot start the bot.")
    return Bot(token=resolved)


def _keyboard(buttons: Buttons) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=cd)] for label, cd in buttons
        ]
    )


def make_sender(bot: Bot) -> Sender:
    """A reminder sender that delivers text (+ optional inline buttons) via ``bot``."""

    async def sender(telegram_id: int, text: str, *, buttons: Buttons | None = None) -> None:
        markup = _keyboard(buttons) if buttons else None
        await bot.send_message(telegram_id, text, reply_markup=markup)

    return sender


async def _run_polling() -> None:
    bot = build_bot()
    dispatcher = build_dispatcher()
    # Reminders run inside the bot process (one service, shared event loop). The live
    # scheduler is shared with handlers via dispatcher data so commands can schedule /
    # unschedule reminders without a restart.
    reminder_scheduler = ReminderScheduler(sender=make_sender(bot))
    dispatcher["reminder_scheduler"] = reminder_scheduler
    await reminder_scheduler.start()
    try:
        await dispatcher.start_polling(bot)
    finally:
        reminder_scheduler.shutdown()


def run() -> None:
    """Console-script entrypoint: start the bot via long polling."""
    asyncio.run(_run_polling())


if __name__ == "__main__":
    run()
