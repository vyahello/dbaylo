"""Lab intake + OCR-confirmation flow (aiogram 3 FSM).

The interactive half of L2: receive a photo/PDF, extract, show the values for
confirmation in Ukrainian, allow corrections (including report date and lab —
a misread date silently corrupts the time series), and persist only on confirm.

The formatting and edit-target parsing are pure functions (unit-tested); the
handlers stay thin. Pending values live in FSM state, never in the DB until the
user confirms (rail #2). FSM state is persisted (``bot.storage.SQLiteStorage``), so a
confirmation in progress survives a restart.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from dbaylo import locale
from dbaylo.bot.formatting import answer_chunked, render_interpretation_html
from dbaylo.bot.keyboards import cancel_keyboard, clear_inline_keyboard
from dbaylo.companion import history, proactive, reminders
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.config import get_settings
from dbaylo.db import get_session
from dbaylo.db.models import LabReport, LabResult, ReportStatus, ResultFlag
from dbaylo.labs.extraction import ExtractionFailed, extract_document
from dbaylo.labs.intake import (
    create_pending_report,
    ensure_user,
    is_supported,
    persist_confirmed,
    save_original_file,
)
from dbaylo.labs.pipeline import compute_report_summary, render_report_charts
from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport
from dbaylo.labs.trends import compute_flag, is_out_of_range, normalize_analyte

router = Router(name="labs")

_CB_CONFIRM = "lab:confirm"
_CB_EDIT = "lab:edit"
_CB_CANCEL = "lab:cancel"

# Post-confirm offers carry the report_id in the callback data, so the buttons work
# even when the FSM state is gone — a restart (MemoryStorage is in-memory) or a Tier 1.3
# menu-label tap (which resets state) would otherwise leave a state-gated button silently
# dead. Everything an offer needs (lab name, out-of-range analytes) is recomputed from the
# saved report. Only the custom-interval sub-step still needs state, and it is set on tap.
_CB_REPEAT_DAYS = {"lab:rep:1m": 30, "lab:rep:3m": 90, "lab:rep:6m": 180}
_CB_REPEAT_OTHER = "lab:rep:oth"
_CB_REPEAT_NO = "lab:rep:no"
_CB_CONCERN_YES = "lab:con:y"
_CB_CONCERN_NO = "lab:con:n"
_CB_CHARTS = "lab:chart"


def _rid_cb(prefix: str, report_id: int) -> str:
    return f"{prefix}:{report_id}"


def _parse_rid(prefix: str, data: str | None) -> int | None:
    if data and data.startswith(prefix + ":"):
        tail = data[len(prefix) + 1 :]
        return int(tail) if tail.isdigit() else None
    return None


class LabStates(StatesGroup):
    confirming = State()
    edit_pick = State()
    edit_value = State()
    edit_date = State()
    edit_lab = State()
    repeat_custom = State()


# --- Pure helpers (unit-tested) -------------------------------------------------

# Sentinel so the first section (even a `None` one) is detected as a change.
_NO_SECTION: object = object()


def _confirm_emoji(a: ExtractedAnalyte) -> str:
    """⚠️ if the lab flags the row (or it's out of range), ✅ if it has a value, else ❔."""
    if is_out_of_range(a.value, a.ref_low, a.ref_high, a.out_of_range):
        return locale.FLAG_ATTENTION
    if a.value is not None or a.value_text:
        return locale.FLAG_EMOJI["normal"]
    return locale.FLAG_EMOJI["unknown"]


def render_confirmation_text(report: ExtractedReport) -> str:
    """Build the Ukrainian confirmation view for an extracted report (table or narrative)."""
    date_txt = report.report_date.isoformat() if report.report_date else locale.LAB_DATE_UNKNOWN
    lab_txt = report.lab or locale.LAB_LAB_UNKNOWN
    if report.is_narrative:
        lines = [
            f"{locale.LAB_TYPE_LABEL}: {report.report_type or locale.LAB_DOC_GENERIC}",
            f"{locale.LAB_DATE_LABEL}: {date_txt}",
            f"{locale.LAB_LAB_LABEL}: {lab_txt}",
            "",
        ]
        if report.narrative:
            lines += [report.narrative, ""]
        if report.conclusion:
            lines += [f"{locale.LAB_CONCLUSION_LABEL}: {report.conclusion}", ""]
        lines.append(locale.LAB_CONFIRM_PROMPT)
        return "\n".join(lines)
    lines = [
        f"{locale.LAB_DATE_LABEL}: {date_txt}",
        f"{locale.LAB_LAB_LABEL}: {lab_txt}",
    ]
    if report.conclusion:
        lines.append(f"{locale.LAB_CONCLUSION_LABEL}: {report.conclusion}")
    lines.append("")
    # Group rows under their panel header (blood vs urine stay apart); the row number is still
    # global and contiguous, so editing by number is unaffected.
    prev_section: object = _NO_SECTION
    for i, a in enumerate(report.results, 1):
        if a.section != prev_section:
            prev_section = a.section
            if a.section:
                if lines[-1] != "":
                    lines.append("")
                lines.append(locale.LAB_SECTION_HEADER.format(section=a.section))
        ref = a.display_reference()
        line = (
            f"{i}. {a.analyte} — {a.display_value()} "
            f"({locale.LAB_NORM_LABEL} {ref}) {_confirm_emoji(a)}"
        )
        lines.append(line.rstrip())
    lines += ["", locale.LAB_CONFIRM_PROMPT]
    return "\n".join(lines)


def confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=locale.BTN_CONFIRM_ALL, callback_data=_CB_CONFIRM)],
            [
                InlineKeyboardButton(text=locale.BTN_EDIT, callback_data=_CB_EDIT),
                InlineKeyboardButton(text=locale.BTN_CANCEL, callback_data=_CB_CANCEL),
            ],
        ]
    )


