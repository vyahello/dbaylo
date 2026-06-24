"""Contextual consultation flow ("Запитати Дбайло") — a UI/entry layer over ``companion.consult``.

A button on a chart / indicator / report reading opens a grounded, multi-turn consultation about
THAT subject. The subject anchor (small, JSON-serializable) lives in FSM state and survives a
restart; the grounded context is re-derived from the DB every turn (``consult_context``). Every
user turn is screened by the gate inside ``consult`` — the deterministic triage owns escalation.

No domain logic here: it wires Telegram events to ``consult_context.build_context`` +
``consult.consult``. The free-text turn is a dedicated FSM state, so it never collides with the
companion's ``StateFilter(None)`` catch-all or the symptom-intake state.
"""

from __future__ import annotations

import contextlib

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from dbaylo import locale
from dbaylo.companion import callbacks, consult, history
from dbaylo.companion.consult_context import (
    KIND_INDICATOR,
    KIND_REPORT,
    KIND_SECTION,
    Subject,
    build_context,
)
from dbaylo.db import get_session
from dbaylo.labs.intake import ensure_user

router = Router(name="consult")


class ConsultStates(StatesGroup):
    active = State()  # a consultation is open; the next free-text turn is a question about it


def _telegram_id(event: Message | CallbackQuery) -> int | None:
    return event.from_user.id if event.from_user else None


def _end_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=locale.CONSULT_BTN_END, callback_data=callbacks.CONSULT_END)]
        ]
    )


async def _typing(message: Message) -> None:
    with contextlib.suppress(Exception):
        await message.bot.send_chat_action(message.chat.id, "typing")  # type: ignore[union-attr]


async def _open(
    message: Message, state: FSMContext, subject: Subject, prompt_template: str
) -> None:
    """Validate the subject resolves, then enter the consult state and prompt the user. The prompt
    template is filled with the subject's resolved Ukrainian label (analyte / report / section)."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=message.chat.id)
        built = await build_context(session, user.id, subject)
    if built is None:
        await message.answer(locale.CONSULT_GONE)
        await state.clear()
        return
    _context, label = built
    await state.set_state(ConsultStates.active)
    await state.update_data(consult_subject=subject.to_dict(), consult_transcript=[])
    await message.answer(prompt_template.format(subject=label), reply_markup=_end_keyboard())


@router.callback_query(F.data.startswith(callbacks.CONSULT_CHART + ":"))
async def on_consult_chart(callback: CallbackQuery, state: FSMContext) -> None:
    """Open a consultation anchored to ONE indicator (the chart the user is looking at)."""
    parsed = callbacks.parse_consult_chart(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    report_id, index = parsed
    await callback.answer()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        items = await history.list_report_pickables(session, user_id=user.id, report_id=report_id)
    if not 0 <= index < len(items):
        await callback.message.answer(locale.CONSULT_GONE)
        return
    item = items[index]
    subject = Subject(
        kind=KIND_INDICATOR, report_id=report_id, analyte_key=item.key, analyte_name=item.name
    )
    await _open(callback.message, state, subject, locale.CONSULT_PROMPT_INDICATOR)


@router.callback_query(F.data.startswith(callbacks.CONSULT_DYN + ":"))
async def on_consult_dyn(callback: CallbackQuery, state: FSMContext) -> None:
    """Open a consultation anchored to ONE indicator from the dynamics browser (category index)."""
    parsed = callbacks.parse_consult_dyn(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    category, index = parsed
    await callback.answer()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        items = await history.aggregate_indicators(session, user_id=user.id)
        indicators = history.indicators_in(items, category)
    if not 0 <= index < len(indicators):
        await callback.message.answer(locale.CONSULT_GONE)
        return
    it = indicators[index]
    subject = Subject(kind=KIND_INDICATOR, report_id=0, analyte_key=it.key, analyte_name=it.name)
    await _open(callback.message, state, subject, locale.CONSULT_PROMPT_INDICATOR)


@router.callback_query(F.data.startswith(callbacks.CONSULT_REPORT + ":"))
async def on_consult_report(callback: CallbackQuery, state: FSMContext) -> None:
    """Open a consultation anchored to a whole report's reading."""
    report_id = callbacks.parse_consult_report(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    await _open(
        callback.message,
        state,
        Subject(kind=KIND_REPORT, report_id=report_id),
        locale.CONSULT_PROMPT_REPORT,
    )


@router.callback_query(F.data.startswith(callbacks.CONSULT_SECTION + ":"))
async def on_consult_section(callback: CallbackQuery, state: FSMContext) -> None:
    """Open a consultation anchored to ONE section of a report's reading (Загалом / Звернути увагу
    / Що допоможе / Коли до лікаря) — the same data, focused on that aspect."""
    parsed = callbacks.parse_consult_section(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    report_id, idx = parsed
    await callback.answer()
    await _open(
        callback.message,
        state,
        Subject(kind=KIND_SECTION, report_id=report_id, section_idx=idx),
        locale.CONSULT_PROMPT_SECTION,
    )


@router.callback_query(F.data == callbacks.CONSULT_END)
async def on_consult_end(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.CONSULT_ENDED)


@router.message(ConsultStates.active, F.text & ~F.text.startswith("/"))
async def on_consult_turn(message: Message, state: FSMContext) -> None:
    """One consultation turn: re-derive the grounded context, answer the question, keep the
    conversation open (multi-turn). A '/command' or menu-label tap ends it (reset middleware)."""
    text = (message.text or "").strip()
    if not text:
        await message.answer(locale.CONSULT_EMPTY)
        return
    data = await state.get_data()
    subject = Subject.from_dict(dict(data.get("consult_subject") or {}))
    transcript: list[consult.Turn] = list(data.get("consult_transcript") or [])
    tg = _telegram_id(message)
    if tg is None:
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        built = await build_context(session, user.id, subject)
    if built is None:  # the report was deleted mid-consult — end gracefully
        await state.clear()
        await message.answer(locale.CONSULT_GONE)
        return
    context, _label = built
    transcript.append({"role": "user", "text": text})
    await _typing(message)
    reply = await consult.consult(context, transcript)
    transcript.append({"role": "assistant", "text": reply.text})
    # Keep only the recent exchange in state so it never grows unbounded across a long consult.
    trimmed = transcript[-2 * consult.MAX_CONTEXT_TURNS :]
    await state.update_data(consult_transcript=trimmed)
    await message.answer(reply.text, reply_markup=_end_keyboard())
