"""Navigator bot commands: /price (named drug) and /coverage (ПМГ for a service).

Thin handlers over :mod:`dbaylo.navigator.pipeline`. The command *argument* is user
text and is screened by the safety gate inside the pipeline — a command is not a
trusted bypass, so "/coverage болить нирка що робити" short-circuits to triage.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from dbaylo import locale
from dbaylo.navigator.pipeline import run_coverage, run_price

router = Router(name="navigator")


@router.message(Command("price"))
async def cmd_price(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await message.answer(locale.NAV_ASK_DRUG)
        return
    result = await run_price(arg)
    await message.answer(result.text)


@router.message(Command("coverage"))
async def cmd_coverage(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await message.answer(locale.NAV_ASK_SERVICE)
        return
    result = await run_coverage(arg)
    await message.answer(result.text)