def parse_edit_target(text: str, n_rows: int) -> str | int | None:
    """Map an edit instruction to 'date', 'lab', a 1-based row index, or None."""
    token = text.strip().casefold()
    if token in ("дата", "date"):
        return "date"
    if token in ("лабораторія", "лаб", "lab"):
        return "lab"
    if token.isdigit():
        index = int(token)
        if 1 <= index <= n_rows:
            return index
    return None


def parse_value(text: str) -> float | None:
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return None


# --- FSM (de)serialization for pending data -------------------------------------


def _report_to_state(report: ExtractedReport) -> dict[str, object]:
    return {
        "report_date": report.report_date.isoformat() if report.report_date else None,
        "lab": report.lab,
        "results": [vars(a) for a in report.results],
    }


def _report_from_state(data: Mapping[str, Any]) -> ExtractedReport:
    raw_date = data.get("report_date")
    raw_results = cast("list[dict[str, Any]]", data.get("results") or [])
    raw_lab = data.get("lab")
    return ExtractedReport(
        results=[ExtractedAnalyte(**row) for row in raw_results],
        report_date=date.fromisoformat(raw_date) if isinstance(raw_date, str) else None,
        lab=raw_lab if isinstance(raw_lab, str) else None,
    )


def _pending_report(data: Mapping[str, Any]) -> ExtractedReport:
    """Re-hydrate the pending report stored in FSM state."""
    return _report_from_state(cast("Mapping[str, Any]", data["report"]))


# --- Handlers -------------------------------------------------------------------


@router.message(F.document)
async def on_document(message: Message, state: FSMContext) -> None:
    document = message.document
    if document is None or message.from_user is None or message.bot is None:
        return
    suffix = Path(document.file_name or "").suffix or _suffix_from_mime(document.mime_type)
    if not is_supported(suffix):
        await message.answer(locale.LAB_UNSUPPORTED_FILE)
        return
    await _handle_upload(message, state, file_id=document.file_id, suffix=suffix)


@router.message(F.photo)
async def on_photo(message: Message, state: FSMContext) -> None:
    if not message.photo or message.from_user is None or message.bot is None:
        return
    await _handle_upload(message, state, file_id=message.photo[-1].file_id, suffix=".jpg")


