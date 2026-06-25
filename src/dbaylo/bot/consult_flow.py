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

import html
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from dbaylo import locale
from dbaylo.bot.formatting import answer_chunked, render_interpretation_html
from dbaylo.bot.keyboards import clear_inline_keyboard
from dbaylo.bot.typing import keep_typing
from dbaylo.companion import (
    callbacks,
    cities,
    consult,
    consult_memory,
    history,
    proactive,
    reminders,
)
from dbaylo.companion.consult_context import (
    KIND_INDICATOR,
    KIND_REPORT,
    KIND_SECTION,
    Subject,
    build_context,
)
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.config import get_settings
from dbaylo.db import get_session
from dbaylo.db.models import ConsultMemory
from dbaylo.labs.intake import ensure_user

router = Router(name="consult")

_WHEN_OFFSETS = (
    (locale.CONSULT_BTN_WHEN_1W, 7),
    (locale.CONSULT_BTN_WHEN_2W, 14),
    (locale.CONSULT_BTN_WHEN_1M, 30),
    (locale.CONSULT_BTN_WHEN_3M, 90),
)


class ConsultStates(StatesGroup):
    active = State()  # a consultation is open; the next free-text turn is a question about it


class ConsultRemindStates(StatesGroup):
    waiting_label = State()  # awaiting the reminder's subject text
    waiting_date = State()  # awaiting a typed date/period (offset buttons also work here)


class ConsultClinicStates(StatesGroup):
    waiting_city = State()  # awaiting the city to search clinics in (remembered after)


def _now() -> datetime:
    return datetime.now(ZoneInfo(get_settings().timezone))


def _telegram_id(event: Message | CallbackQuery) -> int | None:
    return event.from_user.id if event.from_user else None


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


async def _remember(tg: int, report_id: int | None, turns: list[tuple[str, str]]) -> None:
    """Persist consultation turns to durable cross-session memory (best-effort) so a LATER
    consultation can recall them. ``turns`` is a list of (role, text) pairs."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        for role, text in turns:
            await consult_memory.record_turn(
                session, user_id=user.id, role=role, text=text, report_id=report_id
            )
        await session.commit()


def _reply_keyboard() -> InlineKeyboardMarkup:
    """Under each consult reply: set a reminder (#4d), find where to do an exam (#3), or finish."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(locale.CONSULT_BTN_REMIND, callbacks.CONSULT_REMIND),
                _btn(locale.CONSULT_BTN_CLINICS, callbacks.CONSULT_CLINICS),
            ],
            [_btn(locale.CONSULT_BTN_END, callbacks.CONSULT_END)],
        ]
    )


def _end_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(locale.CONSULT_BTN_END, callbacks.CONSULT_END)]]
    )


