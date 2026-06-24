"""Bot and Dispatcher factory + the long-polling entrypoint.

This is the entrypoint the ``dbaylo-bot`` service runs (and what local dev uses).
It also starts the reminder scheduler in-process, so reminders / the daily check-in
fire from the same service — no separate process. The webhook entrypoint in
``dbaylo.web`` shares the same Dispatcher built by ``build_dispatcher``.
"""

from __future__ import annotations

import asyncio
import contextlib

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup

from dbaylo import locale
from dbaylo.bot import (
    companion_flow,
    consult_flow,
    history_flow,
    lab_flow,
    menu_flow,
    navigator_flow,
    proactive_flow,
)
from dbaylo.bot.access import OwnerOnlyMiddleware
from dbaylo.bot.handlers import router
from dbaylo.bot.state_reset import CommandStateResetMiddleware
from dbaylo.bot.storage import SQLiteStorage
from dbaylo.companion import callbacks, history, notewarm
from dbaylo.companion.scheduler import Buttons, ReminderScheduler, Sender
from dbaylo.config import get_settings
from dbaylo.db import get_session
from dbaylo.labs.intake import ensure_user
from dbaylo.labs.labnames import normalize_lab


def build_dispatcher(
    owner_id: int | None = None, *, storage: BaseStorage | None = None
) -> Dispatcher:
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

    FSM state is persisted in a SQLite file (:class:`SQLiteStorage`) so an in-progress
    dialog / symptom interview survives a restart; the connection is opened lazily, so
    building a dispatcher (e.g. in a test) touches no disk.
    """
    resolved_owner = owner_id if owner_id is not None else get_settings().owner_telegram_id
    storage = storage or SQLiteStorage(get_settings().fsm_db_path)
    dispatcher = Dispatcher(storage=storage)
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
    # Consult: its entry callbacks + the ConsultStates free-text turn. Before the companion so the
    # consult state is served here (the companion catch-all is StateFilter(None), so no clash).
    dispatcher.include_router(consult_flow.router)
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


async def apply_bot_commands(bot: Bot) -> None:
    """Populate Telegram's native "/" command menu from ``locale.BOT_COMMANDS``.

    Without this the "/" menu is empty and every command is invisible unless the user
    reads /help. Telegram also shows these as the default chat Menu button, so the whole
    command palette is one tap away — nothing has to be typed from memory.
    """
    commands = [BotCommand(command=name, description=desc) for name, desc in locale.BOT_COMMANDS]
    await bot.set_my_commands(commands)


def make_sender(bot: Bot) -> Sender:
    """A reminder sender that delivers text (+ optional inline buttons) via ``bot``."""

    async def sender(telegram_id: int, text: str, *, buttons: Buttons | None = None) -> None:
        markup = _keyboard(buttons) if buttons else None
        await bot.send_message(telegram_id, text, reply_markup=markup)

    return sender


async def recover_interrupted_analyses(bot: Bot, owner_id: int) -> None:
    """After a restart, offer to finish any analysis that was interrupted mid-run (a deploy /
    crash killed the LLM call, leaving the summary PENDING). One message per affected report with
    a one-tap '▶️ Доробити розбір' — the same cached-or-generate path, which regenerates because
    the summary is empty. Best-effort: a send failure must never block the bot from starting."""
    if not owner_id:
        return
    async with get_session() as session:
        interrupted = await history.find_interrupted_analyses(session)
    for report in interrupted:
        date_txt = report.report_date.isoformat() if report.report_date else locale.HIST_NO_DATE
        lab_txt = normalize_lab(report.lab) or locale.LAB_LAB_UNKNOWN
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=locale.BTN_FINISH_ANALYSIS,
                        callback_data=callbacks.history_interpret(report.id),
                    )
                ]
            ]
        )
        with contextlib.suppress(Exception):
            await bot.send_message(
                owner_id,
                locale.ANALYSIS_INTERRUPTED.format(date=date_txt, lab=lab_txt),
                reply_markup=markup,
            )


async def warm_indicator_notes(owner_id: int) -> None:
    """Fill the educational-note cache for the owner's indicators in the background, so the dynamics
    charts/tables/PDF carry a description for EVERY indicator and render with no claude call. Notes
    are data-independent and persisted, so this is a one-time warm that survives restarts; runs
    best-effort and never blocks startup."""
    if not owner_id:
        return
    async with get_session() as session:
        user = await ensure_user(session, owner_id)
        user_id = user.id
    notewarm.warm_user_notes_in_background(user_id)


async def _run_polling() -> None:
    bot = build_bot()
    dispatcher = build_dispatcher()
    # Reminders run inside the bot process (one service, shared event loop). The live
    # scheduler is shared with handlers via dispatcher data so commands can schedule /
    # unschedule reminders without a restart.
    reminder_scheduler = ReminderScheduler(sender=make_sender(bot))
    dispatcher["reminder_scheduler"] = reminder_scheduler
    await reminder_scheduler.start()
    # Register the "/" command menu so every command is discoverable without typing.
    await apply_bot_commands(bot)
    # Offer to finish any analysis a restart interrupted (best-effort; never blocks startup).
    with contextlib.suppress(Exception):
        await recover_interrupted_analyses(bot, get_settings().owner_telegram_id)
    # Warm the indicator-note cache in the background so every chart/table/PDF has a description and
    # renders without waiting on claude (best-effort; never blocks startup).
    with contextlib.suppress(Exception):
        await warm_indicator_notes(get_settings().owner_telegram_id)
    try:
        await dispatcher.start_polling(bot)
    finally:
        reminder_scheduler.shutdown()


def run() -> None:
    """Console-script entrypoint: start the bot via long polling."""
    asyncio.run(_run_polling())


if __name__ == "__main__":
    run()
