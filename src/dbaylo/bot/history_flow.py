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

import asyncio
import contextlib
import html
import re
from datetime import date, datetime
from pathlib import Path

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.bot import consult_flow
from dbaylo.bot.formatting import (
    SECTION_KEYS,
    answer_chunked,
    render_interpretation_html,
    split_interpretation,
)
from dbaylo.bot.keyboards import clear_inline_keyboard
from dbaylo.companion import callbacks, grouping, history, notecache
from dbaylo.companion.conversation import generate_reply
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.config import get_settings
from dbaylo.db import get_session
from dbaylo.db.models import LabReport, ReportKind
from dbaylo.labs.charts import (
    PdfChart,
    PdfCover,
    PdfQualTrend,
    render_qual_table_png,
    render_trends_pdf,
)
from dbaylo.labs.humanize import describe_indicator, note_cache_key
from dbaylo.labs.intake import ensure_user
from dbaylo.labs.labnames import normalize_lab
from dbaylo.labs.pipeline import compute_report_summary, render_chart_and_summary
from dbaylo.labs.trends import TrendSummary, series_key, specimen
from dbaylo.safety import GateSource, screen

router = Router(name="history")


def _telegram_id(event: Message | CallbackQuery) -> int | None:
    return event.from_user.id if event.from_user else None


_PAGE_SIZE = 8


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _chart_filename(name: str) -> str:
    """A descriptive PNG name for a single trend chart: 'Дбайло-динаміка-<analyte>.png', so a saved
    chart says WHAT it is instead of a bare 'Еритроцити.png'. Control chars / path separators are
    stripped (the series KEY's ``\\x1f`` and friends make aiohttp reject the Content-Disposition
    header — that once silently killed every single-chart pick)."""
    analyte = _safe_filename(name)
    if not analyte or analyte == "dbaylo":  # _safe_filename's fallback for an empty/garbage name
        analyte = locale.CHART_PNG_FALLBACK
    return locale.CHART_PNG_FILENAME.format(analyte=analyte)


def _safe_filename(name: str) -> str:
    """A human-readable but transport-safe attachment name: drop control chars and path separators,
    collapse whitespace to dashes. Cyrillic is preserved (Telegram serves UTF-8 filenames fine)."""
    cleaned = "".join(ch for ch in name if ord(ch) >= 0x20 and ch not in "/\\")
    cleaned = re.sub(r"\s+", "-", cleaned).strip("-. ")
    return cleaned or "dbaylo"


def _report_date_lab(report: LabReport | None) -> tuple[str, str]:
    """(date, lab) of a report for filenames/headers — never a control char, always something."""
    if report is None:
        return locale.HIST_NO_DATE, locale.LAB_LAB_UNKNOWN
    date_txt = report.report_date.isoformat() if report.report_date else locale.HIST_NO_DATE
    lab = normalize_lab(report.lab) or locale.LAB_LAB_UNKNOWN
    return date_txt, lab


def _report_kind(report: LabReport | None) -> str:
    """Short content tag of a report (Кров / Сеча / Спермограма / …) for filenames — same label as
    the history-list button, so a downloaded file says WHAT analysis it was."""
    results = getattr(report, "results", None)
    if report is None or results is None:
        return ""
    return history.report_kind_label(list(results))  # order irrelevant (just counts categories)


def _file_parts(report: LabReport | None) -> tuple[str, str, str]:
    """(kind-with-trailing-dash-or-empty, date, lab-without-city) — the building blocks of a
    self-describing download name: what it is, when, and where (the city adds no info)."""
    date_txt, lab = _report_date_lab(report)
    lab = lab.split(",")[0].strip() or lab  # drop the city ("Сінево, Львів" -> "Сінево")
    kind = _report_kind(report)
    return (f"{kind}-" if kind else ""), date_txt, lab


def _pdf_filename(report: LabReport | None) -> str:
    """The dynamics PDF is named per report — kind + date + lab (no city), not one name for all."""
    kind, date_txt, lab = _file_parts(report)
    return _safe_filename(locale.CHART_PDF_FILENAME.format(kind=kind, date=date_txt, lab=lab))


def _source_filename(report: LabReport | None, path: Path) -> str:
    """The original upload, renamed to kind + date + lab (+ real extension), not random chars."""
    kind, date_txt, lab = _file_parts(report)
    name = locale.CHART_SOURCE_FILENAME.format(kind=kind, date=date_txt, lab=lab, ext=path.suffix)
    return _safe_filename(name)


_CAPTION_MAX = 1024  # Telegram photo-caption limit


async def _show_uploading(message: Message) -> None:
    """Native 'sending a photo…' indicator, so the brief chart render isn't a blank wait. Best
    effort — never let a failed chat-action break the flow."""
    with contextlib.suppress(Exception):
        await message.bot.send_chat_action(message.chat.id, "upload_photo")  # type: ignore[union-attr]


def _chart_nav_keyboard(report_id: int, index: int, total: int) -> InlineKeyboardMarkup:
    """Carousel nav shown UNDER each chart photo so you flip indicators in place instead of
    scrolling back up to the picker. ONE row: ⬅️ / a combined position-and-list button (📋 i/n,
    which shows where you are AND taps back to the full list) / ➡️ — no duplicate buttons."""
    row: list[InlineKeyboardButton] = []
    if index > 0:
        row.append(_btn(locale.BTN_CHART_PREV, callbacks.chart_nav(report_id, index - 1)))
    row.append(
        _btn(
            locale.CHART_NAV_POSITION.format(i=index + 1, n=total),
            callbacks.history_dynamics(report_id),  # the counter IS the back-to-list button
        )
    )
    if index < total - 1:
        row.append(_btn(locale.BTN_CHART_NEXT, callbacks.chart_nav(report_id, index + 1)))
    # A second row: ask Дбайло about THIS indicator (grounded consultation on its trend).
    consult_row = [_btn(locale.BTN_CONSULT, callbacks.consult_chart(report_id, index))]
    return InlineKeyboardMarkup(inline_keyboard=[row, consult_row])


def _chart_caption(report: LabReport | None, summary: TrendSummary) -> str:
    """The dynamics line, led by the source-report context ('🔬 З аналізу <date> · <lab>') when the
    chart was opened from a report — so flipping through the carousel never loses which analysis
    and date you are looking at."""
    line = history.chart_dynamics_caption(summary)
    if report is None:
        return line
    date_txt, lab = _report_date_lab(report)
    ctx = locale.CHART_SOURCE_CONTEXT.format(date=date_txt, lab=lab)
    return f"{ctx}\n{line}"


