"""Command handlers for the bot skeleton.

Stage 1 is intentionally minimal: ``/start``, ``/help``, and a stub ``/checkin``.
Handlers are thin and import-light so they can be unit-tested without a running
Bot. The check-in flow (and its link to triage) is built in Stage 3.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from dbaylo.triage.safety import DISCLAIMER

router = Router(name="commands")

START_TEXT = (
    "Привіт! Я Дбайло — your caring health companion. 🌿\n\n"
    "I help you keep track of how you're doing, watch for warning signs, and "
    "build habits that stick.\n\n"
    f"{DISCLAIMER}\n\n"
    "Try /help to see what I can do."
)

HELP_TEXT = (
    "Here's what I can do so far:\n\n"
    "/start — meet Дбайло\n"
    "/help — this message\n"
    "/checkin — a quick daily check-in (coming soon)\n\n"
    f"{DISCLAIMER}"
)

CHECKIN_STUB_TEXT = (
    "Daily check-ins are on the way. 🛠️\n\n"
    "Soon I'll ask about your sleep, water, training, mood, and how you feel — "
    "and gently flag anything worth a doctor's eyes.\n\n"
    f"{DISCLAIMER}"
)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("checkin"))
async def cmd_checkin(message: Message) -> None:
    await message.answer(CHECKIN_STUB_TEXT)
