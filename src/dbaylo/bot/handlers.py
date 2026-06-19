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

from dbaylo.locale import HELP_TEXT, START_TEXT

router = Router(name="commands")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)
