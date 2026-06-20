"""History & retrieval bot flow (Tier 1.2): browse past lab reports, open the
original file, view stored results, see a single-analyte trend, and delete a report.

Two ways in:

* **Commands / buttons** — ``/history`` (alias ``/reports``) lists confirmed reports
  recent-first (optionally filtered: ``/history synevo``, ``/history 2026-05``,
  ``/history травень``, ``/history останній``); ``/trend <analyte>`` and a per-result
  ``📈`` button show the deterministic trend.
* **Natural language** — a free-text turn that *looks* like a history request (intent
  + a concrete token) is handled here. It is screened through the safety gate FIRST
  (the only sanctioned path to anything LLM-adjacent), then parsed by the deterministic
  ``history`` module. If no concrete filter survives, the turn is handed back to the
  companion — history never steals normal chat.

No LLM lives in retrieval: listing, rendering, and trends are pure/deterministic. The
only model call is the companion fallback, which re-enters through the gate.
"""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from dbaylo import locale
from dbaylo.bot.formatting import answer_chunked
from dbaylo.bot.keyboards import clear_inline_keyboard
from dbaylo.companion import callbacks, history
from dbaylo.companion.conversation import generate_reply
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.db import get_session
from dbaylo.db.models import LabReport
from dbaylo.labs.intake import ensure_user
from dbaylo.safety import GateSource, screen

router = Router(name="history")


def _telegram_id(event: Message | CallbackQuery) -> int | None:
    return event.from_user.id if event.from_user else None


def _list_keyboard(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=locale.BTN_HIST_FILE, callback_data=callbacks.history_file(report_id)
                ),
                InlineKeyboardButton(
                    text=locale.BTN_HIST_RESULTS, callback_data=callbacks.history_results(report_id)
                ),
                InlineKeyboardButton(
                    text=locale.BTN_HIST_DELETE, callback_data=callbacks.history_delete(report_id)
                ),
            ]
        ]
    )


def _cleanup_keyboard(orphans: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=locale.HIST_PENDING_FOOTER.format(n=orphans),
                    callback_data=callbacks.HIST_CLEAN,
                )
            ]
        ]
    )


async def _send_list(message: Message, reports: list[LabReport], orphans: int) -> None:
    """Render a report list (recent-first), with the opt-in orphan-cleanup affordance."""
    cleanup = _cleanup_keyboard(orphans) if orphans else None
    if not reports:
        await message.answer(locale.HIST_EMPTY, reply_markup=cleanup)
        return
    await message.answer(locale.HIST_HEADER, reply_markup=cleanup)
    for report in reports[: history.DEFAULT_LIMIT]:
        results = history.ordered_results(report)
        await message.answer(
            history.render_report_line(report, results), reply_markup=_list_keyboard(report.id)
        )
    if len(reports) > history.DEFAULT_LIMIT:
        await message.answer(locale.HIST_MORE.format(n=history.DEFAULT_LIMIT))


# --- /history (and /reports) ----------------------------------------------------