async def _handle_upload(message: Message, state: FSMContext, *, file_id: str, suffix: str) -> None:
    assert message.from_user is not None and message.bot is not None
    await message.answer(locale.LAB_RECEIVED)

    buffer = BytesIO()
    await message.bot.download(file_id, destination=buffer)

    async with get_session() as session:
        user = await ensure_user(session, message.from_user.id, message.from_user.full_name)
        path = save_original_file(buffer.getvalue(), user_id=user.id, suffix=suffix)
        report = await create_pending_report(session, user=user, file_path=path)
        report_id = report.id

    # Hard ceiling so an upload can never hang the way it once did (a stuck `claude`
    # subprocess). `extract_document` pages a multi-page PDF (each page bounded by the
    # per-page timeout, run a few at a time) or does a single pass; this generous outer
    # budget is the final net. Any timeout / unexpected error becomes a clean "couldn't read".
    budget = 4 * get_settings().claude_extract_timeout_s + 60
    try:
        outcome = await asyncio.wait_for(extract_document(str(path)), timeout=budget)
    except Exception:  # noqa: BLE001 — never leave the user hanging on a bad upload
        outcome = ExtractionFailed("extraction timed out or errored")
    if isinstance(outcome, ExtractionFailed):
        async with get_session() as session:
            pending = await session.get(LabReport, report_id)
            if pending is not None:
                pending.status = ReportStatus.DISCARDED
        await message.answer(locale.LAB_EXTRACTION_FAILED)
        await state.clear()
        return

    async with get_session() as session:
        pending = await session.get(LabReport, report_id)
        if pending is not None:
            pending.raw_ocr = json.dumps(_report_to_state(outcome), ensure_ascii=False)

    await state.set_state(LabStates.confirming)
    await state.update_data(report_id=report_id, report=_report_to_state(outcome))
    # A big report (e.g. ~85 rows) overflows Telegram's 4096-char cap, so send in chunks —
    # the confirm/edit buttons ride the last one. (A single message would raise and the user
    # would see nothing after the long extraction.)
    await answer_chunked(
        message, render_confirmation_text(outcome), reply_markup=confirmation_keyboard()
    )


@router.callback_query(F.data == _CB_CANCEL)
async def on_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_inline_keyboard(callback)  # consume the confirm/edit/cancel buttons
    data = await state.get_data()
    report_id = data.get("report_id")
    if isinstance(report_id, int):
        async with get_session() as session:
            report = await session.get(LabReport, report_id)
            if report is not None:
                report.status = ReportStatus.DISCARDED
    await state.clear()
    await callback.message.answer(locale.LAB_CANCELLED) if callback.message else None
    await callback.answer()


@router.callback_query(F.data == _CB_EDIT)
async def on_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_inline_keyboard(callback)  # leaving edit mode — the old confirm buttons go
    data = await state.get_data()
    report = _pending_report(data)
    await state.set_state(LabStates.edit_pick)
    if callback.message:
        await callback.message.answer(locale.LAB_EDIT_PICK.format(n=len(report.results)))
    await callback.answer()