def _when_keyboard() -> InlineKeyboardMarkup:
    offsets = [_btn(label, callbacks.consult_remind_when(days)) for label, days in _WHEN_OFFSETS]
    rows = [offsets[0:2], offsets[2:4]]
    rows.append([_btn(locale.CONSULT_BTN_RESUME, callbacks.CONSULT_RESUME)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _resume_consult(message: Message, state: FSMContext) -> None:
    """Return from a reminder/clinic sub-flow to the open consultation (its subject + transcript are
    still in FSM data — we never cleared them). If there is none, just clear."""
    data = await state.get_data()
    if data.get("consult_subject"):
        await state.set_state(ConsultStates.active)
        await message.answer(locale.CONSULT_RESUMED, reply_markup=_reply_keyboard())
    else:
        await state.clear()


async def _open(
    message: Message, state: FSMContext, subject: Subject, prompt_template: str
) -> None:
    """Validate the subject resolves, then enter the consult state and prompt the user. The prompt
    template is filled with the subject's resolved Ukrainian label (analyte / report / section)."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=message.chat.id)
        built = await build_context(session, user.id, subject, today=date.today())
    if built is None:
        await message.answer(locale.CONSULT_GONE)
        await state.clear()
        return
    _context, label = built
    await state.set_state(ConsultStates.active)
    await state.update_data(
        consult_subject=subject.to_dict(), consult_transcript=[], consult_label=label
    )
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


async def _run_consult_turn(message: Message, state: FSMContext, text: str, *, tg: int) -> None:
    """One consultation turn: append the user's text, re-derive the grounded context, answer in the
    SAME conversation, and keep it open."""
    text = text.strip()
    if not text:
        await message.answer(locale.CONSULT_EMPTY)
        return
    # A typed ask to be reminded opens the reminder mini-flow (never the LLM — which would otherwise
    # wrongly claim it cannot set reminders).
    if _wants_reminder(text):
        await _start_reminder(message, state)
        return
    # An explicit ask for concrete clinics (addresses / contacts / ratings) goes to the web-search
    # finder instead of the general (grounded, tool-free) consult.
    if _wants_clinics(text):
        await _do_clinic_search(message, state, user_text=text)
        return
    data = await state.get_data()
    subject = Subject.from_dict(dict(data.get("consult_subject") or {}))
    transcript: list[consult.Turn] = list(data.get("consult_transcript") or [])
    # The live transcript already carries this session's turns to the model — exclude them from the
    # recalled cross-session memory so the same line is never shown twice mid-conversation.
    recall_exclude = frozenset(t["text"].strip() for t in transcript if t.get("text"))
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        built = await build_context(
            session, user.id, subject, today=date.today(), recall_exclude=recall_exclude
        )
    if built is None:  # the report was deleted mid-consult — end gracefully
        await state.clear()
        await message.answer(locale.CONSULT_GONE)
        return
    context, _label = built
    transcript.append({"role": "user", "text": text})
    # Keep 'typing…' alive for the whole (multi-second) LLM call — it stops the moment we reply.
    async with keep_typing(message):
        reply = await consult.consult(context, transcript)
    transcript.append({"role": "assistant", "text": reply.text})
    # Persist this exchange to durable memory so a future consultation remembers it.
    await _remember(tg, subject.report_id or None, [("user", text), ("assistant", reply.text)])
    # Keep only the recent exchange in state so it never grows unbounded across a long consult.
    trimmed = transcript[-2 * consult.MAX_CONTEXT_TURNS :]
    await state.set_state(ConsultStates.active)
    await state.update_data(consult_transcript=trimmed)
    # Premium formatting: the light *bold*/_italic_ markers become real HTML; the single canonical
    # disclaimer becomes an italic P.S. (the engine already dropped any model-added duplicate).
    await answer_chunked(
        message,
        render_interpretation_html(reply.text),
        parse_mode=ParseMode.HTML,
        reply_markup=_reply_keyboard(),
    )


@router.message(ConsultStates.active, F.text & ~F.text.startswith("/"))
async def on_consult_turn(message: Message, state: FSMContext) -> None:
    """A typed turn during an open consultation. A '/command' or menu-label tap ends it (the reset
    middleware), so a command is never consumed as a question."""
    tg = _telegram_id(message)
    if tg is not None:
        await _run_consult_turn(message, state, message.text or "", tg=tg)


# --- #4d: set a reminder agreed during the consultation -------------------------

# A typed ask to be reminded ("зроби нагадування", "нагадай мені") opens the SAME mini-flow as the
# 🔔 button — so Дбайло never hallucinates that it cannot set reminders.
_REMIND_INTENT_RE = re.compile(
    r"(нагада[йєити]|нагадуванн|зроби.{0,15}нагад|постав.{0,15}нагад|\bremind)", re.IGNORECASE
)


def _wants_reminder(text: str) -> bool:
    return bool(_REMIND_INTENT_RE.search(text.casefold()))


async def _start_reminder(message: Message, state: FSMContext) -> None:
    await state.set_state(ConsultRemindStates.waiting_label)
    await message.answer(
        locale.CONSULT_REMIND_ASK_LABEL,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_btn(locale.CONSULT_BTN_RESUME, callbacks.CONSULT_RESUME)]]
        ),
    )


@router.callback_query(F.data == callbacks.CONSULT_REMIND)
async def on_consult_remind(callback: CallbackQuery, state: FSMContext) -> None:
    """Дбайло proposes a reminder; this opens the mini-flow — ask WHAT, then WHEN."""
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    await _start_reminder(callback.message, state)


@router.message(ConsultRemindStates.waiting_label, F.text & ~F.text.startswith("/"))
async def on_remind_label(message: Message, state: FSMContext) -> None:
    label = (message.text or "").strip()
    if not label:
        await message.answer(locale.CONSULT_EMPTY)
        return
    await state.set_state(ConsultRemindStates.waiting_date)
    await state.update_data(consult_remind_label=label)
    await message.answer(
        locale.CONSULT_REMIND_ASK_WHEN.format(label=label), reply_markup=_when_keyboard()
    )


async def _create_consult_reminder(
    message: Message,
    state: FSMContext,
    *,
    run_at: datetime,
    scheduler: ReminderScheduler,
) -> None:
    """Persist + live-schedule the reminder, confirm, and resume the consultation."""
    data = await state.get_data()
    label = str(data.get("consult_remind_label") or "").strip() or locale.LAB_REPEAT_LABEL
    tg = _telegram_id(message)
    if tg is None:
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        await proactive.add_consult_reminder(
            session, user=user, run_at=run_at, label=label, scheduler=scheduler
        )
        await session.commit()
    await message.answer(
        locale.CONSULT_REMIND_SET.format(label=label, when=run_at.date().isoformat())
    )
    # Record the reminder in the transcript so the consultation stays aware of it (context).
    transcript: list[consult.Turn] = list(data.get("consult_transcript") or [])
    transcript.append(
        {
            "role": "assistant",
            "text": f"(Я створив нагадування: «{label}» на {run_at.date().isoformat()}.)",
        }
    )
    await state.update_data(consult_transcript=transcript[-2 * consult.MAX_CONTEXT_TURNS :])
    await _resume_consult(message, state)


@router.callback_query(F.data.startswith(callbacks.CONSULT_REMIND_WHEN + ":"))
async def on_remind_when(
    callback: CallbackQuery, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
    days = callbacks.parse_consult_remind_when(callback.data or "")
    if days is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    await _create_consult_reminder(
        callback.message, state, run_at=_now() + timedelta(days=days), scheduler=reminder_scheduler
    )


@router.message(ConsultRemindStates.waiting_date, F.text & ~F.text.startswith("/"))
async def on_remind_date(
    message: Message, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
    """A typed date (YYYY-MM-DD) or a period ('через 2 місяці') — the offset buttons also work."""
    run_at = _parse_when((message.text or "").strip())
    if run_at is None:
        await message.answer(locale.CONSULT_REMIND_BAD_DATE)  # stay in state, let them retry
        return
    await _create_consult_reminder(message, state, run_at=run_at, scheduler=reminder_scheduler)


def _parse_when(text: str) -> datetime | None:
    """A future moment from a typed period ('через 2 місяці') or an ISO date ('2026-09-01'), or
    None. A past date is rejected (a reminder is always in the future)."""
    relative = reminders.parse_relative_when(text, base=_now())
    if relative is not None:
        return relative
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    when = datetime.combine(parsed, datetime.min.time(), tzinfo=ZoneInfo(get_settings().timezone))
    return when if when.date() > _now().date() else None


# --- #3: WHERE to do an exam — a REAL web-search clinic finder (owner-enabled) ---

# An explicit ask for concrete clinics — addresses, contacts, ratings — routes to the finder.
_CLINIC_INTENT_RE = re.compile(
    r"(адрес|телефон|контакт|рейтинг|де (можна )?(зроби|здат|пройт|зробл)|"
    r"яку?\s+(клінік|лаборатор)|куди (піти|звернут|їхати)|"
    r"знайди.{0,25}(клінік|лаборатор|центр|лікар)|порекоменду.{0,25}(клінік|лікар|центр))",
    re.IGNORECASE,
)


def _wants_clinics(text: str) -> bool:
    return bool(_CLINIC_INTENT_RE.search(text.casefold()))


async def _run_clinic_search(
    message: Message, state: FSMContext, *, city: str, transcript: list[consult.Turn]
) -> None:
    """Web-search real clinics for what was discussed, in ``city``, then resume the consultation."""
    data = await state.get_data()
    label = str(data.get("consult_label") or "")
    recent = "\n".join(t["text"] for t in transcript[-6:] if t.get("role") == "user")
    context = f"Обговорюємо: {label}.\nОстанні повідомлення користувача:\n{recent}".strip()
    await message.answer(locale.CONSULT_CLINICS_SEARCHING)
    async with keep_typing(message):  # web search + generation can take a while
        text = await consult.find_clinics(context, city)
    transcript.append({"role": "assistant", "text": text})
    await state.set_state(ConsultStates.active)
    await state.update_data(consult_transcript=transcript[-2 * consult.MAX_CONTEXT_TURNS :])
    # Remember WHERE we looked (the substantive result) for future continuity.
    subject = Subject.from_dict(dict(data.get("consult_subject") or {}))
    tg = _telegram_id(message)
    if tg is not None:
        last_user = next((t["text"] for t in reversed(transcript) if t.get("role") == "user"), "")
        turns = [("user", last_user)] if last_user else []
        turns.append(("assistant", text))
        await _remember(tg, subject.report_id or None, turns)
    await answer_chunked(
        message,
        render_interpretation_html(text),
        parse_mode=ParseMode.HTML,
        reply_markup=_reply_keyboard(),
    )


async def _do_clinic_search(message: Message, state: FSMContext, *, user_text: str = "") -> None:
    """Start the clinic search: search now if the city is known, else ask for it (then remember)."""
    data = await state.get_data()
    if not data.get("consult_subject"):
        return  # not inside a consultation
    transcript: list[consult.Turn] = list(data.get("consult_transcript") or [])
    if user_text:
        transcript.append({"role": "user", "text": user_text})
    city = str(data.get("consult_city") or "").strip()
    if not city:
        # The user may have ALREADY named the city ("де зробити X у Львові?") — detect it from the
        # request + recent turns before asking, so we never ask for a city they just gave.
        recent_user = " ".join(t["text"] for t in transcript[-4:] if t.get("role") == "user")
        detected = cities.parse_city(recent_user)
        if detected:
            city = detected
            await state.update_data(consult_city=detected)
    if not city:
        await state.update_data(consult_transcript=transcript[-2 * consult.MAX_CONTEXT_TURNS :])
        await state.set_state(ConsultClinicStates.waiting_city)
        await message.answer(
            locale.CONSULT_CLINICS_ASK_CITY,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[_btn(locale.CONSULT_BTN_RESUME, callbacks.CONSULT_RESUME)]]
            ),
        )
        return
    await _run_clinic_search(message, state, city=city, transcript=transcript)


@router.callback_query(F.data == callbacks.CONSULT_CLINICS)
async def on_consult_clinics(callback: CallbackQuery, state: FSMContext) -> None:
    """🏥 Де зробити — find REAL clinics (web search) for what was discussed, in the user's city."""
    tg = _telegram_id(callback)
    data = await state.get_data()
    if tg is None or not isinstance(callback.message, Message) or not data.get("consult_subject"):
        await callback.answer()
        return
    await callback.answer()
    await _do_clinic_search(callback.message, state)


@router.message(ConsultClinicStates.waiting_city, F.text & ~F.text.startswith("/"))
async def on_clinic_city(message: Message, state: FSMContext) -> None:
    city = (message.text or "").strip()
    if not city:
        await message.answer(locale.CONSULT_CLINICS_ASK_CITY)
        return
    await state.update_data(consult_city=city)
    data = await state.get_data()
    transcript: list[consult.Turn] = list(data.get("consult_transcript") or [])
    await _run_clinic_search(message, state, city=city, transcript=transcript)


@router.callback_query(F.data == callbacks.CONSULT_RESUME)
async def on_consult_resume(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _resume_consult(callback.message, state)


# --- /memory: view the cross-session memory + "забути все" (two-step) ------------

_MEMORY_LINE_CAP = 200  # truncate a long remembered turn in the view (the full text stays stored)


def _forget_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(locale.MEMORY_BTN_FORGET_ALL, callbacks.MEMORY_FORGET)]]
    )


