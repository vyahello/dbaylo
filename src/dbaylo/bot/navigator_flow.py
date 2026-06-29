"""Navigator bot flow: /price (named drug) and /coverage (ПМГ for a service).

Thin handlers over :mod:`dbaylo.navigator.pipeline`. The command *argument* — and,
Tier 1.3, the **FSM answer** typed after a menu/`/price` prompt — is user text and is
screened by the safety gate inside the pipeline (`run_price`/`run_coverage` call
``gate.screen`` first). A command is not a trusted bypass, and neither is being in the
navigator state: "/coverage болить нирка що робити", or the same typed into the drug
field, short-circuits to triage instead of a price search.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.bot.formatting import answer_chunked, render_companion_html
from dbaylo.bot.keyboards import cancel_keyboard
from dbaylo.bot.typing import keep_typing
from dbaylo.companion import callbacks, cities, medications
from dbaylo.db import get_session
from dbaylo.db.models import Medication
from dbaylo.labs.intake import ensure_user, get_city, set_city
from dbaylo.navigator import priceintent
from dbaylo.navigator.pipeline import (
    find_coverage,
    find_otc_prices,
    find_prices_freeform,
    find_prices_web,
    run_price,
)
from dbaylo.safety import screen

router = Router(name="navigator")


class NavStates(StatesGroup):
    waiting_drug = State()
    waiting_service = State()
    waiting_city = State()


def _short(name: str, limit: int = 30) -> str:
    name = name.strip()
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def _telegram_id(event: Message | CallbackQuery) -> int | None:
    return event.from_user.id if event.from_user else None


# A drug's bare STRENGTH ("40 мг") pulled from the stored dose, for the button label + the agent
# query — so the price search targets the doctor's exact dosage when it was recorded.
_STRENGTH_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:мг|мкг|мл|г)\b", re.IGNORECASE)


def _strength(dose: str | None) -> str | None:
    if not dose:
        return None
    match = _STRENGTH_RE.search(dose)
    return match.group(0).strip() if match else None


# --- Price ----------------------------------------------------------------------


async def start_price_dialog(message: Message, state: FSMContext) -> None:
    """Enter the type-a-drug price dialog (the ✏️ fallback) — always cancellable."""
    await state.set_state(NavStates.waiting_drug)
    await message.answer(locale.NAV_ASK_DRUG, reply_markup=cancel_keyboard())


# Per-prescription marks for the price-options buttons: ① for the 1st course, ② for the 2nd, …
# (🗂 past ten), so it is clear which рецепт each med belongs to. A standalone med is marked 💊.
_COURSE_MARKS = "①②③④⑤⑥⑦⑧⑨⑩"
_STANDALONE_MARK = "💊"


async def _unique_meds(session: AsyncSession, *, user_id: int) -> list[Medication]:
    """The user's medications, de-duplicated by name and GROUPED BY COURSE (prescription) — courses
    in first-seen order, standalone meds last. The price screen + the on-tap re-derivation share
    this order, so a button's index addresses the same med."""
    seen: set[str] = set()
    out: list[Medication] = []
    for med in await medications.list_medications(session, user_id=user_id):
        key = (med.name or "").strip().casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(med)
    course_order: dict[str, int] = {}
    for med in out:
        if med.course and med.course not in course_order:
            course_order[med.course] = len(course_order)
    # Stable sort: course meds first (by first-seen course), standalone meds after.
    return sorted(
        out,
        key=lambda m: (
            course_order.get(m.course, len(course_order)) if m.course else len(course_order) + 1
        ),
    )


def _course_mark(number: int) -> str:
    """The display mark for the N-th prescription (1-based): ①..⑩, then 🗂."""
    return _COURSE_MARKS[number - 1] if 1 <= number <= len(_COURSE_MARKS) else "🗂"