@router.message(LabStates.edit_pick)
async def on_edit_pick(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    report = _pending_report(data)
    target = parse_edit_target(message.text or "", len(report.results))
    if target == "date":
        await state.set_state(LabStates.edit_date)
        await message.answer(locale.LAB_EDIT_NEW_DATE)
    elif target == "lab":
        await state.set_state(LabStates.edit_lab)
        await message.answer(locale.LAB_EDIT_NEW_LAB)
    elif isinstance(target, int):
        await state.update_data(edit_index=target)
        await state.set_state(LabStates.edit_value)
        name = report.results[target - 1].analyte
        await message.answer(locale.LAB_EDIT_NEW_VALUE.format(name=name))
    else:
        await message.answer(locale.LAB_EDIT_BAD_ROW)


@router.message(LabStates.edit_value)
async def on_edit_value(message: Message, state: FSMContext) -> None:
    value = parse_value(message.text or "")
    if value is None:
        await message.answer(locale.LAB_EDIT_BAD_VALUE)
        return
    data = await state.get_data()
    report = _pending_report(data)
    index = cast(int, data["edit_index"])
    report.results[index - 1].value = value
    report.results[index - 1].value_text = None
    await _restore_confirmation(message, state, report)


@router.message(LabStates.edit_date)
async def on_edit_date(message: Message, state: FSMContext) -> None:
    try:
        new_date = date.fromisoformat((message.text or "").strip())
    except ValueError:
        await message.answer(locale.LAB_EDIT_BAD_DATE)
        return
    data = await state.get_data()
    report = _pending_report(data)
    report.report_date = new_date
    await _restore_confirmation(message, state, report)


@router.message(LabStates.edit_lab)
async def on_edit_lab(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    report = _pending_report(data)
    report.lab = (message.text or "").strip() or None
    await _restore_confirmation(message, state, report)


async def _restore_confirmation(
    message: Message, state: FSMContext, report: ExtractedReport
) -> None:
    await state.update_data(report=_report_to_state(report))
    await state.set_state(LabStates.confirming)
    await answer_chunked(
        message, render_confirmation_text(report), reply_markup=confirmation_keyboard()
    )


@router.callback_query(F.data == _CB_CONFIRM)
async def on_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    report_id = data.get("report_id")
    # The pending values live only in FSM state until confirm. If that state is gone
    # (a restart, or a menu-label tap reset it), there is nothing to save — tell the
    # user instead of crashing on a missing key or silently doing nothing.
    if not isinstance(report_id, int) or "report" not in data:
        if isinstance(callback.message, Message):
            await callback.message.answer(locale.LAB_OFFER_EXPIRED)
        await callback.answer()
        return
    report = _pending_report(data)
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    # Consume the confirm/edit/cancel buttons up front: confirmation runs a slow LLM, and the
    # buttons must not stay tappable (no editing/cancelling a report that is already being saved).
    await clear_inline_keyboard(callback)

    async with get_session() as session:
        db_report = await session.get(LabReport, report_id)
        if db_report is None:
            await callback.answer()
            return
        await persist_confirmed(
            session,
            report=db_report,
            analytes=report.results,
            report_date=report.report_date,
            lab=report.lab,
            conclusion=report.conclusion,
            report_type=report.report_type,
            narrative=report.narrative,
        )
        user_id = db_report.user_id

    # Acknowledge immediately: the expert interpretation runs an LLM and can take a while, so
    # confirm the save and show a "working" note before the slow call — never a silent gap.
    await callback.message.answer(locale.LAB_CONFIRMED)
    await callback.message.answer(locale.LAB_INTERPRET_WORKING)
    await callback.answer()

    keys = {normalize_analyte(a.analyte) for a in report.results}
    async with get_session() as session:
        # Pass the confirmed report so the summary is the Stage 5 expert interpretation.
        summary = await compute_report_summary(
            session, user_id=user_id, analyte_keys=keys, report=report
        )
        # Persist the generated summary so /history can show it without re-calling the LLM.
        # Stored as the plain safety-checked text; HTML styling is applied only at send time.
        stored = await session.get(LabReport, report_id)
        if stored is not None:
            stored.summary = summary.text
            await session.commit()

    # The analysis comes FIRST (the valuable part), never buried under a wall of charts.
    await answer_chunked(
        callback.message, render_interpretation_html(summary.text), parse_mode=ParseMode.HTML
    )
    # Charts are opt-in: offer a button only when there is a real trend to show (>=2 dates), so a
    # confirm never dumps dozens of flat same-day images. The button carries the report_id.
    if summary.chart_count > 0:
        await callback.message.answer(
            locale.LAB_CHARTS_OFFER.format(n=summary.chart_count),
            reply_markup=_charts_keyboard(report_id),
        )

    # The report is saved; the pending FSM data is no longer needed. The repeat/concern
    # offers are stateless (they carry report_id), so they survive a state reset.
    await state.clear()
    await callback.message.answer(locale.LAB_REPEAT_OFFER, reply_markup=_repeat_keyboard(report_id))


def _charts_keyboard(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=locale.BTN_SHOW_CHARTS, callback_data=_rid_cb(_CB_CHARTS, report_id)
                )
            ]
        ]
    )


