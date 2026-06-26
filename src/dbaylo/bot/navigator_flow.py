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
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.bot.keyboards import cancel_keyboard
from dbaylo.bot.typing import keep_typing
from dbaylo.companion import callbacks, medications
from dbaylo.db import get_session
from dbaylo.db.models import Medication
from dbaylo.labs.intake import ensure_user
from dbaylo.navigator.pipeline import run_coverage, run_price

router = Router(name="navigator")


class NavStates(StatesGroup):
    waiting_drug = State()
    waiting_service = State()


def _short(name: str, limit: int = 30) -> str:
    name = name.strip()
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def _telegram_id(event: Message | CallbackQuery) -> int | None:
    return event.from_user.id if event.from_user else None


# --- Price ----------------------------------------------------------------------


async def start_price_dialog(message: Message, state: FSMContext) -> None:
    """Enter the type-a-drug price dialog (the ✏️ fallback) — always cancellable."""
    await state.set_state(NavStates.waiting_drug)
    await message.answer(locale.NAV_ASK_DRUG, reply_markup=cancel_keyboard())


async def _unique_meds(session: AsyncSession, *, user_id: int) -> list[Medication]:
    """The user's medications, de-duplicated by name (order kept) — the price proposals."""
    seen: set[str] = set()
    out: list[Medication] = []
    for med in await medications.list_medications(session, user_id=user_id):
        key = (med.name or "").strip().casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(med)
    return out


async def open_price_options(message: Message, state: FSMContext, *, telegram_id: int) -> None:
    """The agent's price screen: propose the user's OWN meds (one-tap price) + ✏️ to type another.
    Falls back to the type dialog when there are no meds yet."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        meds = await _unique_meds(session, user_id=user.id)
    if not meds:
        await start_price_dialog(message, state)
        return
    rows = [
        [
            InlineKeyboardButton(
                text=locale.BTN_PRICE_MED.format(name=_short(med.name)),
                callback_data=callbacks.price_med(index),
            )
        ]
        for index, med in enumerate(meds)
    ]
    rows.append(
        [InlineKeyboardButton(text=locale.BTN_PRICE_TYPE, callback_data=callbacks.PRICE_TYPE)]
    )
    await message.answer(
        locale.NAV_PRICE_OPTIONS, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


async def _send_price(message: Message, drug: str) -> None:
    """Run the gated price lookup (LLM re-parse fallback ON — marked «перевір») with a typing
    indicator, then send the result."""
    async with keep_typing(message):
        result = await run_price(drug, use_llm_fallback=True)
    await message.answer(result.text)


@router.message(Command("price"))
async def cmd_price(message: Message, command: CommandObject, state: FSMContext) -> None:
    arg = (command.args or "").strip()
    if not arg:
        tg = _telegram_id(message)
        if tg is not None:
            await open_price_options(message, state, telegram_id=tg)
        return
    await _send_price(message, arg)  # gated inside the pipeline


@router.message(NavStates.waiting_drug, F.text)
async def on_price_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer(locale.NOTHING_SAVED)
        return
    await _send_price(message, text)  # SAME gate as the command arg — a symptom -> triage


@router.callback_query(F.data == callbacks.PRICE_TYPE)
async def on_price_type(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await start_price_dialog(callback.message, state)


@router.callback_query(F.data.startswith(callbacks.PRICE_MED + ":"))
async def on_price_med(callback: CallbackQuery, state: FSMContext) -> None:
    """One-tap price for a proposed medication (re-derived by index on tap)."""
    # Ack first: the lookup is a multi-second fetch (+ maybe an LLM re-parse).
    await callback.answer()
    index = callbacks.parse_price_med(callback.data or "")
    tg = _telegram_id(callback)
    if index is None or tg is None or not isinstance(callback.message, Message):
        return
    await state.clear()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        meds = await _unique_meds(session, user_id=user.id)
    if 0 <= index < len(meds):
        await _send_price(callback.message, meds[index].name)


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
