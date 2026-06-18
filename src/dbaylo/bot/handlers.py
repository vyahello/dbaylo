"""Command handlers for the bot skeleton.

Stage 1 is intentionally minimal: ``/start``, ``/help``, and a stub ``/checkin``.
Handlers are thin and import-light so they can be unit-tested without a running
Bot. The check-in flow (and its link to triage) is built in Stage 3.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from dbaylo.locale import CHECKIN_STUB_TEXT, HELP_TEXT, START_TEXT

router = Router(name="commands")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("checkin"))
async def cmd_checkin(message: Message) -> None:
    await message.answer(CHECKIN_STUB_TEXT)
