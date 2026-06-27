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
import html
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
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from dbaylo import locale
from dbaylo.bot import prescription_flow
from dbaylo.bot.formatting import answer_chunked
from dbaylo.bot.history_flow import send_analysis
from dbaylo.bot.keyboards import cancel_keyboard, clear_inline_keyboard
from dbaylo.companion import callbacks, history, notewarm, proactive, reminders
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.config import get_settings
from dbaylo.db import get_session
from dbaylo.db.models import LabReport, ReportStatus, ResultFlag, User
from dbaylo.labs.extraction import ExtractionFailed, extract_document
from dbaylo.labs.intake import (
    create_pending_report,
    ensure_user,
    file_hash,
    find_confirmed_by_hash,
    is_supported,
    persist_confirmed,
    save_original_file,
)
from dbaylo.labs.labnames import normalize_lab
from dbaylo.labs.pipeline import compute_report_summary
from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport
from dbaylo.labs.trends import compute_flag, is_out_of_range, series_key

router = Router(name="labs")

_CB_CONFIRM = "lab:confirm"
_CB_EDIT = "lab:edit"
_CB_CANCEL = "lab:cancel"
_CB_SHOW_ALL = "lab:all"  # expand the collapsed in-range rows into the full table
_CB_EDIT_DATE = "lab:edate"  # one-tap edit of the report date
_CB_EDIT_LAB = "lab:elab"  # one-tap edit of the lab name
_CB_EDIT_KEEP = "lab:keep"  # back out of a date/lab edit, re-show the confirmation unchanged

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
_CB_CHART_NO = (
    "lab:chart:no"  # decline the charts offer (the picker opens via callbacks.chart_open)
)


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


def _esc(text: str) -> str:
    # Body text, never an attribute -> keep apostrophes/quotes literal (see formatting._escape).
    return html.escape(text, quote=False)


def _is_oor(a: ExtractedAnalyte) -> bool:
    """The lab flagged the row, or the value is numerically out of its reference range."""
    return is_out_of_range(a.value, a.ref_low, a.ref_high, a.out_of_range, a.value_text)


def _is_unread(a: ExtractedAnalyte) -> bool:
    """OCR could not read a value (and it is not a qualitative result like 'не виявлено')."""
    return a.value is None and not a.value_text


def _needs_check(a: ExtractedAnalyte) -> bool:
    """A row worth surfacing at confirm time: out of range, or unreadable (rail #5)."""
    return _is_oor(a) or _is_unread(a)


def _row_marker(a: ExtractedAnalyte) -> str:
    """⚠️ for an out-of-range row, ❔ for an unreadable one, nothing for an in-range one.

    The in-range rows deliberately carry NO ✅ — a screen of green checks reads as
    'все добре', which the safety rails forbid implying (rail #4). Absence of a marker
    is the 'in range' signal; the global row number is the bullet.
    """
    if _is_oor(a):
        return locale.FLAG_ATTENTION
    if _is_unread(a):
        return locale.FLAG_EMOJI["unknown"]
    return ""


def _row_line(index: int, a: ExtractedAnalyte) -> str:
    """One numbered analyte row (the global index keeps edit-by-number working from any view)."""
    body = (
        f"{index}. {_esc(a.analyte)} — {_esc(a.display_value())} "
        f"({locale.LAB_NORM_LABEL} {_esc(a.display_reference())})"
    )
    return f"{body} {_row_marker(a)}".rstrip()


def _grouped_rows(rows: list[tuple[int, ExtractedAnalyte]]) -> list[str]:
    """Render rows under bold panel headers (blood vs urine stay apart)."""
    out: list[str] = []
    prev_section: object = _NO_SECTION
    for index, a in rows:
        if a.section != prev_section:
            prev_section = a.section
            if a.section:
                if out and out[-1] != "":
                    out.append("")
                out.append(locale.LAB_SECTION_HEADER.format(section=f"<b>{_esc(a.section)}</b>"))
        out.append(_row_line(index, a))
    return out