def _render_memory_view(turns: list[ConsultMemory], total: int) -> str:
    """User-facing Ukrainian view of remembered consultation turns, as escaped HTML."""
    lines = [locale.MEMORY_VIEW_HEADER, "", locale.MEMORY_VIEW_INTRO, ""]
    lines.append(locale.MEMORY_VIEW_COUNT.format(total=total))
    if total > len(turns):
        lines.append(locale.MEMORY_VIEW_SHOWN.format(shown=len(turns)))
    lines.append("")
    for turn in turns:
        icon = locale.MEMORY_ROLE_USER if turn.role == "user" else locale.MEMORY_ROLE_BOT
        day = turn.created_at.date().isoformat() if turn.created_at else "?"
        body = turn.text.strip()
        if len(body) > _MEMORY_LINE_CAP:
            body = body[:_MEMORY_LINE_CAP].rstrip() + "…"
        lines.append(f"{icon} <i>{day}</i> {html.escape(body)}")
    return "\n".join(lines)


@router.message(Command("memory"))
async def on_memory(message: Message) -> None:
    """Перегляд памʼяті — show what Дбайло remembers, with a forget-all button."""
    tg = _telegram_id(message)
    if tg is None:
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        total = await consult_memory.count(session, user_id=user.id)
        turns = (
            await consult_memory.recent_turns(session, user_id=user.id, limit=16) if total else []
        )
    if not turns:
        await message.answer(locale.MEMORY_VIEW_EMPTY, parse_mode=ParseMode.HTML)
        return
    await answer_chunked(
        message,
        _render_memory_view(turns, total),
        parse_mode=ParseMode.HTML,
        reply_markup=_forget_keyboard(),
    )