async def open_price_options(message: Message, state: FSMContext, *, telegram_id: int) -> None:
    """The agent's price screen: propose the user's OWN meds (one-tap price, dosage shown) + ✏️ to
    type another + 📋 manage them (in 💊 Мої ліки) + 📍 set/change the city. Falls back to the type
    dialog when there are no meds yet."""
    await state.clear()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        meds = await _unique_meds(session, user_id=user.id)
        city = (user.city or "").strip() or None
    if not meds:
        await start_price_dialog(message, state)
        return
    # Number each prescription so a med button shows which рецепт it is from (①②… / 💊 standalone).
    course_num: dict[str, int] = {}
    for med in meds:
        if med.course and med.course not in course_num:
            course_num[med.course] = len(course_num) + 1
    rows: list[list[InlineKeyboardButton]] = []
    for index, med in enumerate(meds):
        mark = _course_mark(course_num[med.course]) if med.course else _STANDALONE_MARK
        strength = _strength(med.dose)
        label = (
            locale.BTN_PRICE_MED_DOSE.format(mark=mark, name=_short(med.name, 20), dose=strength)
            if strength
            else locale.BTN_PRICE_MED.format(mark=mark, name=_short(med.name, 24))
        )
        rows.append([InlineKeyboardButton(text=label, callback_data=callbacks.price_med(index))])
    rows.append(
        [
            InlineKeyboardButton(text=locale.BTN_PRICE_TYPE, callback_data=callbacks.PRICE_TYPE),
            InlineKeyboardButton(
                text=locale.BTN_PRICE_MANAGE, callback_data=callbacks.MENU_MED_LIST
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=locale.BTN_PRICE_CHANGE_CITY, callback_data=callbacks.PRICE_CHANGE_CITY
            )
        ]
    )
    city_line = (
        locale.NAV_PRICE_CITY_LINE.format(city=city) if city else locale.NAV_PRICE_NO_CITY_LINE
    )
    parts = [locale.NAV_PRICE_OPTIONS, locale.NAV_PRICE_MEDS_NOTE, city_line]
    if course_num:  # the legend: which рецепт each ①②… mark stands for
        parts.append("")
        parts.append(locale.NAV_PRICE_LEGEND_HEADER)
        parts += [
            locale.NAV_PRICE_COURSE_LEGEND.format(mark=_course_mark(num), course=course)
            for course, num in course_num.items()
        ]
    await message.answer("\n".join(parts), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _send_price(
    message: Message, drug: str, *, telegram_id: int | None, dose: str | None = None
) -> None:
    """Gate FIRST (a symptom short-circuits to triage — no "searching" message, no city fetch), then
    run the smart web-search price lookup (real prices + links) and send it as HTML."""
    decision = screen(drug)
    if decision.short_circuited:  # same gate as the pipeline — surfaced before any search chrome
        await message.answer(decision.message)
        return
    city = await _city_for(telegram_id)
    await message.answer(locale.NAV_PRICE_SEARCHING)
    async with keep_typing(message):
        result = await run_price(drug, use_web_agent=True, city=city, dose=dose)
    await answer_chunked(message, render_companion_html(result.text), parse_mode=ParseMode.HTML)


async def _send_meds_prices(
    message: Message, items: list[tuple[str, str | None]], *, city: str | None
) -> None:
    """Price a whole prescription/course (a list of named meds + doses) via the web-search agent."""
    if not items:
        return
    await message.answer(locale.NAV_PRICE_SEARCHING)
    async with keep_typing(message):
        text = await find_prices_web(items, city=city)
    await answer_chunked(message, render_companion_html(text), parse_mode=ParseMode.HTML)


# A price conversation kept in FSM data (like the companion chat thread): so a follow-up ("а
# дешевше?", "а в Києві?") is answered remembering the drug + city, a real back-and-forth. Wiped on
# any /command or menu tap (the reset middleware clears FSM data); a gap past the TTL starts fresh.
_PRICE_TTL = timedelta(minutes=30)
_PRICE_MAX_TURNS = 4  # keep the last N user+assistant pairs
_PRICE_ASSISTANT_CAP = 1500  # trim a stored answer so FSM data stays small


def _now() -> datetime:
    return datetime.now(UTC)


def price_thread_fresh(data: dict[str, object]) -> bool:
    """True when a recent (within the TTL) price conversation is stored in ``data`` — so a short
    follow-up should continue it instead of falling to general chat."""
    raw = data.get("price_ts")
    if not raw or not data.get("price_transcript"):
        return False
    try:
        ts = datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    return ts.tzinfo is not None and _now() - ts < _PRICE_TTL


async def maybe_handle_price(
    message: Message, state: FSMContext, text: str, *, telegram_id: int | None
) -> bool:
    """If ``text`` is a price request (or a follow-up to a fresh price thread), answer it via the
    web-search agent — remembering the prior turns — and return ``True``. Else return ``False`` so
    the caller continues its own routing. The gate already cleared ``text`` upstream."""
    data = await state.get_data()
    fresh = price_thread_fresh(data)
    new_request = priceintent.is_price_request(text)
    if not (new_request or (fresh and priceintent.is_price_followup(text))):
        return False
    # An explicit new request starts a fresh thread; a bare follow-up continues the existing one.
    continuing = fresh and not new_request
    history: list[tuple[str, str]] = (
        [(t["role"], t["text"]) for t in data.get("price_transcript") or []] if continuing else []
    )
    city = (
        cities.parse_city(text)
        or (str(data.get("price_city")) if continuing and data.get("price_city") else None)
        or await _city_for(telegram_id)
    )
    await message.answer(locale.NAV_PRICE_SEARCHING)
    async with keep_typing(message):
        answer = await find_prices_freeform(text, city=city, history=history)
    await answer_chunked(message, render_companion_html(answer), parse_mode=ParseMode.HTML)
    updated = [*history, ("user", text), ("assistant", answer[:_PRICE_ASSISTANT_CAP])]
    await state.update_data(
        price_transcript=[{"role": r, "text": b} for r, b in updated[-2 * _PRICE_MAX_TURNS :]],
        price_city=city,
        price_ts=_now().isoformat(),
    )
    return True


@router.callback_query(F.data == callbacks.CHAT_OTC)
async def on_chat_otc(callback: CallbackQuery, state: FSMContext) -> None:
    """💊 (owner-authorized OTC path) — for the stored minor complaint, name безрецептурні options +
    prices, with an interaction caution vs the user's Rx meds. Only shown by the intake at triage
    MONITOR; the agent re-screens, so a red flag still escalates."""
    await callback.answer()
    tg = _telegram_id(callback)
    if tg is None or not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    complaint = str(data.get("otc_complaint") or "").strip()
    if not complaint:
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        meds_list = await medications.list_medications(session, user_id=user.id)
        city = (user.city or "").strip() or None
    seen: set[str] = set()
    meds_names: list[str] = []
    for med in meds_list:
        key = medications.normalize_name(med.name)
        if key and key not in seen:
            seen.add(key)
            meds_names.append(medications.clean_drug_name(med.name))
    await callback.message.answer(locale.OTC_SEARCHING)
    async with keep_typing(callback.message):
        text = await find_otc_prices(complaint, city=city, meds=", ".join(meds_names))
    await answer_chunked(callback.message, render_companion_html(text), parse_mode=ParseMode.HTML)


async def maybe_handle_coverage(message: Message, text: str, *, telegram_id: int | None) -> bool:
    """If ``text`` asks what may be FREE under ПМГ / НСЗУ / «Доступні ліки» ("чи безкоштовне УЗД?",
    "де безплатно здати аналізи?"), answer via the coverage agent and return ``True``. Checked
    BEFORE the price intent (more specific). The gate already cleared ``text`` upstream."""
    if not priceintent.is_coverage_request(text):
        return False
    await _send_coverage(message, text, telegram_id=telegram_id)
    return True


@router.message(Command("price"))
async def cmd_price(message: Message, command: CommandObject, state: FSMContext) -> None:
    arg = (command.args or "").strip()
    tg = _telegram_id(message)
    if not arg:
        if tg is not None:
            await open_price_options(message, state, telegram_id=tg)
        return
    await _send_price(message, arg, telegram_id=tg)  # gated inside _send_price


@router.message(NavStates.waiting_drug, F.text)
async def on_price_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer(locale.NOTHING_SAVED)
        return
    # SAME gate as the command arg — a symptom typed into the drug field short-circuits to triage.
    await _send_price(message, text, telegram_id=_telegram_id(message))


@router.callback_query(F.data == callbacks.PRICE_TYPE)
async def on_price_type(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await start_price_dialog(callback.message, state)


@router.callback_query(F.data.startswith(callbacks.PRICE_MED + ":"))
async def on_price_med(callback: CallbackQuery, state: FSMContext) -> None:
    """One-tap price for a proposed medication (re-derived by index on tap; dosage folded in)."""
    # Ack first: the lookup is a multi-second web search.
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
        med = meds[index]
        drug = medications.clean_drug_name(med.name)  # strip "К."/"Т." before searching
        await _send_price(callback.message, drug, telegram_id=tg, dose=_strength(med.dose))


# --- City (asked once, remembered on User.city; reused by price + clinic search) -------


async def _city_for(telegram_id: int | None) -> str | None:
    """The user's saved city (or ``None``) — folded into the price search for local results."""
    if telegram_id is None:
        return None
    async with get_session() as session:
        return await get_city(session, telegram_id=telegram_id)


@router.callback_query(F.data == callbacks.PRICE_CHANGE_CITY)
async def on_price_change_city(callback: CallbackQuery, state: FSMContext) -> None:
    """📍 Set / change the saved city used for price (and clinic) search."""
    await callback.answer()
    if isinstance(callback.message, Message):
        await state.set_state(NavStates.waiting_city)
        await callback.message.answer(locale.NAV_ASK_CITY, reply_markup=cancel_keyboard())


@router.message(NavStates.waiting_city, F.text & ~F.text.startswith("/"))
async def on_city_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    raw = (message.text or "").strip()
    tg = _telegram_id(message)
    if not raw or tg is None:
        await message.answer(locale.NOTHING_SAVED)
        return
    city = cities.parse_city(raw) or raw  # canonical if known, else the typed town as-is
    async with get_session() as session:
        await set_city(session, telegram_id=tg, city=city)
        await session.commit()
    await message.answer(locale.NAV_CITY_SAVED.format(city=city))
    await open_price_options(message, state, telegram_id=tg)


# --- Prescription ↔ price: price a saved course / single med (the 💰 buttons) ----------


def _price_items(meds: list[Medication]) -> list[tuple[str, str | None]]:
    """(name, strength?) pairs for the price agent — de-duplicated by normalized drug name, with the
    leading form marker ("К."/"Т.") stripped so the agent searches the plain product."""
    items: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for med in meds:
        key = medications.normalize_name(med.name)
        if key and key not in seen:
            seen.add(key)
            items.append((medications.clean_drug_name(med.name), _strength(med.dose)))
    return items


@router.callback_query(F.data.startswith(callbacks.COURSE_PRICES + ":"))
async def on_course_prices(callback: CallbackQuery) -> None:
    """💰 Price a whole prescription (course) — or the single med, when it has no course."""
    await callback.answer()
    parsed = callbacks.parse_course_prices(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        return
    rep_med_id, _origin = parsed
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        rep = await session.get(Medication, rep_med_id)
        if rep is None or rep.user_id != user.id:
            return
        meds = (
            await medications.list_by_course(session, user_id=user.id, course=rep.course)
            if rep.course
            else [rep]
        )
        city = (user.city or "").strip() or None
    await _send_meds_prices(callback.message, _price_items(meds), city=city)


@router.callback_query(F.data.startswith(callbacks.MEDICATION_PRICE + ":"))
async def on_medication_price(callback: CallbackQuery) -> None:
    """💰 Price a single saved medication (from its card)."""
    await callback.answer()
    parsed = callbacks.parse_medication_price(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        return
    medication_id, _origin = parsed
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        med = await session.get(Medication, medication_id)
        if med is None or med.user_id != user.id:
            return
        name, dose = medications.clean_drug_name(med.name), _strength(med.dose)
    await _send_price(callback.message, name, telegram_id=tg, dose=dose)


# --- Coverage -------------------------------------------------------------------


async def start_coverage_dialog(message: Message, state: FSMContext) -> None:
    """Enter the type-a-service coverage dialog — always cancellable."""
    await state.set_state(NavStates.waiting_service)
    await message.answer(locale.NAV_ASK_SERVICE, reply_markup=cancel_keyboard())


async def open_coverage_screen(message: Message, state: FSMContext, *, telegram_id: int) -> None:
    """🆓 Безкоштовно (ПМГ) — explain the value (what may be FREE from the state), then offer to
    check a SERVICE or the user's MEDS against «Доступні ліки». The owner found НСЗУ opaque; this
    screen states why it matters and turns it into one tap."""
    await state.clear()
    rows = [
        [
            InlineKeyboardButton(
                text=locale.BTN_COVERAGE_SERVICE, callback_data=callbacks.COVERAGE_SERVICE
            )
        ],
        [
            InlineKeyboardButton(
                text=locale.BTN_COVERAGE_MEDS, callback_data=callbacks.COVERAGE_MEDS
            )
        ],
    ]
    await message.answer(
        locale.NAV_COVERAGE_EXPLAINER,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode=ParseMode.HTML,
    )


def _coverage_result_keyboard(*, is_meds: bool) -> InlineKeyboardMarkup:
    """Contextual actions UNDER a coverage answer so it is never a dead-end (all reuse existing
    entry points). The meds answer offers to price the same meds; the service answer offers the
    meds check; both offer a quick re-check of another service."""
    if is_meds:
        row = [
            InlineKeyboardButton(
                text=locale.BTN_COVERAGE_TO_PRICES, callback_data=callbacks.MENU_PRICE
            ),
            InlineKeyboardButton(
                text=locale.BTN_COVERAGE_OTHER_SERVICE, callback_data=callbacks.COVERAGE_SERVICE
            ),
        ]
    else:
        row = [
            InlineKeyboardButton(
                text=locale.BTN_COVERAGE_CHECK_MEDS, callback_data=callbacks.COVERAGE_MEDS
            ),
            InlineKeyboardButton(
                text=locale.BTN_COVERAGE_OTHER_SERVICE, callback_data=callbacks.COVERAGE_SERVICE
            ),
        ]
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def _send_coverage(message: Message, request: str, *, telegram_id: int | None) -> None:
    """Gate FIRST, then the smart ПМГ/НСЗУ agent (city-grounded), rendered as HTML."""
    decision = screen(request)
    if decision.short_circuited:
        await message.answer(decision.message)
        return
    city = await _city_for(telegram_id)
    await message.answer(locale.NAV_COVERAGE_SEARCHING)
    async with keep_typing(message):
        text = await find_coverage(request, city=city)
    await answer_chunked(
        message,
        render_companion_html(text),
        parse_mode=ParseMode.HTML,
        reply_markup=_coverage_result_keyboard(is_meds=False),
    )


@router.callback_query(F.data == callbacks.COVERAGE_SERVICE)
async def on_coverage_service(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await start_coverage_dialog(callback.message, state)


@router.callback_query(F.data == callbacks.COVERAGE_MEDS)
async def on_coverage_meds(callback: CallbackQuery, state: FSMContext) -> None:
    """💊 Check the user's OWN meds against «Доступні ліки» (free / discounted reimbursement)."""
    await callback.answer()
    tg = _telegram_id(callback)
    if tg is None or not isinstance(callback.message, Message):
        return
    await state.clear()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        meds = await _unique_meds(session, user_id=user.id)
        city = (user.city or "").strip() or None
    if not meds:
        await callback.message.answer(locale.NAV_COVERAGE_NO_MEDS)
        return
    names = "\n".join(f"- {medications.clean_drug_name(m.name)}" for m in meds)
    await callback.message.answer(locale.NAV_COVERAGE_SEARCHING)
    async with keep_typing(callback.message):
        text = await find_coverage(names, city=city, is_meds=True)
    await answer_chunked(
        callback.message,
        render_companion_html(text),
        parse_mode=ParseMode.HTML,
        reply_markup=_coverage_result_keyboard(is_meds=True),
    )


@router.message(Command("coverage"))
async def cmd_coverage(message: Message, command: CommandObject, state: FSMContext) -> None:
    arg = (command.args or "").strip()
    tg = _telegram_id(message)
    if not arg:
        if tg is not None:
            await open_coverage_screen(message, state, telegram_id=tg)
        return
    await _send_coverage(message, arg, telegram_id=tg)


@router.message(NavStates.waiting_service, F.text)
async def on_coverage_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer(locale.NOTHING_SAVED)
        return
    await _send_coverage(message, text, telegram_id=_telegram_id(message))