def _summary_line(total: int, n_oor: int, n_unread: int) -> str:
    parts = [locale.LAB_CONFIRM_COUNT.format(n=total)]
    if n_unread:
        parts.append(locale.LAB_CONFIRM_ATTENTION.format(n=n_oor + n_unread))
    elif n_oor:
        parts.append(locale.LAB_CONFIRM_OOR.format(n=n_oor))
    return " · ".join(parts)


def _confirm_header(report: ExtractedReport) -> str:
    date_txt = report.report_date.isoformat() if report.report_date else locale.LAB_DATE_UNKNOWN
    lab_txt = report.lab or locale.LAB_LAB_UNKNOWN
    inner = locale.LAB_CONFIRM_HEADER.format(date=_esc(date_txt), lab=_esc(lab_txt))
    return f"<b>{inner}</b>"


def _render_narrative_confirmation(report: ExtractedReport) -> str:
    date_txt = report.report_date.isoformat() if report.report_date else locale.LAB_DATE_UNKNOWN
    # An imaging study often has no lab brand — show date (· lab only when there IS one), never
    # a bare "невідома"; its study type is the title.
    meta = f"{date_txt} · {_esc(report.lab)}" if report.lab else date_txt
    lines = [f"📄 <b>{_esc(report.report_type or locale.LAB_DOC_GENERIC)}</b>", meta, ""]
    if report.narrative:
        lines += [_esc(report.narrative), ""]
    if report.conclusion:
        lines += [f"{locale.LAB_CONCLUSION_LABEL}: {_esc(report.conclusion)}", ""]
    lines.append(locale.LAB_CONFIRM_PROMPT)
    return "\n".join(lines)


# Collapsing in-range rows into an aggregate only helps when there are MANY of them; a
# handful (e.g. a single-analyte ДІЛА report) is better shown by name than hidden behind
# "✅ Усі N — у межах норми". At/below this count, the in-range rows are listed.
_INLINE_NORMAL_MAX = 5


def _collapses_normal(report: ExtractedReport) -> bool:
    """True when there are more in-range rows than we inline — they get the aggregate + the
    '📋 Усі показники' expand. A few in-range rows are shown by name instead."""
    return sum(1 for a in report.results if not _needs_check(a)) > _INLINE_NORMAL_MAX


def render_confirmation_text(report: ExtractedReport) -> str:
    """Problems-first confirmation view (Telegram HTML): header + summary + the rows that need a
    look (out of range / unreadable). In-range rows are listed by name when there are only a few,
    or collapsed into an aggregate (with a '📋 Усі показники' expand, rail #5) when there are many.
    A narrative document keeps its prose view."""
    if report.is_narrative:
        return _render_narrative_confirmation(report)
    indexed = list(enumerate(report.results, 1))
    attention = [(i, a) for i, a in indexed if _needs_check(a)]
    normal = [(i, a) for i, a in indexed if not _needs_check(a)]
    n_oor = sum(1 for a in report.results if _is_oor(a))
    n_unread = sum(1 for a in report.results if _is_unread(a))
    total = len(report.results)
    collapse = _collapses_normal(report)

    lines = [_confirm_header(report), _summary_line(total, n_oor, n_unread)]
    if report.conclusion:
        lines.append(f"{locale.LAB_CONCLUSION_LABEL}: {_esc(report.conclusion)}")
    if attention:
        lines += ["", locale.LAB_CONFIRM_ATT_HEADER.format(n=len(attention))]
        lines += _grouped_rows(attention)
        if normal and collapse:
            lines += ["", locale.LAB_CONFIRM_NORMAL_AGG.format(n=len(normal))]
        elif normal:
            lines += ["", locale.LAB_CONFIRM_NORMAL_HEADER]
            lines += _grouped_rows(normal)
        lines += ["", locale.LAB_CONFIRM_VERIFY]
    elif collapse:  # all in range, but too many to list
        lines += ["", locale.LAB_CONFIRM_ALL_NORMAL.format(n=total), "", locale.LAB_CONFIRM_PROMPT]
    else:  # all in range, few enough to name (e.g. the single-analyte report the user hit)
        lines += ["", locale.LAB_CONFIRM_NORMAL_HEADER]
        lines += _grouped_rows(normal)
        lines += ["", locale.LAB_CONFIRM_PROMPT]
    return "\n".join(lines)


