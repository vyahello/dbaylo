"""Lab intake + OCR-confirmation flow (aiogram 3 FSM).

The interactive half of L2: receive a photo/PDF, extract, show the values for
confirmation in Ukrainian, allow corrections (including report date and lab —
a misread date silently corrupts the time series), and persist only on confirm.

The formatting and edit-target parsing are pure functions (unit-tested); the
handlers stay thin. Pending values live in FSM state, never in the DB until the
user confirms (rail #2). MemoryStorage is fine for single-user local dev; a
persistent FSM store would be needed if the process restarts mid-confirmation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any, cast

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from dbaylo import locale
from dbaylo.db import get_session
from dbaylo.db.models import LabReport, ReportStatus
from dbaylo.labs.extraction import ExtractionFailed, extract_with_escalation
from dbaylo.labs.intake import (
    create_pending_report,
    ensure_user,
    is_supported,
    persist_confirmed,
    save_original_file,
)
from dbaylo.labs.pipeline import compute_report_summary
from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport
from dbaylo.labs.trends import compute_flag, normalize_analyte

router = Router(name="labs")

_CB_CONFIRM = "lab:confirm"
_CB_EDIT = "lab:edit"
_CB_CANCEL = "lab:cancel"


class LabStates(StatesGroup):
    confirming = State()
    edit_pick = State()
    edit_value = State()
    edit_date = State()
    edit_lab = State()


# --- Pure helpers (unit-tested) -------------------------------------------------


def render_confirmation_text(report: ExtractedReport) -> str:
    """Build the Ukrainian confirmation table for an extracted report."""
    date_txt = report.report_date.isoformat() if report.report_date else locale.LAB_DATE_UNKNOWN
    lab_txt = report.lab or locale.LAB_LAB_UNKNOWN
    lines = [
        f"{locale.LAB_DATE_LABEL}: {date_txt}",
        f"{locale.LAB_LAB_LABEL}: {lab_txt}",
        "",
    ]
    for i, a in enumerate(report.results, 1):
        flag = compute_flag(a.value, a.ref_low, a.ref_high)
        emoji = locale.FLAG_EMOJI.get(flag.value, "")
        ref = a.display_reference()
        line = f"{i}. {a.analyte} — {a.display_value()} ({locale.LAB_NORM_LABEL} {ref}) {emoji}"
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

    outcome = await extract_with_escalation(str(path))
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
    await message.answer(render_confirmation_text(outcome), reply_markup=confirmation_keyboard())


@router.callback_query(F.data == _CB_CANCEL)
async def on_cancel(callback: CallbackQuery, state: FSMContext) -> None:
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
    await message.answer(render_confirmation_text(report), reply_markup=confirmation_keyboard())


@router.callback_query(F.data == _CB_CONFIRM)
async def on_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    report = _pending_report(data)
    report_id = cast(int, data["report_id"])
    if callback.message is None:
        await callback.answer()
        return

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
        )
        user_id = db_report.user_id

    keys = {normalize_analyte(a.analyte) for a in report.results}
    async with get_session() as session:
        summary = await compute_report_summary(session, user_id=user_id, analyte_keys=keys)

    await state.clear()
    await callback.message.answer(locale.LAB_CONFIRMED)
    for name, png in summary.charts:
        await callback.message.answer_photo(BufferedInputFile(png, filename=f"{name}.png"))
    await callback.message.answer(summary.text)
    await callback.answer()


def _suffix_from_mime(mime: str | None) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "application/pdf": ".pdf",
    }.get(mime or "", "")