def _repeat_keyboard(report_id: int) -> InlineKeyboardMarkup:
    keys = list(_CB_REPEAT_DAYS)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=locale.BTN_REPEAT_1M, callback_data=_rid_cb(keys[0], report_id)
                ),
                InlineKeyboardButton(
                    text=locale.BTN_REPEAT_3M, callback_data=_rid_cb(keys[1], report_id)
                ),
                InlineKeyboardButton(
                    text=locale.BTN_REPEAT_6M, callback_data=_rid_cb(keys[2], report_id)
                ),
            ],
            [
                InlineKeyboardButton(
                    text=locale.BTN_REPEAT_OTHER,
                    callback_data=_rid_cb(_CB_REPEAT_OTHER, report_id),
                ),
                InlineKeyboardButton(
                    text=locale.BTN_REPEAT_NO, callback_data=_rid_cb(_CB_REPEAT_NO, report_id)
                ),
            ],
        ]
    )


def _concern_keyboard(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=locale.BTN_LAB_CONCERN_YES,
                    callback_data=_rid_cb(_CB_CONCERN_YES, report_id),
                ),
                InlineKeyboardButton(
                    text=locale.BTN_LAB_CONCERN_NO,
                    callback_data=_rid_cb(_CB_CONCERN_NO, report_id),
                ),
            ]
        ]
    )


def _now() -> datetime:
    return datetime.now(ZoneInfo(get_settings().timezone))


async def _do_repeat(
    answer_to: Message,
    *,
    owner_tg: int,
    report_id: int,
    run_at: datetime,
    scheduler: ReminderScheduler,
) -> None:
    """Create the repeat-lab reminder for a saved report (stateless — by report_id)."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=owner_tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        label = (report.lab if report is not None else None) or locale.LAB_REPEAT_LABEL
        await proactive.add_repeat_lab(
            session, user=user, run_at=run_at, label=label, scheduler=scheduler, report_id=report_id
        )
        await session.commit()
    await answer_to.answer(locale.LAB_REPEAT_SET.format(when=run_at.date().isoformat()))


async def _draft_concern_name(owner_tg: int, report_id: int) -> str | None:
    """The proposed concern name from a report's first out-of-range analyte, or None."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=owner_tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        if report is None:
            return None
        for result in history.ordered_results(report):
            flag = compute_flag(result.value, result.ref_low, result.ref_high)
            if flag in (ResultFlag.LOW, ResultFlag.HIGH):
                return locale.PROBLEM_LAB_DRAFT.format(analyte=result.analyte)
    return None


async def _offer_concern(answer_to: Message, *, owner_tg: int, report_id: int) -> None:
    """After the repeat step, offer the lab-flag concern (if anything is out of range)."""
    if await _draft_concern_name(owner_tg, report_id) is not None:
        await answer_to.answer(locale.LAB_CONCERN_OFFER, reply_markup=_concern_keyboard(report_id))