def render_confirmation_full(report: ExtractedReport) -> str:
    """The full numbered table — every row, grouped by panel, neutral markers (the opt-in
    '📋 Усі показники' expand from the problems-first view)."""
    lines = [_confirm_header(report), locale.LAB_CONFIRM_FULL_HEADER, ""]
    lines += _grouped_rows(list(enumerate(report.results, 1)))
    return "\n".join(lines)


def confirmation_keyboard(report: ExtractedReport, *, full: bool = False) -> InlineKeyboardMarkup:
    """Confirm / expand / quick-edit / cancel. The expand button appears only when in-range
    rows are hidden (and not already on the full view); quick-edit covers the two fields most
    often wrong (date corrupts the whole series); number-typing handles the rare value fix."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=locale.BTN_CONFIRM_ALL, callback_data=_CB_CONFIRM)]
    ]
    n_total = len(report.results)
    if not full and _collapses_normal(report):  # expand only when in-range rows are hidden
        rows.append(
            [
                InlineKeyboardButton(
                    text=locale.BTN_CONFIRM_SHOW_ALL.format(n=n_total), callback_data=_CB_SHOW_ALL
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text=locale.BTN_EDIT_DATE, callback_data=_CB_EDIT_DATE),
            InlineKeyboardButton(text=locale.BTN_EDIT_LAB, callback_data=_CB_EDIT_LAB),
        ]
    )
    last_row = [InlineKeyboardButton(text=locale.BTN_CANCEL, callback_data=_CB_CANCEL)]
    if not report.is_narrative:  # narrative has no numbered rows to edit by number
        last_row.insert(0, InlineKeyboardButton(text=locale.BTN_EDIT, callback_data=_CB_EDIT))
    rows.append(last_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    # ALL fields must survive the round-trip — dropping narrative/report_type/conclusion turned a
    # confirmed МРТ into an empty 'tabular' report, whose interpretation hallucinated "table empty".
    return {
        "report_date": report.report_date.isoformat() if report.report_date else None,
        "birth_date": report.birth_date.isoformat() if report.birth_date else None,
        "sex": report.sex,
        "lab": report.lab,
        "conclusion": report.conclusion,
        "report_type": report.report_type,
        "narrative": report.narrative,
        "results": [vars(a) for a in report.results],
    }


def _report_from_state(data: Mapping[str, Any]) -> ExtractedReport:
    raw_date = data.get("report_date")
    raw_birth = data.get("birth_date")
    raw_results = cast("list[dict[str, Any]]", data.get("results") or [])

    def _str(key: str) -> str | None:
        value = data.get(key)
        return value if isinstance(value, str) else None

    return ExtractedReport(
        results=[ExtractedAnalyte(**row) for row in raw_results],
        report_date=date.fromisoformat(raw_date) if isinstance(raw_date, str) else None,
        birth_date=date.fromisoformat(raw_birth) if isinstance(raw_birth, str) else None,
        sex=_str("sex"),
        lab=_str("lab"),
        conclusion=_str("conclusion"),
        report_type=_str("report_type"),
        narrative=_str("narrative"),
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

    buffer = BytesIO()
    await message.bot.download(file_id, destination=buffer)
    data = buffer.getvalue()
    content_hash = file_hash(data)

    async with get_session() as session:
        user = await ensure_user(session, message.from_user.id, message.from_user.full_name)
        # Same bytes already confirmed before? Don't re-extract (slow) or duplicate the report —
        # point the user at the saved one instead.
        duplicate = await find_confirmed_by_hash(
            session, user_id=user.id, content_hash=content_hash
        )
        if duplicate is not None:
            when = duplicate.report_date.isoformat() if duplicate.report_date else "?"
            await message.answer(
                locale.LAB_DUPLICATE.format(date=when),
                reply_markup=_saved_report_keyboard(duplicate.id),
            )
            return
        await message.answer(locale.LAB_RECEIVED)
        path = save_original_file(data, user_id=user.id, suffix=suffix)
        report = await create_pending_report(
            session, user=user, file_path=path, content_hash=content_hash
        )
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

    # Auto-routing: the read classified this upload as a prescription / лист призначень AND it has
    # no analyte table (a lab that merely prints a meds footer keeps its rows → stays a lab). Hand a
    # freely-dropped prescription to the medication flow, so the user need not pre-tap 📷 З фото
    # рецепта. The file is already saved (``path``); the meds flow re-reads it with its focused
    # prescription parser (drug · dose · time) and confirms before anything persists (rail #2/#5).
    if outcome.is_prescription and not outcome.results:
        async with get_session() as session:
            pending = await session.get(LabReport, report_id)
            if pending is not None:
                pending.status = ReportStatus.DISCARDED  # it is not a lab report after all
        await message.answer(locale.LAB_LOOKS_LIKE_PRESCRIPTION)
        await prescription_flow.present_prescription_from_path(
            message, state, path=str(path), content_hash=content_hash
        )
        return

    async with get_session() as session:
        pending = await session.get(LabReport, report_id)
        if pending is not None:
            pending.raw_ocr = json.dumps(_report_to_state(outcome), ensure_ascii=False)

    await state.set_state(LabStates.confirming)
    await state.update_data(report_id=report_id, report=_report_to_state(outcome))
    # Problems-first, so a typical report is one short message. A pathological one (many
    # flagged rows) can still overflow Telegram's 4096-char cap, so send section-aware chunks
    # as a safety net — the action buttons ride the last one.
    await answer_chunked(
        message,
        render_confirmation_text(outcome),
        reply_markup=confirmation_keyboard(outcome),
        parse_mode=ParseMode.HTML,
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


@router.callback_query(F.data == _CB_SHOW_ALL)
async def on_show_all(callback: CallbackQuery, state: FSMContext) -> None:
    """Expand the collapsed in-range rows: send the full table and move the action buttons to
    the bottom of it (the compact message's keyboard is consumed, so there's one button set)."""
    data = await state.get_data()
    report = _pending_report(data)
    await clear_inline_keyboard(callback)
    if isinstance(callback.message, Message):
        await answer_chunked(
            callback.message,
            render_confirmation_full(report),
            reply_markup=confirmation_keyboard(report, full=True),
            parse_mode=ParseMode.HTML,
        )
    await callback.answer()


def _keep_keyboard() -> InlineKeyboardMarkup:
    """A single 'leave as is' button so an accidental field-edit tap is escapable."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=locale.BTN_EDIT_KEEP, callback_data=_CB_EDIT_KEEP)]
        ]
    )


