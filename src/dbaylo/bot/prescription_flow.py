"""Read a prescription photo -> confirm -> set medication reminders (the agent does the typing).

Step 4 of the menu→AI-agent overhaul: instead of typing each medication + time by hand, the user
sends a photo/PDF of the doctor's prescription. ``labs.prescription`` OCRs it (drug · dose · times),
the bot shows it for confirmation (rail #5 — OCR is never trusted silently; rail #2 — nothing
persists until the user confirms), and only then creates a :class:`Medication` per drug with one
reminder per time. The dose is stored as record-keeping (rail #1 permits it) but NEVER shows in a
reminder; a medication whose time the page didn't print is listed for manual entry, not guessed.

Photo routing: this router is registered BEFORE ``lab_flow`` and its photo/document handlers are
state-filtered to ``PrescriptionStates.waiting_photo``, so a prescription upload is handled here
while every other photo still flows to the lab pipeline.

This module imports the extractor (``labs.prescription``), NOT ``run_claude`` directly, so the
safety choke-point invariant is unaffected (extraction is OCR/record-keeping, like lab extraction).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, time
from io import BytesIO
from pathlib import Path

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
from dbaylo.bot.keyboards import cancel_keyboard, clear_inline_keyboard
from dbaylo.companion import callbacks, medications, proactive
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.config import get_settings
from dbaylo.db import get_session
from dbaylo.labs.extraction import ExtractionFailed
from dbaylo.labs.intake import ensure_user, is_supported, save_original_file
from dbaylo.labs.prescription import ExtractedMedication, extract_prescription

router = Router(name="prescription")


class PrescriptionStates(StatesGroup):
    waiting_photo = State()
    confirming = State()


async def start_prescription_dialog(message: Message, state: FSMContext) -> None:
    """Enter the prescription-photo flow (from the 💊 Ліки section) — always cancellable."""
    await state.set_state(PrescriptionStates.waiting_photo)
    await message.answer(locale.MED_FROM_PHOTO_ASK, reply_markup=cancel_keyboard())


@router.message(PrescriptionStates.waiting_photo, F.photo)
async def on_prescription_photo(message: Message, state: FSMContext) -> None:
    if not message.photo:
        return
    await _handle_upload(message, state, file_id=message.photo[-1].file_id, suffix=".jpg")


@router.message(PrescriptionStates.waiting_photo, F.document)
async def on_prescription_document(message: Message, state: FSMContext) -> None:
    document = message.document
    if document is None:
        return
    suffix = Path(document.file_name or "").suffix.lower()
    if not is_supported(suffix):
        await message.answer(locale.LAB_UNSUPPORTED_FILE)
        return
    await _handle_upload(message, state, file_id=document.file_id, suffix=suffix)


async def _handle_upload(message: Message, state: FSMContext, *, file_id: str, suffix: str) -> None:
    if message.from_user is None or message.bot is None:
        return
    buffer = BytesIO()
    await message.bot.download(file_id, destination=buffer)
    data = buffer.getvalue()
    await message.answer(locale.PRESCRIPTION_RECEIVED)

    async with get_session() as session:
        user = await ensure_user(session, message.from_user.id, message.from_user.full_name)
        path = save_original_file(data, user_id=user.id, suffix=suffix)

    await present_prescription_from_path(message, state, path=str(path))


async def present_prescription_from_path(message: Message, state: FSMContext, *, path: str) -> None:
    """Read an ALREADY-SAVED prescription file → confirm. Shared by the explicit 📷 button flow and
    the **auto-routing** path (`lab_flow` hands off a freely-dropped photo the lab read classified
    as a prescription — the file is already on disk, so no re-download / re-save)."""
    budget = 2 * get_settings().claude_extract_timeout_s + 30
    try:
        outcome = await asyncio.wait_for(extract_prescription(path), timeout=budget)
    except Exception:  # noqa: BLE001 — never leave the user hanging on a bad upload
        outcome = ExtractionFailed("prescription extraction timed out or errored")

    if isinstance(outcome, ExtractionFailed):
        await message.answer(locale.PRESCRIPTION_FAILED)
        await state.clear()
        return
    if not outcome.medications:
        await message.answer(locale.PRESCRIPTION_NONE)
        await state.clear()
        return

    # A doctor writes a FREQUENCY ("3 рази на день"), not clock times — so when the page gave a
    # frequency but no hours, the bot picks the times instead of leaving the med for manual entry.
    resolved = [_with_resolved_times(med) for med in outcome.medications]
    # The COURSE label is the model's best clinical naming of the set ("Урологічний курс"); only
    # when it couldn't tell do we fall back to a date label the user can rename.
    course = outcome.course or locale.PRESCRIPTION_COURSE_DEFAULT.format(
        date=date.today().isoformat()
    )
    await state.set_state(PrescriptionStates.confirming)
    # Keep the photo path so the saved meds link back to it (the user can re-open the prescription),
    # and the course label that groups these meds — the user can rename it (see below).
    await state.update_data(
        meds=[_med_to_state(med) for med in resolved], rx_path=path, course=course
    )
    await message.answer(_render_confirm(resolved, course=course), reply_markup=_confirm_keyboard())


@router.message(PrescriptionStates.confirming, F.text)
async def on_prescription_course(message: Message, state: FSMContext) -> None:
    """While confirming, a typed message renames the prescription's GROUP (course) — the agent
    auto-determines a default, the user can override it in their own words, then re-confirm."""
    course = (message.text or "").strip()
    if not course:
        return
    data = await state.get_data()
    raw = data.get("meds") or []
    await state.update_data(course=course)
    meds = [_med_from_state(item) for item in raw if isinstance(item, dict)]
    body = locale.PRESCRIPTION_COURSE_UPDATED.format(course=course) + "\n\n"
    body += _render_confirm(meds, course=course)
    await message.answer(body, reply_markup=_confirm_keyboard())


def _with_resolved_times(med: ExtractedMedication) -> ExtractedMedication:
    """Fill a med's times from its frequency phrase when the page printed no clock times — the bot
    picks the hours from "зранку" / "на ніч" / "3 р/д" / "3 рази на день" etc. (a doctor writes the
    schedule, not the clock). Unchanged when explicit times are present, or nothing usable found."""
    if med.times or not med.frequency:
        return med
    resolved = medications.times_from_text(med.frequency)
    if not resolved:
        return med
    times = tuple(t.strftime("%H:%M") for t in resolved)
    return replace(med, times=times)


@router.callback_query(PrescriptionStates.confirming, F.data == callbacks.PRESCRIPTION_CONFIRM)
async def on_prescription_confirm(
    callback: CallbackQuery, state: FSMContext, reminder_scheduler: ReminderScheduler
) -> None:
    data = await state.get_data()
    raw = data.get("meds") or []
    rx_path = data.get("rx_path")  # the original prescription photo, linked to each saved med
    course = data.get("course") or None  # the group label these meds are filed under
    await state.clear()
    await clear_inline_keyboard(callback)  # consume the confirm/cancel buttons
    tg = callback.from_user.id if callback.from_user else None
    if tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return

    source_file = str(rx_path) if rx_path else None
    today = date.today()
    meds = [_med_from_state(item) for item in raw if isinstance(item, dict)]
    created: list[str] = []
    skipped: list[str] = []
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        for med in meds:
            times = _parse_times(med.times)
            if times:
                await proactive.add_medication(
                    session,
                    user=user,
                    name=med.name,
                    times=times,
                    scheduler=reminder_scheduler,
                    dose=med.dose,
                    source_file=source_file,
                    course=course,
                    until=medications.course_end(today, med.duration),  # the doctor's term
                )
                created.append(med.name)
            else:
                skipped.append(med.name)
        await session.commit()

    await callback.answer()
    await callback.message.answer(_result_text(created, skipped))


# --- Rendering / (de)serialization ----------------------------------------------


def _med_to_state(med: ExtractedMedication) -> dict[str, object]:
    return {
        "name": med.name,
        "dose": med.dose,
        "times": list(med.times),
        "frequency": med.frequency,
        "duration": med.duration,
    }


def _med_from_state(item: dict[str, object]) -> ExtractedMedication:
    times = item.get("times")
    return ExtractedMedication(
        name=str(item.get("name") or ""),
        dose=(str(item["dose"]) if item.get("dose") else None),
        times=tuple(str(t) for t in times) if isinstance(times, list) else (),
        frequency=(str(item["frequency"]) if item.get("frequency") else None),
        duration=(str(item["duration"]) if item.get("duration") else None),
    )


def _parse_times(tokens: tuple[str, ...]) -> list[time]:
    out: list[time] = []
    for token in tokens:
        hh, _, mm = token.partition(":")
        if hh.isdigit() and mm.isdigit():
            out.append(time(int(hh), int(mm)))
    return out


def _med_line(med: ExtractedMedication) -> str:
    parts = [f"💊 {med.name}"]
    if med.dose:
        parts.append(med.dose)
    if med.times:
        parts.append(", ".join(med.times))
    elif med.frequency:
        parts.append(f"{med.frequency} ({locale.PRESCRIPTION_LINE_NO_TIME})")
    else:
        parts.append(locale.PRESCRIPTION_LINE_NO_TIME)
    if med.duration:  # the doctor's term — when the bot will stop reminding
        parts.append(locale.PRESCRIPTION_LINE_DURATION.format(duration=med.duration))
    return " · ".join(parts)


def _render_confirm(meds: list[ExtractedMedication], *, course: str | None = None) -> str:
    lines = [locale.PRESCRIPTION_CONFIRM_HEADER, ""]
    lines.extend(_med_line(med) for med in meds)
    if course:
        lines.append("")
        lines.append(locale.PRESCRIPTION_CONFIRM_COURSE.format(course=course))
    return "\n".join(lines)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=locale.BTN_PRESCRIPTION_CONFIRM,
                    callback_data=callbacks.PRESCRIPTION_CONFIRM,
                )
            ],
            [
                InlineKeyboardButton(
                    text=locale.BTN_DIALOG_CANCEL, callback_data=callbacks.CANCEL_DIALOG
                )
            ],
        ]
    )


def _result_text(created: list[str], skipped: list[str]) -> str:
    if not created:
        return locale.PRESCRIPTION_NOTHING_SAVED
    text = locale.PRESCRIPTION_SAVED.format(names=", ".join(created))
    if skipped:
        text += "\n" + locale.PRESCRIPTION_SAVED_SKIPPED.format(names=", ".join(skipped))
    return text