@router.callback_query(F.data.startswith("lab:rep:"))
async def on_repeat(
    callback: CallbackQuery, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
    """All repeat-offer buttons — stateless (report_id is in the callback data)."""
    data = callback.data or ""
    owner = callback.from_user.id if callback.from_user else None
    if not isinstance(callback.message, Message) or owner is None:
        await callback.answer()
        return
    await clear_inline_keyboard(callback)  # the repeat offer is one-shot

    if (report_id := _parse_rid(_CB_REPEAT_OTHER, data)) is not None:
        await state.set_state(LabStates.repeat_custom)
        await state.update_data(repeat_report_id=report_id)
        await callback.message.answer(locale.LAB_REPEAT_ASK_CUSTOM, reply_markup=cancel_keyboard())
        await callback.answer()
        return

    if (report_id := _parse_rid(_CB_REPEAT_NO, data)) is not None:
        await _offer_concern(callback.message, owner_tg=owner, report_id=report_id)
        await callback.answer()
        return

    for prefix, days in _CB_REPEAT_DAYS.items():
        if (report_id := _parse_rid(prefix, data)) is not None:
            run_at = _now() + timedelta(days=days)
            await _do_repeat(
                callback.message,
                owner_tg=owner,
                report_id=report_id,
                run_at=run_at,
                scheduler=reminder_scheduler,
            )
            await _offer_concern(callback.message, owner_tg=owner, report_id=report_id)
            break
    await callback.answer()


@router.message(LabStates.repeat_custom, F.text)
async def on_repeat_custom(
    message: Message, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
    data = await state.get_data()
    report_id = data.get("repeat_report_id")
    run_at = reminders.parse_relative_when(message.text or "", base=_now())
    if run_at is None:
        await message.answer(locale.LAB_REPEAT_BAD_CUSTOM)  # stay in state
        return
    await state.clear()
    owner = message.from_user.id if message.from_user else None
    if isinstance(report_id, int) and owner is not None:
        await _do_repeat(
            message,
            owner_tg=owner,
            report_id=report_id,
            run_at=run_at,
            scheduler=reminder_scheduler,
        )
        await _offer_concern(message, owner_tg=owner, report_id=report_id)


@router.callback_query(F.data.startswith("lab:con:"))
async def on_concern(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    """Concern-offer buttons — stateless (report_id is in the callback data)."""
    data = callback.data or ""
    owner = callback.from_user.id if callback.from_user else None
    if not isinstance(callback.message, Message) or owner is None:
        await callback.answer()
        return
    await clear_inline_keyboard(callback)  # the concern offer is one-shot
    if (report_id := _parse_rid(_CB_CONCERN_YES, data)) is not None:
        name = await _draft_concern_name(owner, report_id)
        if name is not None:
            async with get_session() as session:
                user = await ensure_user(session, telegram_id=owner)
                await proactive.add_problem(
                    session,
                    user=user,
                    name=name,
                    scheduler=reminder_scheduler,
                    report_id=report_id,
                )
                await session.commit()
            await callback.message.answer(locale.PROBLEM_ADDED)
    await callback.answer()


@router.callback_query(F.data.startswith(_CB_CHARTS + ":"))
async def on_show_charts(callback: CallbackQuery) -> None:
    """Render the report's trend charts on demand (analytes with a real, multi-date trend)."""
    report_id = _parse_rid(_CB_CHARTS, callback.data)
    owner = callback.from_user.id if callback.from_user else None
    if report_id is None or owner is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await clear_inline_keyboard(callback)  # one-shot — consume the 📈 button
    async with get_session() as session:
        stored = await session.get(LabReport, report_id)
        if stored is None:
            await callback.answer()
            return
        names = (
            await session.scalars(select(LabResult.analyte).where(LabResult.report_id == report_id))
        ).all()
        keys = {normalize_analyte(n) for n in names}
        charts = await render_report_charts(session, user_id=stored.user_id, analyte_keys=keys)
    for name, png in charts:
        await callback.message.answer_photo(BufferedInputFile(png, filename=f"{name}.png"))
    if not charts:  # defensive: the data changed since the offer was made
        await callback.message.answer(locale.LAB_CHARTS_EMPTY)
    await callback.answer()


@router.callback_query(F.data.startswith("lab:"))
async def on_lab_stale(callback: CallbackQuery) -> None:
    """Fallback: any lab button whose flow already ended (state lost / already actioned)
    still gets an acknowledgement instead of silently hanging."""
    await clear_inline_keyboard(callback)
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.LAB_OFFER_EXPIRED)
    await callback.answer()


def _suffix_from_mime(mime: str | None) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "application/pdf": ".pdf",
    }.get(mime or "", "")
