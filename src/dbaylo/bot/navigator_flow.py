"""Navigator bot flow: /price (named drug) and /coverage (ПМГ for a service).

Thin handlers over :mod:`dbaylo.navigator.pipeline`. The command *argument* — and,
Tier 1.3, the **FSM answer** typed after a menu/`/price` prompt — is user text and is
screened by the safety gate inside the pipeline (`run_price`/`run_coverage` call
``gate.screen`` first). A command is not a trusted bypass, and neither is being in the
navigator state: "/coverage болить нирка що робити", or the same typed into the drug
field, short-circuits to triage instead of a price search.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from dbaylo import locale
from dbaylo.bot.keyboards import cancel_keyboard
from dbaylo.navigator.pipeline import run_coverage, run_price

router = Router(name="navigator")


class NavStates(StatesGroup):
    waiting_drug = State()
    waiting_service = State()


# --- Price ----------------------------------------------------------------------


async def start_price_dialog(message: Message, state: FSMContext) -> None:
    """Enter the price dialog (from /price or the menu) — always cancellable."""
    await state.set_state(NavStates.waiting_drug)
    await message.answer(locale.NAV_ASK_DRUG, reply_markup=cancel_keyboard())


@router.message(Command("price"))
async def cmd_price(message: Message, command: CommandObject, state: FSMContext) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await start_price_dialog(message, state)
        return
    result = await run_price(arg)  # gated inside the pipeline
    await message.answer(result.text)


@router.message(NavStates.waiting_drug, F.text)
async def on_price_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer(locale.NOTHING_SAVED)
        return
    result = await run_price(text)  # SAME gate as the command arg — a symptom -> triage
    await message.answer(result.text)


# --- Coverage -------------------------------------------------------------------


async def start_coverage_dialog(message: Message, state: FSMContext) -> None:
    """Enter the coverage dialog (from /coverage or the menu) — always cancellable."""
    await state.set_state(NavStates.waiting_service)
    await message.answer(locale.NAV_ASK_SERVICE, reply_markup=cancel_keyboard())


@router.message(Command("coverage"))
async def cmd_coverage(message: Message, command: CommandObject, state: FSMContext) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await start_coverage_dialog(message, state)
        return
    result = await run_coverage(arg)  # gated inside the pipeline
    await message.answer(result.text)


@router.message(NavStates.waiting_service, F.text)
async def on_coverage_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer(locale.NOTHING_SAVED)
        return
    result = await run_coverage(text)  # SAME gate as the command arg
    await message.answer(result.text)