async def _prompt_edit_date(message: Message, report: ExtractedReport) -> None:
    current = report.report_date.isoformat() if report.report_date else locale.LAB_DATE_UNKNOWN
    await message.answer(
        locale.LAB_EDIT_NEW_DATE.format(current=current), reply_markup=_keep_keyboard()
    )


async def _prompt_edit_lab(message: Message, report: ExtractedReport) -> None:
    current = report.lab or locale.LAB_LAB_UNKNOWN
    await message.answer(
        locale.LAB_EDIT_NEW_LAB.format(current=current), reply_markup=_keep_keyboard()
    )


@router.callback_query(F.data == _CB_EDIT_DATE)
async def on_edit_date_btn(callback: CallbackQuery, state: FSMContext) -> None:
    """One-tap jump to CORRECTING the auto-recognised date (a misread date corrupts the series)."""
    await clear_inline_keyboard(callback)
    report = _pending_report(await state.get_data())
    await state.set_state(LabStates.edit_date)
    if isinstance(callback.message, Message):
        await _prompt_edit_date(callback.message, report)
    await callback.answer()


@router.callback_query(F.data == _CB_EDIT_LAB)
async def on_edit_lab_btn(callback: CallbackQuery, state: FSMContext) -> None:
    """One-tap jump to CORRECTING the auto-recognised lab name."""
    await clear_inline_keyboard(callback)
    report = _pending_report(await state.get_data())
    await state.set_state(LabStates.edit_lab)
    if isinstance(callback.message, Message):
        await _prompt_edit_lab(callback.message, report)
    await callback.answer()


