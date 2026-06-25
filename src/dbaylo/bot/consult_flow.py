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
import html
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.bot.formatting import answer_chunked, render_interpretation_html
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
from dbaylo.db.models import ConsultMemory, LabReport
from dbaylo.labs.humanize import strip_markup
from dbaylo.labs.intake import ensure_user
from dbaylo.labs.labnames import normalize_lab
from dbaylo.triage.safety import DISCLAIMER

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


async def _run_consult_turn(
    message: Message, state: FSMContext, text: str, *, tg: int, scheduler: ReminderScheduler
) -> None:
    """One consultation turn: append the user's text, re-derive the grounded context, answer in the
    SAME conversation, and keep it open."""
    text = text.strip()
    if not text:
        await message.answer(locale.CONSULT_EMPTY)
        return
    # A typed ask to be reminded — OR to be "booked" (which Дбайло can't do, so it saves a reminder
    # instead of repeating that it can't) — opens the smart reminder mini-flow (never the LLM). The
    # subject/date are inferred from this message + the conversation, so we don't re-ask.
    if _wants_reminder(text) or _wants_booking(text):
        await _start_reminder(
            message, state, scheduler=scheduler, trigger_text=text, booking=_wants_booking(text)
        )
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
async def on_consult_turn(
    message: Message, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
    """A typed turn during an open consultation. A '/command' or menu-label tap ends it (the reset
    middleware), so a command is never consumed as a question."""
    tg = _telegram_id(message)
    if tg is not None:
        await _run_consult_turn(
            message, state, message.text or "", tg=tg, scheduler=reminder_scheduler
        )


# --- #4d: set a reminder agreed during the consultation -------------------------

# A typed ask to be reminded ("зроби нагадування", "нагадай мені") opens the SAME mini-flow as the
# 🔔 button — so Дбайло never hallucinates that it cannot set reminders.
_REMIND_INTENT_RE = re.compile(
    r"(нагада[йєити]|нагадуванн|зроби.{0,15}нагад|постав.{0,15}нагад|\bremind)", re.IGNORECASE
)
# A "book me" request ("запиши мене на …", "записати мене", "забронюй на …"). Дбайло can't actually
# call a clinic, so instead of repeating that, it SAVES the appointment as a reminder + nudges the
# user to call. Routed to the same smart flow (subject/date inferred), with a booking-aware confirm.
_BOOKING_INTENT_RE = re.compile(
    r"\b(запиш[иі]|записа\w*|заброню\w*)\s+(мене|на|до)\b", re.IGNORECASE
)


def _wants_reminder(text: str) -> bool:
    return bool(_REMIND_INTENT_RE.search(text.casefold()))


def _wants_booking(text: str) -> bool:
    return bool(_BOOKING_INTENT_RE.search(text.casefold()))


async def _ask_reminder_subject(message: Message, state: FSMContext) -> None:
    """Fallback: ask WHAT to remind about (only when we couldn't infer it from the conversation)."""
    await state.set_state(ConsultRemindStates.waiting_label)
    await message.answer(
        locale.CONSULT_REMIND_ASK_LABEL,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_btn(locale.CONSULT_BTN_RESUME, callbacks.CONSULT_RESUME)]]
        ),
    )


async def _start_reminder(
    message: Message,
    state: FSMContext,
    *,
    scheduler: ReminderScheduler,
    trigger_text: str = "",
    booking: bool = False,
) -> None:
    """Smart reminder entry. Infer WHAT (and, if stated, WHEN) from the user's request + the
    conversation, instead of always asking 'про що?'. Create it outright when both are known; ask
    only for the missing piece; fall back to asking the subject only when it can't be inferred.
    ``booking`` flags a "запиши мене" request, so the confirmation explains Дбайло can't book."""
    data = await state.get_data()
    transcript: list[consult.Turn] = list(data.get("consult_transcript") or [])
    async with keep_typing(message):
        draft = await consult.extract_reminder(trigger_text, transcript, today=_now().date())
    if draft is None or not draft.subject:
        await _ask_reminder_subject(message, state)
        return
    await state.update_data(consult_remind_label=draft.subject)
    run_at = _parse_when(draft.date) if draft.date else None
    if run_at is not None:  # we know WHAT and WHEN — just create it
        await _create_consult_reminder(
            message, state, run_at=run_at, scheduler=scheduler, booking=booking
        )
        return
    # We know WHAT but not WHEN — ask only the date (subject pre-filled).
    await state.set_state(ConsultRemindStates.waiting_date)
    await message.answer(
        locale.CONSULT_REMIND_ASK_WHEN.format(label=draft.subject), reply_markup=_when_keyboard()
    )


