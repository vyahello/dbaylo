"""Top-level command handlers: ``/start`` and ``/help``.

Thin and import-light so they unit-test without a running Bot. The Stage 3
companion commands (``/checkin``, ``/goal``, ``/goals``) and free-text chat live
in :mod:`dbaylo.bot.companion_flow`; lab intake lives in
:mod:`dbaylo.bot.lab_flow`.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from dbaylo.db import get_session
from dbaylo.labs.intake import ensure_user
from dbaylo.locale import HELP_TEXT, START_TEXT

router = Router(name="commands")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    # Capture the user (telegram_id) on /start too, so proactive reminders can always
    # reach them — previously this only happened on a lab upload / goal / check-in.
    if message.from_user is not None:
        async with get_session() as session:
            await ensure_user(session, message.from_user.id, message.from_user.full_name)
            await session.commit()
    await message.answer(START_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)