@router.callback_query(F.data == _CB_EDIT_KEEP)
async def on_edit_keep(callback: CallbackQuery, state: FSMContext) -> None:
    """Leave the field as recognised: re-show the confirmation unchanged (nothing is lost)."""
    await clear_inline_keyboard(callback)
    report = _pending_report(await state.get_data())
    if isinstance(callback.message, Message):
        await _restore_confirmation(callback.message, state, report)
    await callback.answer()


@router.message(LabStates.edit_pick)
async def on_edit_pick(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    report = _pending_report(data)
    target = parse_edit_target(message.text or "", len(report.results))
    if target == "date":
        await state.set_state(LabStates.edit_date)
        await _prompt_edit_date(message, report)
    elif target == "lab":
        await state.set_state(LabStates.edit_lab)
        await _prompt_edit_lab(message, report)
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
    report.lab = normalize_lab((message.text or "").strip() or None)
    await _restore_confirmation(message, state, report)


async def _restore_confirmation(
    message: Message, state: FSMContext, report: ExtractedReport
) -> None:
    await state.update_data(report=_report_to_state(report))
    await state.set_state(LabStates.confirming)
    await answer_chunked(
        message,
        render_confirmation_text(report),
        reply_markup=confirmation_keyboard(report),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == _CB_CONFIRM)
async def on_confirm(
    callback: CallbackQuery, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
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
            birth_date=report.birth_date,
            sex=report.sex,
            conclusion=report.conclusion,
            report_type=report.report_type,
            narrative=report.narrative,
        )
        # Mark the analysis PENDING (committed on block exit) BEFORE the slow LLM call, so a
        # restart mid-interpretation leaves an empty summary the startup recovery can finish.
        db_report.summary = history.SUMMARY_PENDING
        user_id = db_report.user_id
        # A newly confirmed report may put an indicator out of range — proactively (re)schedule the
        # daily check-in so Дбайло starts engaging on it, even without a manually-added concern.
        user = await session.get(User, user_id)
        if user is not None:
            await proactive.reconcile_checkin(session, user=user, scheduler=reminder_scheduler)

    # Acknowledge immediately: the expert interpretation runs an LLM and can take a while, so
    # confirm the save and show a "working" note before the slow call — never a silent gap.
    await callback.message.answer(locale.LAB_CONFIRMED)
    await callback.message.answer(locale.LAB_INTERPRET_WORKING)
    await callback.answer()

    keys = {series_key(a.section, a.analyte) for a in report.results}
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

    # The analysis comes FIRST (the valuable part). Delivered as a navigable overview (Загалом +
    # per-section buttons), not a 4-message wall.
    await send_analysis(callback.message, summary.text, report_id)

    # Warm the educational-note cache for this user's indicators in the background, so a chart /
    # table / PDF opened after this report already has a description for every indicator (incl. any
    # brand-new analyte) and never waits on claude. Best-effort, fire-and-forget.
    notewarm.warm_user_notes_in_background(user_id)

    # The report is saved; the pending FSM data is no longer needed. The follow-up offers are
    # sequenced ONE AT A TIME (repeat → concern → charts picker), each shown only after the prior
    # is answered, so a confirm never dumps a stack of prompts. They are stateless (report_id in
    # the callback data), so they survive a restart / state reset.
    await state.clear()
    await callback.message.answer(locale.LAB_REPEAT_OFFER, reply_markup=_repeat_keyboard(report_id))


def _saved_report_keyboard(report_id: int) -> InlineKeyboardMarkup:
    """For a duplicate upload: open the already-saved report, or delete that previous version so
    the file can be re-uploaded (e.g. to re-read it after an extraction fix). Delete reuses the
    safe two-step `/history` flow (shows what's removed, then confirms)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=locale.BTN_VIEW_SAVED, callback_data=callbacks.history_results(report_id)
                )
            ],
            [
                InlineKeyboardButton(
                    text=locale.BTN_DELETE_PREV, callback_data=callbacks.history_delete(report_id)
                )
            ],
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


async def _advance_after_repeat(answer_to: Message, *, owner_tg: int, report_id: int) -> None:
    """Next step after the repeat offer: the concern offer if anything is out of range, otherwise
    straight to the charts picker (the last step) — one prompt at a time, never a stack."""
    if await _draft_concern_name(owner_tg, report_id) is not None:
        await answer_to.answer(locale.LAB_CONCERN_OFFER, reply_markup=_concern_keyboard(report_id))
    else:
        await _advance_after_concern(answer_to, owner_tg=owner_tg, report_id=report_id)


def _charts_offer_keyboard(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=locale.BTN_CHARTS_SHOW, callback_data=callbacks.chart_open(report_id)
                ),
                InlineKeyboardButton(
                    text=locale.BTN_CHARTS_SKIP, callback_data=_rid_cb(_CB_CHART_NO, report_id)
                ),
            ]
        ]
    )


def _to_analyses_keyboard() -> InlineKeyboardMarkup:
    """End-of-intake navigation: a one-tap jump to the saved analyses (the /history list), so the
    lab flow LEADS somewhere — symmetry with the prescription result's «Мої ліки / Нагадування»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=locale.BTN_TO_ANALYSES, callback_data=callbacks.MENU_OPEN_HISTORY
                )
            ]
        ]
    )