@router.callback_query(F.data == callbacks.CONSULT_REMIND)
async def on_consult_remind(
    callback: CallbackQuery, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
    """🔔 Нагадати — infer the reminder from the conversation; ask only for what's missing."""
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    await _start_reminder(callback.message, state, scheduler=reminder_scheduler)


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
    booking: bool = False,
) -> None:
    """Persist + live-schedule the reminder, confirm, and resume the consultation. A ``booking``
    reminder gets the 'I can't book — call them' confirmation instead of the plain one."""
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
    template = locale.CONSULT_REMIND_SET_BOOKING if booking else locale.CONSULT_REMIND_SET
    await message.answer(template.format(label=label, when=run_at.date().isoformat()))
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
    """A future moment from a typed period ('через 2 місяці'), an ISO date ('2026-09-01'), or a
    Ukrainian date ('11 липня'), or None. A past date is rejected (a reminder is in the future).
    Date-only inputs default to 9:00 (a friendlier time than midnight)."""
    text = text.strip()
    relative = reminders.parse_relative_when(text, base=_now())
    if relative is not None:
        return relative
    try:
        parsed: date | None = date.fromisoformat(text)
    except ValueError:
        parsed = reminders.parse_ukrainian_date(text, today=_now().date())
    if parsed is None:
        return None
    when = datetime.combine(parsed, time(9, 0), tzinfo=ZoneInfo(get_settings().timezone))
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


# --- /memory + 🧠 Памʼять: grouped, per-analysis memory (master-detail) ----------
# The view is a list of CONVERSATION GROUPS (one per analysis we talked about, plus the general
# non-anchored chats). Tap a group to read it; each group can be forgotten on its own, or all at
# once. Navigation edits the message in place (no spam). rid 0 in callbacks == the general group.

_MEMORY_LINE_CAP = 200  # truncate a long remembered turn in the view (the full text stays stored)
_GROUP_LABEL_CAP = 38  # truncate a long report descriptor in a group button


def _clean(text: str) -> str:
    """A remembered turn for display: drop the appended disclaimer + the *bold*/_italic_ markers
    (which would otherwise show up as literal '*' / '_' in the read-back)."""
    body = text.split(DISCLAIMER)[0] if DISCLAIMER and DISCLAIMER in text else text
    return strip_markup(body).strip()


def _report_what(report: LabReport | None) -> str:
    """A short '<date> · <lab/type>' descriptor of the analysis a conversation is about."""
    if report is None:
        return ""
    date_txt = report.report_date.isoformat() if report.report_date else "?"
    lab = report.report_type or normalize_lab(report.lab) or ""
    return f"{date_txt} · {lab}" if lab else date_txt


async def _groups_payload(
    session: AsyncSession, user_id: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    """The conversation-groups list (header + one button per group + «забути все»), or the empty
    state when nothing is remembered yet."""
    total = await consult_memory.count(session, user_id=user_id)
    if not total:
        return locale.MEMORY_VIEW_EMPTY, None
    rows: list[list[InlineKeyboardButton]] = []
    for rid, n in await consult_memory.list_groups(session, user_id=user_id):
        if rid is None:
            rows.append([_btn(locale.MEMORY_GROUP_GENERAL.format(n=n), callbacks.memory_group(0))])
            continue
        report = await history.get_report(session, report_id=rid, user_id=user_id)
        what = _report_what(report)
        if what:
            if len(what) > _GROUP_LABEL_CAP:
                what = what[:_GROUP_LABEL_CAP].rstrip() + "…"
            label = locale.MEMORY_GROUP_REPORT.format(what=what, n=n)
        else:
            label = locale.MEMORY_GROUP_REPORT_DELETED.format(n=n)
        rows.append([_btn(label, callbacks.memory_group(rid))])
    rows.append([_btn(locale.MEMORY_BTN_FORGET_ALL, callbacks.MEMORY_FORGET)])
    text = "\n\n".join(
        (
            locale.MEMORY_VIEW_HEADER,
            locale.MEMORY_GROUPS_INTRO,
            locale.MEMORY_GROUPS_COUNT.format(total=total),
        )
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _render_turns(title: str, turns: list[ConsultMemory], total: int) -> str:
    """The read-back of ONE conversation's turns (cleaned, escaped HTML)."""
    lines = [title, "", locale.MEMORY_VIEW_COUNT.format(total=total)]
    if total > len(turns):
        lines.append(locale.MEMORY_VIEW_SHOWN.format(shown=len(turns)))
    lines.append("")
    for turn in turns:
        icon = locale.MEMORY_ROLE_USER if turn.role == "user" else locale.MEMORY_ROLE_BOT
        day = turn.created_at.date().isoformat() if turn.created_at else "?"
        body = _clean(turn.text)
        if len(body) > _MEMORY_LINE_CAP:
            body = body[:_MEMORY_LINE_CAP].rstrip() + "…"
        lines.append(f"{icon} <i>{day}</i> {html.escape(body)}")
    return "\n".join(lines)


def _group_keyboard(rid: int) -> InlineKeyboardMarkup:
    """Hub-entry conversation actions: forget just this one, or back to the GROUPS list."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(locale.MEMORY_BTN_FORGET_ONE, callbacks.memory_forget_one(rid)),
                _btn(locale.MEMORY_BTN_BACK, callbacks.MEMORY_HUB),
            ]
        ]
    )


def _card_memory_keyboard(report_id: int) -> InlineKeyboardMarkup:
    """Card-entry conversation actions: forget just this one, or back to THIS analysis's card
    (not the general memory) — so opening memory from a report stays in that report's context."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(locale.MEMORY_BTN_FORGET_ONE, callbacks.memory_forget_card(report_id)),
                _btn(locale.MEMORY_BTN_BACK, callbacks.history_open(report_id, 0)),
            ]
        ]
    )