async def _chart_full_caption(dynamics: str, analyte: str, specimen: str | None) -> str | None:
    """The dynamics line + the sample-specific educational note, or None if there is no note / it
    would overflow the caption limit (the caller then keeps the dynamics line alone). The note is
    read from / written to the persistent cache, so it is generated by claude only once ever and
    browsing a chart also warms the cache for the PDF."""
    async with get_session() as session:
        note = await notecache.get_note(session, analyte=analyte, specimen=specimen)
        await session.commit()
    if not note:
        return None
    full = f"{dynamics}\n\n{note}\n\n{locale.CHART_NOTE_DISCLAIMER}"
    return full if len(full) <= _CAPTION_MAX else None


async def _send_chart(
    message: Message,
    *,
    png: bytes,
    dynamics: str,
    analyte: str,
    specimen: str | None,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """Send the chart IMMEDIATELY with the deterministic dynamics caption, then fill in the
    sample-specific educational note by editing the caption — so a cache-miss note (~several
    seconds) never leaves the user staring at a blank screen waiting for the chart."""
    sent = await message.answer_photo(
        BufferedInputFile(png, filename=_chart_filename(analyte)),
        caption=dynamics,
        reply_markup=keyboard,
    )
    full = await _chart_full_caption(dynamics, analyte, specimen)
    if full:
        with contextlib.suppress(TelegramBadRequest):
            await sent.edit_caption(caption=full, reply_markup=keyboard)


def _list_view(
    reports: list[LabReport], page: int, orphans: int
) -> tuple[str, InlineKeyboardMarkup]:
    """The paginated master list: one tappable button per report, a pager, the cleanup footer."""
    pages = max(1, -(-len(reports) // _PAGE_SIZE))  # ceil division
    page = max(0, min(page, pages - 1))
    chunk = reports[page * _PAGE_SIZE : page * _PAGE_SIZE + _PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = [
        [
            _btn(
                history.report_button_label(r, history.ordered_results(r)),
                callbacks.history_open(r.id, page),
            )
        ]
        for r in chunk
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn(locale.BTN_HIST_PREV, callbacks.history_page(page - 1)))
    if page < pages - 1:
        nav.append(_btn(locale.BTN_HIST_NEXT, callbacks.history_page(page + 1)))
    if nav:
        rows.append(nav)
    if orphans:
        rows.append([_btn(locale.HIST_PENDING_FOOTER.format(n=orphans), callbacks.HIST_CLEAN)])
    # Back to the "Аналізи" hub (dynamics lives there now, not duplicated on the list).
    rows.append([_btn(locale.BTN_HIST_BACK, callbacks.MENU_OPEN_LABS)])
    header = locale.HIST_LIST_HEADER.format(n=len(reports))
    if pages > 1:
        header += " · " + locale.HIST_PAGE_LABEL.format(page=page + 1, pages=pages)
    return header, InlineKeyboardMarkup(inline_keyboard=rows)


def _card_keyboard(
    report_id: int, page: int, *, is_narrative: bool = False
) -> InlineKeyboardMarkup:
    rows = [
        [
            _btn(locale.BTN_HIST_INTERPRET, callbacks.history_interpret(report_id)),
            _btn(locale.BTN_HIST_RESULTS, callbacks.history_results(report_id)),
        ]
    ]
    # A narrative/imaging doc (МРТ/КТ/УЗД) has NO numeric indicators, so "Динаміка" is meaningless
    # for it — show just the file. A tabular report keeps the dynamics button next to the file.
    if is_narrative:
        rows.append([_btn(locale.BTN_HIST_FILE, callbacks.history_file(report_id))])
    else:
        rows.append(
            [
                _btn(locale.BTN_HIST_DYNAMICS, callbacks.history_dynamics(report_id)),
                _btn(locale.BTN_HIST_FILE, callbacks.history_file(report_id)),
            ]
        )
    # Talk to Дбайло about this report. Past-conversation memory is folded INTO this (the consult
    # recalls it automatically + says so) — a separate «Памʼять» button here was confusing.
    rows.append([_btn(locale.BTN_CONSULT, callbacks.consult_report(report_id))])
    rows.append(
        [
            _btn(locale.BTN_HIST_DELETE, callbacks.history_delete(report_id)),
            _btn(locale.BTN_HIST_BACK, callbacks.history_back(page)),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _load_reports(session: AsyncSession, user_id: int) -> tuple[list[LabReport], int]:
    reports = await history.list_confirmed(session, user_id=user_id, limit=None)
    orphans = await history.count_orphans(session, user_id=user_id, now=datetime.now())
    return reports, orphans


async def _send_list(message: Message, reports: list[LabReport], orphans: int) -> None:
    """Send the master list as a single message (fresh, from /history or the menu)."""
    if not reports:
        rows: list[list[InlineKeyboardButton]] = []
        if orphans:
            rows.append([_btn(locale.HIST_PENDING_FOOTER.format(n=orphans), callbacks.HIST_CLEAN)])
        rows.append([_btn(locale.BTN_HIST_BACK, callbacks.MENU_OPEN_LABS)])
        await message.answer(
            locale.HIST_EMPTY, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
        return
    text, keyboard = _list_view(reports, 0, orphans)
    await message.answer(text, reply_markup=keyboard)


# --- /history (and /reports) ----------------------------------------------------


async def render_history(message: Message, telegram_id: int, raw: str = "") -> None:
    """List confirmed reports (optionally filtered) — from /history or the menu."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        filt = None
        if raw:
            labs = await history.known_labs(session, user_id=user.id)
            filt = history.parse_history_query(raw, known_labs=labs)
        reports = await history.list_confirmed(session, user_id=user.id, filt=filt, limit=None)
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
            reports = await history.list_confirmed(session, user_id=user.id, filt=filt, limit=None)
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
    await _show_uploading(message)
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        view = await history.trend_for_analyte(session, user_id=user.id, analyte=analyte)
    if view.chart is not None:
        await _send_chart(
            message,
            png=view.chart,
            dynamics=view.text,
            analyte=view.analyte,
            specimen=view.specimen,
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


# --- Master-detail navigation (edit-in-place) -----------------------------------


async def _edit_to_list(callback: CallbackQuery, page: int, telegram_id: int) -> None:
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        reports, orphans = await _load_reports(session, user.id)
    if isinstance(callback.message, Message):
        text, keyboard = _list_view(reports, page, orphans)
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


async def open_history_in_place(callback: CallbackQuery, telegram_id: int) -> None:
    """Edit the current message into the report list (the 'Аналізи' hub -> history). Edit-in-place
    so the list's ◀ Назад returns to the hub in the SAME message — no message spam."""
    await _edit_to_list(callback, 0, telegram_id)


@router.callback_query(F.data.startswith(callbacks.HIST_PAGE + ":"))
async def on_history_page(callback: CallbackQuery) -> None:
    page = callbacks.parse_history_page(callback.data or "")
    tg = _telegram_id(callback)
    if page is None or tg is None:
        await callback.answer()
        return
    await _edit_to_list(callback, page, tg)


@router.callback_query(F.data.startswith(callbacks.HIST_BACK + ":"))
async def on_history_back(callback: CallbackQuery) -> None:
    page = callbacks.parse_history_back(callback.data or "")
    tg = _telegram_id(callback)
    if page is None or tg is None:
        await callback.answer()
        return
    await _edit_to_list(callback, page, tg)


@router.callback_query(F.data.startswith(callbacks.HIST_OPEN + ":"))
async def on_history_open(callback: CallbackQuery) -> None:
    """Open a report's card (edit the list message in place)."""
    parsed = callbacks.parse_history_open(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    report_id, page = parsed
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        card = (
            history.render_card(report, history.ordered_results(report))
            if report is not None
            else None
        )
    if card is None:
        await callback.answer(locale.HIST_FILE_GONE, show_alert=True)
        return
    is_narrative = report is not None and report.kind == ReportKind.NARRATIVE
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(
            card, reply_markup=_card_keyboard(report_id, page, is_narrative=is_narrative)
        )
    await callback.answer()


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
            await callback.message.answer_document(
                FSInputFile(str(path), filename=_source_filename(report, path))
            )
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.HIST_RESULTS + ":"))
async def on_history_results(callback: CallbackQuery) -> None:
    """Focused results: lab conclusion + ONLY the out-of-range rows + an aggregate for the rest."""
    report_id = callbacks.parse_history_results(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    text = locale.HIST_FILE_GONE
    parse_mode: str | None = None
    keyboard: InlineKeyboardMarkup | None = None
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        if report is not None:
            text = render_interpretation_html(
                history.render_problems(report, history.ordered_results(report))
            )
            parse_mode = ParseMode.HTML
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _btn(locale.BTN_HIST_RESULTS_ALL, callbacks.history_results_all(report_id)),
                        _btn(locale.BTN_HIST_DYNAMICS, callbacks.history_dynamics(report_id)),
                    ],
                    [_btn(locale.BTN_CONSULT, callbacks.consult_report(report_id))],
                    [_btn(locale.BTN_HIST_BACK, callbacks.history_open(report_id, 0))],
                ]
            )
    if keyboard is not None:  # edit the card in place; '◀ Назад' returns to it
        await _show_view(callback.message, text, keyboard, edit=True)
    else:
        await answer_chunked(callback.message, text, parse_mode=parse_mode)
    await callback.answer()


@router.callback_query(F.data.startswith(callbacks.HIST_RESULTS_ALL + ":"))
async def on_history_results_all(callback: CallbackQuery) -> None:
    """The full table — opt-in from the focused view, chunked, with the P.S."""
    report_id = callbacks.parse_history_results_all(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    text = locale.HIST_FILE_GONE
    parse_mode: str | None = None
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        if report is not None:
            text = render_interpretation_html(
                history.render_report_results(report, history.ordered_results(report))
            )
            parse_mode = ParseMode.HTML
    await answer_chunked(callback.message, text, parse_mode=parse_mode)
    await callback.answer()


# --- Charts picker: one button per trending analyte → its single chart (no 45-image dump) -------

_CHART_PAGE_SIZE = 8


def _charts_picker_view(
    items: list[history.PickItem],
    report_id: int,
    page: int,
    flagged_total: int = 0,
    no_dynamics: tuple[str, ...] = (),
) -> tuple[str, InlineKeyboardMarkup]:
    """A paginated list — one button per pickable indicator (flagged first, ⚠️-marked; a 📋 marks a
    qualitative one that opens a TABLE instead of a chart), a pager, and opt-in exports. Rendered as
    HTML: a bold "Поза нормою: N" header (the flagged ones are the ⚠️ buttons), and any flagged
    indicator WITHOUT a chart/table yet (single measurement) is named so it is not lost."""
    pages = max(1, -(-len(items) // _CHART_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    start = page * _CHART_PAGE_SIZE
    rows: list[list[InlineKeyboardButton]] = []
    for offset, item in enumerate(items[start : start + _CHART_PAGE_SIZE]):
        prefix = (locale.CHART_FLAGGED_PREFIX if item.flagged else "") + (
            locale.CHART_QUAL_PREFIX if item.qualitative else ""
        )
        rows.append([_btn(f"{prefix}{item.name}", callbacks.chart_pick(report_id, start + offset))])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn(locale.BTN_HIST_PREV, callbacks.chart_page(report_id, page - 1)))
    if page < pages - 1:
        nav.append(_btn(locale.BTN_HIST_NEXT, callbacks.chart_page(report_id, page + 1)))
    if nav:
        rows.append(nav)
    rows.append([_btn(locale.BTN_CHART_ALL, callbacks.chart_all(report_id))])
    rows.append([_btn(locale.BTN_CHART_PDF, callbacks.chart_pdf(report_id))])
    rows.append([_btn(locale.BTN_HIST_BACK, callbacks.history_open(report_id, 0))])
    pick = locale.CHART_PICK_HEADER
    if pages > 1:
        pick += " · " + locale.HIST_PAGE_LABEL.format(page=page + 1, pages=pages)
    lines: list[str] = []
    if flagged_total:
        lines.append(locale.CHART_PICK_FLAGGED.format(n=flagged_total))
        if no_dynamics:
            names = " · ".join(html.escape(n) for n in no_dynamics)
            lines.append(locale.CHART_PICK_FLAGGED_NODYN.format(names=names))
        lines.append("")  # blank line before the pick instruction
    lines.append(pick)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _flagged_summary(
    report: LabReport | None, items: list[history.PickItem]
) -> tuple[int, tuple[str, ...]]:
    """(total flagged in this report, names of the flagged ones that have NO dynamics button yet) —
    so the picker banner shows a count and never silently drops a single-measurement flagged one."""
    fmap = history.report_flagged_map(report)
    pickable_flagged = {it.key for it in items if it.flagged}
    no_dyn = tuple(name for key, name in fmap.items() if key not in pickable_flagged)
    return len(fmap), no_dyn


async def open_charts_picker(
    message: Message,
    *,
    telegram_id: int,
    report_id: int,
    silent_if_empty: bool = False,
    edit: bool = False,
) -> None:
    """Show the charts picker for a report (used by /history and the post-confirm chain). When
    there is no trend yet, say so — unless called silently as the last step of the confirm chain.
    From the card (``edit``) it replaces the card in place; '◀ Назад' returns to it."""
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        items = await history.list_report_pickables(session, user_id=user.id, report_id=report_id)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
    if not items:
        if not silent_if_empty:
            # No typed command — offer the values as a BUTTON (📊 Показники) + back to the card.
            empty_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _btn(locale.BTN_HIST_RESULTS, callbacks.history_results(report_id)),
                        _btn(locale.BTN_HIST_BACK, callbacks.history_open(report_id, 0)),
                    ]
                ]
            )
            await _show_view(message, locale.HIST_DYNAMICS_EMPTY, empty_kb, edit=edit)
        return
    flagged_total, no_dyn = _flagged_summary(report, items)
    text, keyboard = _charts_picker_view(items, report_id, 0, flagged_total, no_dyn)
    await _show_view(message, text, keyboard, edit=edit)


@router.callback_query(F.data.startswith(callbacks.HIST_DYNAMICS + ":"))
async def on_history_dynamics(callback: CallbackQuery) -> None:
    """Open the charts picker for a report (from the /history card)."""
    report_id = callbacks.parse_history_dynamics(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    await open_charts_picker(callback.message, telegram_id=tg, report_id=report_id, edit=True)


@router.callback_query(F.data.startswith(callbacks.CHART_OPEN + ":"))
async def on_chart_open(callback: CallbackQuery) -> None:
    """Accept the post-confirm charts offer: consume its buttons, then open the picker."""
    report_id = callbacks.parse_chart_open(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await clear_inline_keyboard(callback)  # consume the offer's Так/Ні
    await callback.answer()
    await open_charts_picker(callback.message, telegram_id=tg, report_id=report_id)


@router.callback_query(F.data.startswith(callbacks.CHART_PAGE + ":"))
async def on_chart_page(callback: CallbackQuery) -> None:
    """Paginate the charts picker in place."""
    parsed = callbacks.parse_chart_page(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    report_id, page = parsed
    await callback.answer()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        items = await history.list_report_pickables(session, user_id=user.id, report_id=report_id)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
    if not items:
        return
    flagged_total, no_dyn = _flagged_summary(report, items)
    text, keyboard = _charts_picker_view(items, report_id, page, flagged_total, no_dyn)
    with contextlib.suppress(TelegramBadRequest):  # ignore "message is not modified"
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def _render_pickable(
    session: AsyncSession,
    *,
    user_id: int,
    report_id: int,
    report: LabReport | None,
    item: history.PickItem,
) -> tuple[bytes, str, str | None] | None:
    """(png, dynamics-caption, specimen) for a picked indicator — a numeric CHART or a qualitative
    TABLE, both PNGs so they share one carousel. None if it can't be rendered. The caption leads
    with the source-report context, same as a chart."""
    if not item.qualitative:
        result = await render_chart_and_summary(
            session,
            user_id=user_id,
            key=item.key,
            title=item.name,
            highlight_date=report.report_date if report else None,
        )
        if result is None:
            return None
        png, summary = result
        spec = specimen(summary.latest.section, summary.analyte) if summary.latest else None
        return png, _chart_caption(report, summary), spec
    qual = await history.qual_trend_by_key(
        session, user_id=user_id, report_id=report_id, key=item.key
    )
    if qual is None:
        return None
    rows = [(m.taken_on.isoformat(), m.text, m.flagged) for m in qual.timeline]
    here = report.report_date.isoformat() if report and report.report_date else None
    png = render_qual_table_png(item.name, rows, highlight_date=here)
    dynamics = history.qual_dynamics_caption(qual)
    if report is not None:
        date_txt, lab = _report_date_lab(report)
        dynamics = f"{locale.CHART_SOURCE_CONTEXT.format(date=date_txt, lab=lab)}\n{dynamics}"
    return png, dynamics, qual.specimen


@router.callback_query(F.data.startswith(callbacks.CHART_PICK + ":"))
async def on_chart_pick(callback: CallbackQuery, state: FSMContext) -> None:
    """Render ONE indicator (the picked button) — a chart, or a table for a qualitative one — with
    carousel nav under it so the next indicator is reachable without scrolling back up."""
    parsed = callbacks.parse_chart_pick(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    report_id, index = parsed
    await callback.answer()
    await _show_uploading(callback.message)
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        items = await history.list_report_pickables(session, user_id=user.id, report_id=report_id)
        if not 0 <= index < len(items):
            return
        item = items[index]
        rendered = await _render_pickable(
            session, user_id=user.id, report_id=report_id, report=report, item=item
        )
    if rendered is not None:
        png, dynamics, spec = rendered
        await _send_chart(
            callback.message,
            png=png,
            dynamics=dynamics,
            analyte=item.name,
            specimen=spec,
            keyboard=_chart_nav_keyboard(report_id, index, len(items)),
        )
        # Now that this indicator is on screen, a free-text question about it (no button tap) is
        # answered IN context (see consult_flow.start_primed_consult).
        await consult_flow.prime_indicator(state, report_id=report_id, key=item.key, name=item.name)


@router.callback_query(F.data.startswith(callbacks.CHART_NAV + ":"))
async def on_chart_nav(callback: CallbackQuery, state: FSMContext) -> None:
    """Carousel: flip to the prev/next indicator by editing the SAME photo in place, so browsing
    many indicators never floods the chat or buries the picker."""
    parsed = callbacks.parse_chart_nav(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    report_id, index = parsed
    await callback.answer()
    await _show_uploading(callback.message)
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        items = await history.list_report_pickables(session, user_id=user.id, report_id=report_id)
        if not 0 <= index < len(items):
            return
        item = items[index]
        rendered = await _render_pickable(
            session, user_id=user.id, report_id=report_id, report=report, item=item
        )
    if rendered is None:
        return
    png, dynamics, spec = rendered
    keyboard = _chart_nav_keyboard(report_id, index, len(items))
    media = InputMediaPhoto(
        media=BufferedInputFile(png, filename=_chart_filename(item.name)), caption=dynamics
    )
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_media(media, reply_markup=keyboard)
    full = await _chart_full_caption(dynamics, item.name, spec)
    if full:
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_caption(caption=full, reply_markup=keyboard)
    # Re-anchor a free-text question to the indicator now showing.
    await consult_flow.prime_indicator(state, report_id=report_id, key=item.key, name=item.name)


@router.callback_query(F.data.startswith(callbacks.CHART_ALL + ":"))
async def on_chart_all(callback: CallbackQuery) -> None:
    """A single scannable TEXT report of every trending analyte (problems first) — instead of
    dumping one chart image per analyte (a flood at 85 indicators). A specific chart is one tap
    away in the picker."""
    report_id = callbacks.parse_chart_all(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.render_dynamics_report(session, user_id=user.id, report_id=report_id)
    if report is None:
        await callback.message.answer(locale.HIST_DYNAMICS_EMPTY)
        return
    await answer_chunked(
        callback.message, render_interpretation_html(report), parse_mode=ParseMode.HTML
    )


def _pdf_caption(dynamics: str, note: str) -> str:
    """One PDF page's description: the deterministic dynamics line, plus the educational note when
    there is one."""
    return f"{dynamics}\n\n{note}" if note else dynamics


_PDF_NOTE_BUDGET_S = 55  # ship the dynamics PDF within this even if claude is slow — notes optional


async def _gather_notes_bounded(items: list[tuple[str, str | None]]) -> list[str]:
    """Generate the per-indicator educational notes concurrently (bounded by the interpret
    concurrency) under an OVERALL time budget. Returns one note per item, in order; anything not
    finished within the budget is "" — its task is cancelled, which makes run_claude kill the child
    process, so a slow / rate-limited claude can never hang the PDF. Never raises."""
    if not items:
        return []
    sem = asyncio.Semaphore(max(1, get_settings().claude_interpret_concurrency))

    async def _note(title: str, spec: str | None) -> str:
        async with sem:
            return await describe_indicator(title, specimen=spec)

    tasks = [asyncio.create_task(_note(title, spec)) for title, spec in items]
    done, pending = await asyncio.wait(tasks, timeout=_PDF_NOTE_BUDGET_S)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)  # let the cancels kill the children
    notes: list[str] = []
    for task in tasks:
        note = ""
        if task in done and not task.cancelled():
            with contextlib.suppress(Exception):
                note = task.result()
        notes.append(note)
    return notes


@router.callback_query(F.data.startswith(callbacks.CHART_PDF + ":"))
async def on_chart_pdf(callback: CallbackQuery) -> None:
    """Build ONE PDF, named for THIS report (date + lab): a cover that honestly explains the
    indicator split, one page per numeric trend chart, then a text-timeline section for the
    qualitative indicators (so 'не виявлені'-type results are not silently dropped). Educational
    notes are generated concurrently (bounded) and cached."""
    report_id = callbacks.parse_chart_pdf(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        data, quals, breakdown = await history.report_dynamics_bundle(
            session, user_id=user.id, report_id=report_id
        )
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
    if not data and not quals:
        await callback.message.answer(locale.CHART_PDF_EMPTY)
        return
    await _render_and_send_pdf(
        callback.message,
        data=data,
        quals=quals,
        cover=_pdf_cover(report, breakdown),
        highlight=report.report_date if report else None,
        filename=_pdf_filename(report),
    )


async def _render_and_send_pdf(
    message: Message,
    *,
    data: list[history.TrendChartData],
    quals: list[history.QualTrend],
    cover: PdfCover,
    highlight: date | None,
    filename: str,
) -> None:
    """Build and send a dynamics PDF (per-report OR cross-lab). EVERY indicator gets a description,
    but a note is data-independent, so it is generated by claude only ONCE EVER and PERSISTED
    (notecache): read the cache, generate only the misses (bounded so a slow claude can't hang it),
    persist them, then assemble. After warmup the PDF needs no claude call — render is ~1s."""
    await message.answer(locale.CHART_PDF_PREPARING)
    all_items = [(d.title, d.specimen) for d in data] + [(q.title, q.specimen) for q in quals]
    keys = [note_cache_key(spec, title) for title, spec in all_items]
    async with get_session() as session:
        cached = await notecache.fetch_cached(session, keys)
    missing = [pair for pair, key in zip(all_items, keys, strict=True) if key not in cached]
    fresh = await _gather_notes_bounded(missing)
    fresh_map = {
        note_cache_key(spec, title): note
        for (title, spec), note in zip(missing, fresh, strict=True)
    }
    if fresh_map:
        async with get_session() as session:
            await notecache.store_many(session, fresh_map)
            await session.commit()

    def _note_for(title: str, spec: str | None) -> str:
        key = note_cache_key(spec, title)
        return cached.get(key) or fresh_map.get(key, "")

    pages = [
        PdfChart(
            title=d.title,
            subtitle=d.category,
            points=d.points,
            caption=_pdf_caption(d.dynamics, _note_for(d.title, d.specimen)),
            highlight_date=highlight,
        )
        for d in data
    ]
    highlight_iso = highlight.isoformat() if highlight else ""
    qual_pages = tuple(
        PdfQualTrend(
            title=q.title,
            subtitle=q.category,
            rows=tuple((m.taken_on.isoformat(), m.text, m.flagged) for m in q.timeline),
            note=_note_for(q.title, q.specimen),
            changed=q.changed,
            highlight_date=highlight_iso,
        )
        for q in quals
    )
    pdf = await asyncio.to_thread(render_trends_pdf, pages, cover=cover, qual_trends=qual_pages)
    await message.answer_document(BufferedInputFile(pdf, filename=filename))


def _pdf_cover(report: LabReport | None, breakdown: history.ReportBreakdown) -> PdfCover:
    """A clear "what is inside" cover: total count + a plain breakdown of HOW the dynamics are shown
    (graphs for numeric, tables for qualitative, plus any single-measurement ones). The category
    split is a secondary line only when the report spans more than one category."""
    date_txt, lab = _report_date_lab(report)
    rows = [locale.CHART_PDF_ON_CHARTS.format(n=breakdown.numeric)]
    if breakdown.qualitative:
        rows.append(locale.CHART_PDF_IN_TABLES.format(n=breakdown.qualitative))
    if breakdown.single:
        rows.append(locale.CHART_PDF_SINGLE_LINE.format(n=breakdown.single))
    notes: list[str] = []
    if len(breakdown.categories) > 1:  # which kinds of analysis, only when there is more than one
        names = " · ".join(
            locale.CHART_PDF_CATEGORY_LABELS.get(key, key) for key, _ in breakdown.categories
        )
        notes.append(locale.CHART_PDF_SECTIONS.format(names=names))
    return PdfCover(
        heading=locale.CHART_PDF_HEADING,
        report_line=locale.CHART_PDF_REPORT_LINE.format(date=date_txt, lab=lab),
        summary_line=locale.CHART_PDF_INTRO.format(n=breakdown.total),
        category_rows=tuple(rows),
        notes=tuple(notes),
    )


# --- Dynamics browser: indicators grouped by clinical category, across all labs ------

_DYN_PAGE_SIZE = 10


def _pair_rows(buttons: list[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]


def _dyn_home_view(counts: list[tuple[str, int]]) -> tuple[str, InlineKeyboardMarkup]:
    buttons = [
        _btn(f"{locale.CATEGORY_NAMES.get(cat, cat)} ({n})", callbacks.dyn_category(cat))
        for cat, n in counts
    ]
    rows = _pair_rows(buttons)
    rows.append([_btn(locale.BTN_HIST_BACK, callbacks.MENU_OPEN_LABS)])  # back to the "Аналізи" hub
    return locale.DYN_HEADER, InlineKeyboardMarkup(inline_keyboard=rows)


def _dyn_pdf_cover(category: str, breakdown: history.ReportBreakdown) -> PdfCover:
    """Cover for a SINGLE-category PDF: the category name + how its dynamics are shown (charts for
    numeric, tables for qualitative). One document per category, never one giant file."""
    name = locale.CHART_PDF_CATEGORY_LABELS.get(
        category, locale.CATEGORY_NAMES.get(category, category)
    )
    rows = [locale.CHART_PDF_ON_CHARTS.format(n=breakdown.numeric)]
    if breakdown.qualitative:
        rows.append(locale.CHART_PDF_IN_TABLES.format(n=breakdown.qualitative))
    return PdfCover(
        heading=locale.CHART_PDF_CATEGORY_HEADING.format(category=name),
        report_line=locale.CHART_PDF_CATEGORY_SUBTITLE,
        summary_line=locale.CHART_PDF_INTRO.format(n=breakdown.total),
        category_rows=tuple(rows),
        notes=(),
    )


def _dyn_pdf_filename(category: str) -> str:
    """'Дбайло-динаміка-<категорія>.pdf' — the file says which category it is (emoji-free slug)."""
    slug = _safe_filename(locale.CATEGORY_SHORT.get(category, category))
    return _safe_filename(locale.CHART_PDF_CATEGORY_FILENAME.format(category=slug))


@router.callback_query(F.data.startswith(callbacks.DYN_PDF + ":"))
async def on_dyn_pdf(callback: CallbackQuery) -> None:
    """One PDF for a SINGLE category's dynamics (charts + tables) — not one giant cross-category
    file, which was unwieldy to read. The category key rides on the callback."""
    category = callbacks.parse_dyn_pdf(callback.data or "")
    tg = _telegram_id(callback)
    if category is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        data, quals, breakdown = await history.all_dynamics_bundle(
            session, user_id=user.id, category=category
        )
    if not data and not quals:
        await callback.message.answer(locale.CHART_PDF_EMPTY)
        return
    await _render_and_send_pdf(
        callback.message,
        data=data,
        quals=quals,
        cover=_dyn_pdf_cover(category, breakdown),
        highlight=None,
        filename=_dyn_pdf_filename(category),
    )


def _dyn_category_view(
    category: str, indicators: list[history.IndicatorItem], page: int
) -> tuple[str, InlineKeyboardMarkup]:
    pages = max(1, -(-len(indicators) // _DYN_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    start = page * _DYN_PAGE_SIZE
    buttons: list[InlineKeyboardButton] = []
    for offset, it in enumerate(indicators[start : start + _DYN_PAGE_SIZE]):
        prefix = (
            locale.CHART_FLAGGED_PREFIX
            if it.last_flagged
            else (locale.DYN_TREND_PREFIX if it.has_trend else "")
        )
        buttons.append(
            _btn(f"{prefix}{it.name}", callbacks.dyn_indicator(category, start + offset))
        )
    rows = _pair_rows(buttons)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn(locale.BTN_HIST_PREV, callbacks.dyn_category(category, page - 1)))
    if page < pages - 1:
        nav.append(_btn(locale.BTN_HIST_NEXT, callbacks.dyn_category(category, page + 1)))
    if nav:
        rows.append(nav)
    rows.append(
        [_btn(locale.BTN_DYN_PDF, callbacks.dyn_pdf(category))]
    )  # PDF of just this category
    rows.append([_btn(locale.DYN_BTN_BACK, callbacks.DYN_HOME)])
    header = locale.DYN_CATEGORY_HEADER.format(
        category=locale.CATEGORY_NAMES.get(category, category)
    )
    if pages > 1:
        header += " · " + locale.HIST_PAGE_LABEL.format(page=page + 1, pages=pages)
    return header, InlineKeyboardMarkup(inline_keyboard=rows)


def _dyn_imaging_view(narratives: list[LabReport]) -> tuple[str, InlineKeyboardMarkup]:
    rows: list[list[InlineKeyboardButton]] = []
    for r in narratives:
        date_txt = r.report_date.isoformat() if r.report_date else locale.HIST_NO_DATE
        rtype = history.short_type(r.report_type)  # keep the long study name button on one line
        rows.append([_btn(f"📄 {date_txt} · {rtype}", callbacks.history_results(r.id))])
    rows.append([_btn(locale.DYN_BTN_BACK, callbacks.DYN_HOME)])
    return locale.DYN_IMAGING_HEADER, InlineKeyboardMarkup(inline_keyboard=rows)


async def _dyn_home(telegram_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        items = await history.aggregate_indicators(session, user_id=user.id)
        narratives = await history.list_narratives(session, user_id=user.id)
    counts = history.category_counts(items, len(narratives))
    return _dyn_home_view(counts) if counts else None


async def render_dynamics(message: Message, telegram_id: int) -> None:
    """Open the dynamics browser (category list) — from /dynamics or the /history button."""
    view = await _dyn_home(telegram_id)
    if view is None:
        await message.answer(locale.DYN_EMPTY)
        return
    text, keyboard = view
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("dynamics"))
async def cmd_dynamics(message: Message) -> None:
    tg = _telegram_id(message)
    if tg is not None:
        await render_dynamics(message, tg)


async def _edit_to_dyn_home(callback: CallbackQuery, telegram_id: int) -> None:
    """Edit the current message into the dynamics-browser home (category list). Shared by the
    'Аналізи' hub entry and the ◀ Категорії back button — edit-in-place, so ◀ Назад can return to
    the hub in the same message. The empty state still offers a way back to the hub."""
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    view = await _dyn_home(telegram_id)
    if view is None:
        back = InlineKeyboardMarkup(
            inline_keyboard=[[_btn(locale.BTN_HIST_BACK, callbacks.MENU_OPEN_LABS)]]
        )
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(locale.DYN_EMPTY, reply_markup=back)
        return
    text, keyboard = view
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(F.data == callbacks.DYN_OPEN)
async def on_dyn_open(callback: CallbackQuery) -> None:
    """Open the dynamics browser from the 'Аналізи' hub — edits the hub message in place, so the
    home's ◀ Назад returns to the hub (same one-message master-detail as the report list)."""
    tg = _telegram_id(callback)
    if tg is None:
        await callback.answer()
        return
    await _edit_to_dyn_home(callback, tg)


@router.callback_query(F.data == callbacks.DYN_HOME)
async def on_dyn_home(callback: CallbackQuery) -> None:
    tg = _telegram_id(callback)
    if tg is None:
        await callback.answer()
        return
    await _edit_to_dyn_home(callback, tg)


@router.callback_query(F.data.startswith(callbacks.DYN_CAT + ":"))
async def on_dyn_category(callback: CallbackQuery) -> None:
    parsed = callbacks.parse_dyn_category(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    category, page = parsed
    await callback.answer()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        if category == grouping.IMAGING:
            text, keyboard = _dyn_imaging_view(
                await history.list_narratives(session, user_id=user.id)
            )
        else:
            items = await history.aggregate_indicators(session, user_id=user.id)
            text, keyboard = _dyn_category_view(
                category, history.indicators_in(items, category), page
            )
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith(callbacks.DYN_IND + ":"))
async def on_dyn_indicator(callback: CallbackQuery, state: FSMContext) -> None:
    """Show one indicator's trend chart (the analyte's dynamics across all labs)."""
    parsed = callbacks.parse_dyn_indicator(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    category, index = parsed
    await callback.answer()
    await _show_uploading(callback.message)
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        items = await history.aggregate_indicators(session, user_id=user.id)
        indicators = history.indicators_in(items, category)
        if not 0 <= index < len(indicators):
            return
        indicator = indicators[index]
        view = await history.trend_for_analyte(session, user_id=user.id, analyte=indicator.name)
    if view.chart is not None:
        consult_kb = InlineKeyboardMarkup(
            inline_keyboard=[[_btn(locale.BTN_CONSULT, callbacks.consult_dyn(category, index))]]
        )
        await _send_chart(
            callback.message,
            png=view.chart,
            dynamics=view.text,
            analyte=view.analyte,
            specimen=view.specimen,
            keyboard=consult_kb,
        )
        # A free-text question right after is answered about this indicator (no button tap needed).
        await consult_flow.prime_indicator(
            state, report_id=0, key=indicator.key, name=indicator.name
        )
    else:
        await callback.message.answer(view.text)


# --- Expert reading: cached (instant), with refresh / delete --------------------


# Section key -> its drill-down button label (the overview/overall button reads "🩺 Огляд").
_ANALYSIS_LABELS: dict[str, str] = {
    "overall": locale.BTN_ANALYSIS_OVERVIEW,
    "attention": locale.BTN_ANALYSIS_ATTENTION,
    "help": locale.BTN_ANALYSIS_HELP,
    "doctor": locale.BTN_ANALYSIS_DOCTOR,
}


def _refresh_delete_row(report_id: int) -> list[InlineKeyboardButton]:
    return [
        _btn(locale.BTN_INTERP_REFRESH, callbacks.history_interpret_refresh(report_id)),
        _btn(locale.BTN_INTERP_DELETE, callbacks.history_interpret_del(report_id)),
    ]


def _analysis_actions(report_id: int) -> InlineKeyboardMarkup:
    """Fallback keyboard (narrative / deterministic reading whole): refresh / delete + consult."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _refresh_delete_row(report_id),
            [_btn(locale.BTN_CONSULT, callbacks.consult_report(report_id))],
        ]
    )


def _analysis_keyboard(
    report_id: int, sections: dict[str, str], idx: int, *, back_page: int | None = None
) -> InlineKeyboardMarkup:
    """Drill-down navigation. On the overview (idx 0): a button for each OTHER present section
    plus the refresh / delete row. On a section (idx >= 1): '🩺 Огляд' first, then the other
    sections — so you hop between sections without scrolling back. Buttons two per row. When
    ``back_page`` is given (the /history flow), a '◀ Назад' returns to the report card so you are
    never stranded in a section with no way back."""
    current_key = SECTION_KEYS[idx]
    nav = [
        _btn(_ANALYSIS_LABELS[key], callbacks.history_interpret_view(report_id, target))
        for target, key in enumerate(SECTION_KEYS)
        if key in sections and key != current_key
    ]
    rows = [nav[i : i + 2] for i in range(0, len(nav), 2)]
    if idx == 0:
        rows.append(_refresh_delete_row(report_id))
    # Ask Дбайло about THIS section of the reading (Загалом / Звернути увагу / Що допоможе / лікар),
    # so the consultation is centred on the aspect the user is reading right now.
    rows.append([_btn(locale.BTN_CONSULT, callbacks.consult_section(report_id, idx))])
    if back_page is not None:
        rows.append([_btn(locale.BTN_HIST_BACK, callbacks.history_open(report_id, back_page))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render_analysis_view(
    summary_text: str, report_id: int, idx: int, *, back_page: int | None = None
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Render one section (idx 0 = overview/Загалом) + its nav keyboard, or None when the text
    is not the canonical 4-section shape (caller then sends it whole)."""
    sections = split_interpretation(summary_text)
    if "overall" not in sections:
        return None
    key = SECTION_KEYS[idx] if SECTION_KEYS[idx] in sections else "overall"
    idx = SECTION_KEYS.index(key)
    keyboard = _analysis_keyboard(report_id, sections, idx, back_page=back_page)
    return render_interpretation_html(sections[key]), keyboard


async def _show_view(
    message: Message, text: str, keyboard: InlineKeyboardMarkup, *, edit: bool
) -> None:
    """Render a drill-down view in place (master-detail, no message spam) when ``edit`` is set,
    falling back to a fresh chunked message when the edit can't apply (content too long / the
    message isn't editable)."""
    if edit:
        try:
            await message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            return
        except TelegramBadRequest:
            pass  # too long / not a text message — send a fresh one below
    await answer_chunked(message, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def send_analysis(
    message: Message,
    summary_text: str,
    report_id: int,
    *,
    back_page: int | None = None,
    edit: bool = False,
) -> None:
    """Deliver the analysis as a navigable overview (Загалом + per-section buttons). In the
    /history flow it edits the card message in place and carries a '◀ Назад' to the card
    (``back_page`` set, ``edit`` true); the post-confirm flow sends it fresh with no card to
    return to. A reading without the canonical sections falls back to the whole text."""
    view = _render_analysis_view(summary_text, report_id, 0, back_page=back_page)
    if view is None:
        await answer_chunked(
            message,
            render_interpretation_html(summary_text),
            reply_markup=_analysis_actions(report_id),
            parse_mode=ParseMode.HTML,
        )
        return
    text, keyboard = view
    await _show_view(message, text, keyboard, edit=edit)


async def _generate_analysis(
    message: Message,
    *,
    report_id: int,
    telegram_id: int,
    back_page: int | None = None,
    edit: bool = False,
) -> None:
    # In the /history flow we own the card message: edit it to a working note, then to the result,
    # so generation stays in ONE message instead of stacking new ones.
    if edit:
        with contextlib.suppress(TelegramBadRequest):
            await message.edit_text(locale.LAB_INTERPRET_WORKING)
    else:
        await message.answer(locale.LAB_INTERPRET_WORKING)
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=telegram_id)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        if report is None:
            await message.answer(locale.HIST_FILE_GONE)
            return
        results = history.ordered_results(report)
        reconstructed = history.reconstruct_report(report, results)
        keys = {series_key(r.section, r.analyte) for r in results}
        report.summary = history.SUMMARY_PENDING  # mark pending BEFORE the slow LLM call
        await session.commit()  # ... and persist it, so a restart mid-run is recoverable
        summary = await compute_report_summary(
            session, user_id=user.id, analyte_keys=keys, report=reconstructed
        )
        report.summary = summary.text
        await session.commit()
    await send_analysis(message, summary.text, report_id, back_page=back_page, edit=edit)


@router.callback_query(F.data.startswith(callbacks.HIST_INTERPRET + ":"))
async def on_history_interpret(callback: CallbackQuery) -> None:
    """Show the saved analysis INSTANTLY if it exists; otherwise generate it once and store it."""
    report_id = callbacks.parse_history_interpret(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        cached = report.summary if report is not None else None
        gone = report is None
    if gone:
        await callback.message.answer(locale.HIST_FILE_GONE)
        return
    if cached:  # instant, from the DB — edit the card in place so '◀ Назад' returns to it
        await send_analysis(callback.message, cached, report_id, back_page=0, edit=True)
    else:
        await _generate_analysis(
            callback.message, report_id=report_id, telegram_id=tg, back_page=0, edit=True
        )


@router.callback_query(F.data.startswith(callbacks.HIST_INTERP_VIEW + ":"))
async def on_history_interpret_view(callback: CallbackQuery) -> None:
    """Drill-down: pull ONE section of the stored analysis (idx 0 = overview). Stateless —
    the section content is re-derived from the stored summary, so it survives a restart."""
    parsed = callbacks.parse_history_interpret_view(callback.data or "")
    tg = _telegram_id(callback)
    if parsed is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    report_id, idx = parsed
    await callback.answer()
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        summary = report.summary if report is not None else None
    if not summary:
        await callback.message.answer(locale.HIST_FILE_GONE)
        return
    view = _render_analysis_view(summary, report_id, idx, back_page=0)
    if view is None:  # summary lost its canonical shape (e.g. regenerated as a fallback)
        await answer_chunked(
            callback.message, render_interpretation_html(summary), parse_mode=ParseMode.HTML
        )
        return
    text, keyboard = view
    await _show_view(callback.message, text, keyboard, edit=True)  # hop sections in place


@router.callback_query(F.data.startswith(callbacks.HIST_INTERP_REFRESH + ":"))
async def on_history_interpret_refresh(callback: CallbackQuery) -> None:
    report_id = callbacks.parse_history_interpret_refresh(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await clear_inline_keyboard(callback)  # consume the old refresh/delete row
    await callback.answer()
    await _generate_analysis(
        callback.message, report_id=report_id, telegram_id=tg, back_page=0, edit=True
    )


@router.callback_query(F.data.startswith(callbacks.HIST_INTERP_DEL + ":"))
async def on_history_interpret_del(callback: CallbackQuery) -> None:
    report_id = callbacks.parse_history_interpret_del(callback.data or "")
    tg = _telegram_id(callback)
    if report_id is None or tg is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await clear_inline_keyboard(callback)
    async with get_session() as session:
        user = await ensure_user(session, telegram_id=tg)
        report = await history.get_report(session, report_id=report_id, user_id=user.id)
        if report is not None:
            report.summary = None
            await session.commit()
    await callback.message.answer(locale.HIST_INTERP_DELETED)
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