async def _advance_after_concern(answer_to: Message, *, owner_tg: int, report_id: int) -> None:
    """The final step: OFFER the charts (yes/no) — never auto-open a big picker. Shown only if the
    report has a real trend; tapping 'Так' opens the picker (`callbacks.chart_open`). With no trend
    the chain ends here, so we close it with a «📊 До аналізів» jump."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=owner_tg)
        has_trend = bool(
            await history.list_report_trends(session, user_id=user.id, report_id=report_id)
        )
    if has_trend:
        await answer_to.answer(
            locale.LAB_CHARTS_PROMPT, reply_markup=_charts_offer_keyboard(report_id)
        )
    else:
        await answer_to.answer(locale.LAB_INTAKE_DONE, reply_markup=_to_analyses_keyboard())


@router.callback_query(F.data.startswith(_CB_CHART_NO + ":"))
async def on_charts_no(callback: CallbackQuery) -> None:
    """Decline the charts offer — consume the buttons, then close with a «📊 До аналізів»."""
    await clear_inline_keyboard(callback)
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.LAB_INTAKE_DONE, reply_markup=_to_analyses_keyboard())
    await callback.answer()


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
        await _advance_after_repeat(callback.message, owner_tg=owner, report_id=report_id)
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
            await _advance_after_repeat(callback.message, owner_tg=owner, report_id=report_id)
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
        await _advance_after_repeat(message, owner_tg=owner, report_id=report_id)


@router.callback_query(F.data.startswith("lab:con:"))
async def on_concern(callback: CallbackQuery, reminder_scheduler: ReminderScheduler) -> None:
    """Concern-offer buttons — stateless (report_id is in the callback data)."""
    data = callback.data or ""
    owner = callback.from_user.id if callback.from_user else None
    if not isinstance(callback.message, Message) or owner is None:
        await callback.answer()
        return
    await clear_inline_keyboard(callback)  # the concern offer is one-shot
    report_id = _parse_rid(_CB_CONCERN_YES, data)
    if report_id is not None:
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
    else:
        report_id = _parse_rid(_CB_CONCERN_NO, data)
    if report_id is not None:  # last step of the chain: the charts picker (silent if no trend)
        await _advance_after_concern(callback.message, owner_tg=owner, report_id=report_id)
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