def _back_to_card_keyboard(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(locale.MEMORY_BTN_BACK, callbacks.history_open(report_id, 0))]]
    )


async def _report_memory_text(
    session: AsyncSession, user_id: int, report_id: int | None
) -> str | None:
    """One conversation's rendered read-back (``report_id`` None == general), or ``None`` when it
    has no turns. Keyboard is the caller's job (it differs by entry: hub vs report card)."""
    turns = await consult_memory.recent_turns_for_report(
        session, user_id=user_id, report_id=report_id
    )
    total = await consult_memory.count_for_report(session, user_id=user_id, report_id=report_id)
    if not turns:
        return None
    if report_id is None:
        title = locale.MEMORY_REPORT_TITLE.format(what=locale.MEMORY_GROUP_GENERAL_TITLE)
    else:
        report = await history.get_report(session, report_id=report_id, user_id=user_id)
        title = locale.MEMORY_REPORT_TITLE.format(
            what=html.escape(_report_what(report) or "аналіз")
        )
    return _render_turns(title, turns, total)


async def open_memory_view(message: Message, telegram_id: int) -> None:
    """Show the conversation-groups list. Shared by /memory and the 🧠 Памʼять menu tap."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        text, keyboard = await _groups_payload(session, user.id)
    await answer_chunked(message, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def _edit_to_groups(callback: CallbackQuery) -> None:
    """Edit the current message back into the (refreshed) groups list."""
    tg = _telegram_id(callback)
    if tg is None or not isinstance(callback.message, Message):
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        text, keyboard = await _groups_payload(session, user.id)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@router.message(Command("memory"))
async def on_memory(message: Message) -> None:
    """Перегляд памʼяті — the grouped memory view (per analysis), each forgettable on its own."""
    tg = _telegram_id(message)
    if tg is not None:
        await open_memory_view(message, tg)


@router.callback_query(F.data == callbacks.MEMORY_HUB)
async def on_memory_hub(callback: CallbackQuery) -> None:
    await _edit_to_groups(callback)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.MEMORY_GROUP + ":"))
async def on_memory_group(callback: CallbackQuery) -> None:
    """Open ONE conversation group from the general hub (edit-in-place); back returns to the hub."""
    rid = callbacks.parse_memory_group(callback.data or "")
    tg = _telegram_id(callback)
    if rid is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        text = await _report_memory_text(session, user.id, None if rid == 0 else rid)
    if text is None:  # the conversation was cleared meanwhile — show the fresh list
        await _edit_to_groups(callback)
        await callback.answer()
        return
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=_group_keyboard(rid)
        )
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.MEMORY_OPEN_REPORT + ":"))
async def on_memory_open_report(callback: CallbackQuery) -> None:
    """💭 Памʼять on a /history card: edit the card in place into THIS analysis's conversation, with
    «◀ Назад» returning to the card (not the general memory) — like 🔬 Розбір / 📊 Показники do."""
    report_id = callbacks.parse_memory_open_report(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        text = await _report_memory_text(session, user.id, report_id)
    with contextlib.suppress(TelegramBadRequest):
        if text is None:  # no conversation about this analysis yet
            await callback.message.edit_text(
                locale.MEMORY_REPORT_EMPTY, reply_markup=_back_to_card_keyboard(report_id)
            )
        else:
            await callback.message.edit_text(
                text, parse_mode=ParseMode.HTML, reply_markup=_card_memory_keyboard(report_id)
            )


@router.callback_query(F.data.startswith(callbacks.MEMORY_FORGET_CARD + ":"))
async def on_memory_forget_card(callback: CallbackQuery) -> None:
    """«Забути цю розмову» from a card — step 1 (confirm); cancel re-opens the analysis's memory."""
    report_id = callbacks.parse_memory_forget_card(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        total = await consult_memory.count_for_report(session, user_id=user.id, report_id=report_id)
    if not total:
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(
                locale.MEMORY_REPORT_EMPTY, reply_markup=_back_to_card_keyboard(report_id)
            )
        await callback.answer()
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(locale.MEMORY_BTN_FORGET_ONE_YES, callbacks.memory_forget_card_ok(report_id)),
                _btn(locale.MEMORY_BTN_FORGET_NO, callbacks.memory_open_report(report_id)),
            ]
        ]
    )
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(
            locale.MEMORY_FORGET_ONE_CONFIRM.format(total=total),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.MEMORY_FORGET_CARD_OK + ":"))