async def render_history(message: Message, telegram_id: int, raw: str = "") -> None:
    """List confirmed reports (optionally filtered) — from /history or the menu."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        filt = None
        if raw:
            labs = await history.known_labs(session, user_id=user.id)
            filt = history.parse_history_query(raw, known_labs=labs)
        reports = await history.list_confirmed(session, user_id=user.id, filt=filt)
        orphans = await history.count_orphans(session, user_id=user.id, now=datetime.now())
    await _send_list(message, reports, orphans)


@router.message(Command("history", "reports"))
async def cmd_history(message: Message, command: CommandObject) -> None:
    tg = _telegram_id(message)
    if tg is None:
        return
    await render_history(message, tg, (command.args or "").strip())


# --- Natural-language history search (gate FIRST, then deterministic parse) ------


async def _is_history_text(message: Message) -> bool:
    text = message.text or ""
    return bool(text) and not text.startswith("/") and history.is_history_query(text)


@router.message(StateFilter(None), _is_history_text)
async def on_history_query(message: Message) -> None:
    tg = _telegram_id(message)
    text = message.text or ""
    if tg is None:
        return
    # The gate is the only sanctioned path from user text onward — screen BEFORE
    # we parse or answer (a red flag here still wins over a history lookup).
    decision = screen(text)
    if decision.source is not GateSource.CLEARED:
        await message.answer(decision.message)
        return

    orphans = 0
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        labs = await history.known_labs(session, user_id=user.id)
        filt = history.parse_history_query(text, known_labs=labs)
        reports: list[LabReport] | None = None
        if filt.has_filter:
            reports = await history.list_confirmed(session, user_id=user.id, filt=filt)
            orphans = await history.count_orphans(session, user_id=user.id, now=datetime.now())

    if reports is None:
        # Intent matched but nothing concrete for THIS user — don't show an empty
        # history result; hand the turn back to the companion.
        reply = await generate_reply(text)
        await message.answer(reply.text)
        return
    await _send_list(message, reports, orphans)


# --- /trend <analyte> + per-result trend button ---------------------------------


async def _send_trend(message: Message, *, telegram_id: int, analyte: str) -> None:
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        view = await history.trend_for_analyte(session, user_id=user.id, analyte=analyte)
    if view.chart is not None:
        await message.answer_photo(
            BufferedInputFile(view.chart, filename="trend.png"), caption=view.text
        )
    else:
        await message.answer(view.text)


@router.message(Command("trend"))
async def cmd_trend(message: Message, command: CommandObject) -> None:
    tg = _telegram_id(message)
    if tg is None:
        return
    analyte = (command.args or "").strip()
    if not analyte:
        await message.answer(locale.TREND_ASK)
        return
    await _send_trend(message, telegram_id=tg, analyte=analyte)


# --- Callbacks ------------------------------------------------------------------


@router.callback_query(F.data.startswith(callbacks.HIST_FILE + ":"))
async def on_history_file(callback: CallbackQuery) -> None:
    report_id = callbacks.parse_history_file(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None:
        await callback.answer()
        return
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        path = history.report_file_path(report) if report is not None else None
    if isinstance(callback.message, Message):
        if path is None:
            await callback.message.answer(locale.HIST_FILE_GONE)
        else:
            await callback.message.answer_document(FSInputFile(str(path)))
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.HIST_RESULTS + ":"))
async def on_history_results(callback: CallbackQuery) -> None:
    report_id = callbacks.parse_history_results(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None:
        await callback.answer()
        return
    text = locale.HIST_FILE_GONE
    keyboard: InlineKeyboardMarkup | None = None
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        if report is not None:
            results = history.ordered_results(report)
            text = history.render_report_results(report, results)
            rows = [
                [
                    InlineKeyboardButton(
                        text=f"📈 {result.analyte[:28]}",
                        callback_data=callbacks.history_trend(report_id, index),
                    )
                ]
                for index, result in enumerate(results)
            ]
            keyboard = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    if isinstance(callback.message, Message):
        await answer_chunked(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.HIST_TREND + ":"))
async def on_history_trend(callback: CallbackQuery) -> None:
    parsed = callbacks.parse_history_trend(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None:
        await callback.answer()
        return
    report_id, index = parsed
    analyte: str | None = None
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        if report is not None:
            results = history.ordered_results(report)
            if 0 <= index < len(results):
                analyte = results[index].analyte
    if isinstance(callback.message, Message):
        if analyte is None:
            await callback.message.answer(locale.TREND_NOT_FOUND)
        else:
            await _send_trend(callback.message, telegram_id=tg, analyte=analyte)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.HIST_DELETE + ":"))
async def on_history_delete(callback: CallbackQuery) -> None:
    report_id = callbacks.parse_history_delete(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None:
        await callback.answer()
        return
    text = locale.HIST_FILE_GONE
    keyboard: InlineKeyboardMarkup | None = None
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        if report is not None:
            results = history.ordered_results(report)
            date_txt = report.report_date.isoformat() if report.report_date else locale.HIST_NO_DATE
            details = (
                f"{date_txt} · {report.lab or locale.LAB_LAB_UNKNOWN} · {len(results)} показників"
            )
            items = [
                locale.HIST_COUPLING_CONCERN.format(name=concern.name)
                for concern in await history.linked_active_concerns(session, report_id)
            ]
            if await history.linked_active_reminders(session, report_id):
                items.append(locale.HIST_COUPLING_REMINDER)
            text = locale.HIST_DELETE_CONFIRM.format(details=details)
            if items:
                text += "\n\n" + locale.HIST_DELETE_COUPLING.format(items="; ".join(items))
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=locale.BTN_DELETE_YES,
                            callback_data=callbacks.history_delete_ok(report_id),
                        ),
                        InlineKeyboardButton(
                            text=locale.BTN_DELETE_NO,
                            callback_data=callbacks.history_delete_no(report_id),
                        ),
                    ]
                ]
            )
    if isinstance(callback.message, Message):
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.HIST_DELETE_OK + ":"))
async def on_history_delete_ok(
    callback: CallbackQuery, reminder_scheduler: ReminderScheduler
) -> None:
    report_id = callbacks.parse_history_delete_ok(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None:
        await callback.answer()
        return
    await clear_inline_keyboard(callback)  # consume the confirm buttons (no re-tap / cancel after)
    deleted = False
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        if report is not None:
            await history.delete_report(session, report=report, scheduler=reminder_scheduler)
            await session.commit()
            deleted = True
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.HIST_DELETED if deleted else locale.HIST_FILE_GONE)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.HIST_DELETE_NO + ":"))
async def on_history_delete_no(callback: CallbackQuery) -> None:
    await clear_inline_keyboard(callback)  # consume the confirm buttons
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.HIST_DELETE_CANCELLED)
    await callback.answer()


@router.callback_query(F.data == callbacks.HIST_CLEAN)
async def on_history_clean(callback: CallbackQuery) -> None:
    tg = _telegram_id(callback)
    if tg is None:
        await callback.answer()
        return
    await clear_inline_keyboard(callback)  # one-shot purge — consume the 🧹 button
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        removed = await history.cleanup_orphans(session, user_id=user.id, now=datetime.now())
        await session.commit()
    if isinstance(callback.message, Message):
        await callback.message.answer(locale.HIST_PENDING_CLEANED.format(n=removed))
    await callback.answer()