@router.callback_query(F.data == callbacks.MEMORY_FORGET)
async def on_memory_forget(callback: CallbackQuery) -> None:
    """Step 1 of «забути все»: confirm before wiping anything."""
    tg = _telegram_id(callback)
    if tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        total = await consult_memory.count(session, user_id=user.id)
    if not total:
        await callback.message.answer(locale.MEMORY_FORGET_EMPTY)
        return
    await callback.message.answer(
        locale.MEMORY_FORGET_CONFIRM.format(total=total),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _btn(locale.MEMORY_BTN_FORGET_YES, callbacks.MEMORY_FORGET_OK),
                    _btn(locale.MEMORY_BTN_FORGET_NO, callbacks.MEMORY_FORGET_NO),
                ]
            ]
        ),
    )


@router.callback_query(F.data == callbacks.MEMORY_FORGET_OK)
async def on_memory_forget_ok(callback: CallbackQuery) -> None:
    """Step 2: confirmed — forget everything remembered for this user."""
    tg = _telegram_id(callback)
    if tg is None:
        await callback.answer()
        return
    await clear_inline_keyboard(callback)  # consume the confirm buttons (no re-tap / cancel after)
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        deleted = await consult_memory.clear_all(session, user_id=user.id)
        await session.commit()
    if isinstance(callback.message, Message):
        done = locale.MEMORY_FORGET_DONE.format(total=deleted)
        await callback.message.answer(done if deleted else locale.MEMORY_FORGET_EMPTY)
    await callback.answer()


@router.callback_query(F.data == callbacks.MEMORY_FORGET_NO)
async def on_memory_forget_no(callback: CallbackQuery) -> None:
    await clear_inline_keyboard(callback)  # consume the confirm buttons
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.MEMORY_FORGET_CANCELLED)
    await callback.answer()