async def on_memory_forget_card_ok(callback: CallbackQuery) -> None:
    """«Забути цю розмову» from a card — step 2; then offer to go back to the analysis's card."""
    report_id = callbacks.parse_memory_forget_card_ok(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        deleted = await consult_memory.clear_report(session, user_id=user.id, report_id=report_id)
        await session.commit()
    await callback.answer(
        locale.MEMORY_FORGET_ONE_DONE.format(total=deleted)
        if deleted
        else locale.MEMORY_FORGET_EMPTY
    )
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(
            locale.MEMORY_REPORT_EMPTY, reply_markup=_back_to_card_keyboard(report_id)
        )


@router.callback_query(F.data == callbacks.MEMORY_FORGET)
async def on_memory_forget(callback: CallbackQuery) -> None:
    """«Забути все» step 1 — confirm before wiping every conversation."""
    tg = _telegram_id(callback)
    if tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        total = await consult_memory.count(session, user_id=user.id)
    if not total:
        await _edit_to_groups(callback)
        await callback.answer()
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(locale.MEMORY_BTN_FORGET_YES, callbacks.MEMORY_FORGET_OK),
                _btn(locale.MEMORY_BTN_FORGET_NO, callbacks.MEMORY_FORGET_NO),
            ]
        ]
    )
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(
            locale.MEMORY_FORGET_CONFIRM.format(total=total),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    await callback.answer()


@router.callback_query(F.data == callbacks.MEMORY_FORGET_OK)
async def on_memory_forget_ok(callback: CallbackQuery) -> None:
    """«Забути все» step 2 — wipe everything, then show the (now empty) list."""
    tg = _telegram_id(callback)
    if tg is None:
        await callback.answer()
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        deleted = await consult_memory.clear_all(session, user_id=user.id)
        await session.commit()
    await callback.answer(
        locale.MEMORY_FORGET_DONE.format(total=deleted) if deleted else locale.MEMORY_FORGET_EMPTY
    )
    await _edit_to_groups(callback)


@router.callback_query(F.data == callbacks.MEMORY_FORGET_NO)
async def on_memory_forget_no(callback: CallbackQuery) -> None:
    await _edit_to_groups(callback)  # cancelled — back to the list, nothing deleted
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.MEMORY_FORGET_ONE + ":"))
async def on_memory_forget_one(callback: CallbackQuery) -> None:
    """«Забути цю розмову» step 1 — confirm forgetting just one conversation."""
    rid = callbacks.parse_memory_forget_one(callback.data or "")
    tg = _telegram_id(callback)
    if rid is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    report_id = None if rid == 0 else rid
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        total = await consult_memory.count_for_report(session, user_id=user.id, report_id=report_id)
    if not total:
        await _edit_to_groups(callback)
        await callback.answer()
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(locale.MEMORY_BTN_FORGET_ONE_YES, callbacks.memory_forget_one_ok(rid)),
                _btn(locale.MEMORY_BTN_FORGET_NO, callbacks.memory_group(rid)),
            ]
        ]
    )
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(
            locale.MEMORY_FORGET_ONE_CONFIRM.format(total=total),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.MEMORY_FORGET_ONE_OK + ":"))
async def on_memory_forget_one_ok(callback: CallbackQuery) -> None:
    """«Забути цю розмову» step 2 — forget just that conversation, then show the list."""
    rid = callbacks.parse_memory_forget_one_ok(callback.data or "")
    tg = _telegram_id(callback)
    if rid is None or tg is None:
        await callback.answer()
        return
    report_id = None if rid == 0 else rid
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        deleted = await consult_memory.clear_report(session, user_id=user.id, report_id=report_id)
        await session.commit()
    await callback.answer(
        locale.MEMORY_FORGET_ONE_DONE.format(total=deleted)
        if deleted
        else locale.MEMORY_FORGET_EMPTY
    )
    await _edit_to_groups(callback)
