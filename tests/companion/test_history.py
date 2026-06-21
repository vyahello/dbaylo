"""Tier 1.2 — history & retrieval (deterministic logic).

Covers the NL parser/router (incl. the addition-B "no concrete filter -> companion"
boundary), listing/filtering, rendering, single-analyte trends, delete with Tier 1.1
coupling cleanup, original-file removal, and the orphaned-upload cleanup.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo import locale
from dbaylo.bot import history_flow
from dbaylo.bot.formatting import render_interpretation_html
from dbaylo.companion import callbacks, grouping, history, proactive
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.db.models import (
    Condition,
    ConditionStatus,
    LabReport,
    LabResult,
    Reminder,
    ReportKind,
    ReportStatus,
    User,
)
from dbaylo.labs.trends import compute_flag, is_out_of_range, series_key
from dbaylo.triage.safety import DISCLAIMER

TZ = ZoneInfo("Europe/Kyiv")


# --- Seed helpers ---------------------------------------------------------------


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=777, name="Test")
    session.add(user)
    await session.flush()
    return user


async def _report(
    session: AsyncSession,
    *,
    user_id: int,
    on: date | None,
    lab: str | None,
    status: ReportStatus = ReportStatus.CONFIRMED,
    results: tuple[tuple[str, float | None, float | None, float | None], ...] = (),
    source_file: str | None = None,
    created_at: datetime | None = None,
) -> LabReport:
    # Build results through the relationship so the collection is populated in memory
    # (async sessions cannot lazy-load it later).
    report = LabReport(
        user_id=user_id,
        report_date=on,
        lab=lab,
        status=status,
        source_file=source_file,
        results=[
            # Store flag + flagged like persist_confirmed does — history reads them.
            LabResult(
                analyte=name,
                value=value,
                ref_low=low,
                ref_high=high,
                flag=compute_flag(value, low, high),
                flagged=is_out_of_range(value, low, high, None),
            )
            for name, value, low, high in results
        ],
    )
    if created_at is not None:
        report.created_at = created_at
    session.add(report)
    await session.flush()
    return report


async def _sender(telegram_id: int, text: str, *, buttons: object | None = None) -> None:
    return None


@pytest_asyncio.fixture
async def scheduler(async_session: AsyncSession) -> AsyncIterator[ReminderScheduler]:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield async_session

    rs = ReminderScheduler(sender=_sender, session_factory=factory, tz=TZ)
    await rs.start()
    yield rs
    rs.shutdown()


# --- NL parser + routing --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "year", "month", "day"),
    [
        ("покажи аналізи 2026-05-12", 2026, 5, 12),
        ("аналізи за 2026-05", 2026, 5, None),
        ("що було у 2025 році", 2025, None, None),
        ("результати за травень", None, 5, None),
        ("звіти за грудень", None, 12, None),
    ],
)
def test_parse_history_query_dates(text, year, month, day) -> None:
    filt = history.parse_history_query(text)
    assert (filt.year, filt.month, filt.day) == (year, month, day)


def test_parse_history_query_lab_keyword_and_known_lab() -> None:
    assert history.parse_history_query("аналізи synevo").lab == "synevo"
    # A real lab name not in the keyword list is matched via known_labs.
    filt = history.parse_history_query("аналізи Медіс за травень", known_labs=("Медіс",))
    assert filt.lab == "Медіс" and filt.month == 5


def test_parse_history_query_latest() -> None:
    assert history.parse_history_query("покажи останній аналіз").latest is True


@pytest.mark.parametrize(
    ("text", "is_query"),
    [
        ("покажи аналізи за травень", True),  # intent + concrete
        ("synevo", True),  # lab keyword alone is concrete
        ("останній аналіз", True),
        ("покажи мої аналізи", False),  # intent, NO concrete token -> companion (addition B)
        ("динаміка ваги", False),  # intent word, but no analyte/date token
        ("як справи?", False),  # neither
        ("дякую, ти супер", False),
    ],
)
def test_is_history_query_routing(text, is_query) -> None:
    assert history.is_history_query(text) is is_query


def test_intent_without_concrete_token_yields_empty_filter() -> None:
    # The addition-B contract: routing only fires when a concrete filter survives.
    assert not history.parse_history_query("покажи мої аналізи").has_filter


# --- Listing + filtering --------------------------------------------------------


async def test_list_confirmed_recent_first_and_excludes_pending(
    async_session: AsyncSession,
) -> None:
    user = await _user(async_session)
    await _report(async_session, user_id=user.id, on=date(2026, 1, 10), lab="A")
    await _report(async_session, user_id=user.id, on=date(2026, 5, 20), lab="B")
    await _report(
        async_session, user_id=user.id, on=date(2026, 6, 1), lab="C", status=ReportStatus.PENDING
    )
    reports = await history.list_confirmed(async_session, user_id=user.id)
    assert [r.lab for r in reports] == ["B", "A"]  # recent first, no PENDING


async def test_list_confirmed_filters(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    await _report(async_session, user_id=user.id, on=date(2026, 5, 2), lab="Synevo")
    await _report(async_session, user_id=user.id, on=date(2026, 5, 20), lab="Dila")
    await _report(async_session, user_id=user.id, on=date(2025, 5, 9), lab="Synevo")

    by_lab = await history.list_confirmed(
        async_session, user_id=user.id, filt=history.HistoryFilter(lab="synevo")
    )
    assert {r.lab for r in by_lab} == {"Synevo"} and len(by_lab) == 2

    by_month = await history.list_confirmed(
        async_session, user_id=user.id, filt=history.HistoryFilter(year=2026, month=5)
    )
    assert len(by_month) == 2

    latest = await history.list_confirmed(
        async_session, user_id=user.id, filt=history.HistoryFilter(latest=True)
    )
    assert len(latest) == 1 and latest[0].report_date == date(2026, 5, 20)


async def test_list_confirmed_returns_one_over_limit_for_more(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    for i in range(12):
        await _report(async_session, user_id=user.id, on=date(2026, 1, i + 1), lab=f"L{i}")
    reports = await history.list_confirmed(async_session, user_id=user.id, limit=10)
    assert len(reports) == 11  # limit + 1 sentinel so the caller can show "more"


async def test_known_labs(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    await _report(async_session, user_id=user.id, on=date(2026, 1, 1), lab="Synevo")
    await _report(async_session, user_id=user.id, on=date(2026, 2, 1), lab="Synevo")
    await _report(async_session, user_id=user.id, on=date(2026, 3, 1), lab=None)
    # known_labs canonicalizes + dedupes, so "Synevo" surfaces as the printed "Сінево".
    assert await history.known_labs(async_session, user_id=user.id) == ("Сінево",)


# --- Rendering ------------------------------------------------------------------


async def test_render_report_line_flags_and_results(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    report = await _report(
        async_session,
        user_id=user.id,
        on=date(2026, 5, 12),
        lab="Synevo",
        results=(("Глюкоза", 7.0, 3.9, 6.1), ("Калій", 4.0, 3.5, 5.1)),
    )
    results = history.ordered_results(report)
    line = history.render_report_line(report, results)
    assert "2026-05-12" in line and "Сінево" in line  # "Synevo" canonicalized on render
    assert "2 показників" in line and "⚠️" in line  # one out-of-range analyte

    body = history.render_report_results(report, results)
    assert "1. Глюкоза" in body and "2. Калій" in body
    assert "норма" in body


async def test_report_button_label_and_card_show_flag_count(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    report = await _report(
        async_session,
        user_id=user.id,
        on=date(2021, 8, 6),
        lab="Сінево",
        results=(("АЛТ", 63.0, None, 50.0), ("Калій", 4.0, 3.5, 5.1)),  # АЛТ out of range
    )
    results = history.ordered_results(report)
    label = history.report_button_label(report, results)
    assert "Сінево" in label and "⚠️1" in label  # one flagged, shown on the button
    assert "⚠️ 1 поза нормою" in history.render_card(report, results)


def test_short_type_truncates_long_study_names() -> None:
    assert history.short_type("МРТ головного мозку") == "МРТ головного мозку"  # short -> unchanged
    out = history.short_type("КТ сечовивідної системи з внутрішньовенним контрастуванням")
    assert out.endswith("…") and len(out) <= 27
    assert out == "КТ сечовивідної системи…"  # cut on a WORD boundary, never mid-word
    assert not out.rstrip("…").endswith(" ")  # no dangling space before the ellipsis


async def test_render_problems_lists_few_in_range_by_name(
    async_session: AsyncSession,
) -> None:
    user = await _user(async_session)
    report = await _report(
        async_session,
        user_id=user.id,
        on=date(2021, 8, 6),
        lab="Сінево",
        results=(("АЛТ", 63.0, None, 50.0), ("Калій", 4.0, 3.5, 5.1), ("Глюкоза", 5.0, 3.9, 6.1)),
    )
    body = history.render_problems(report, history.ordered_results(report))
    assert "АЛТ" in body  # the out-of-range row (with ⚠️)
    assert "Калій" in body and "Глюкоза" in body  # only a couple in range -> listed by name
    assert "У межах норми" in body
    assert DISCLAIMER in body  # disclaimer on every health view (rendered as the italic P.S.)

    # The send layer turns it into the premium HTML: a bold title + the consistent italic P.S.
    html = render_interpretation_html(body)
    assert "<b>🔬 2021-08-06 · Сінево</b>" in html  # bold one-line title
    assert f"P.S. <i>{DISCLAIMER}</i>" in html  # one consistent italic P.S. everywhere


async def test_render_problems_collapses_many_in_range(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    results = (("АЛТ", 63.0, None, 50.0),) + tuple(
        (f"N{i}", 4.0, 3.5, 5.1) for i in range(6)
    )  # 1 flagged + 6 in range
    report = await _report(
        async_session, user_id=user.id, on=date(2021, 8, 6), lab="A", results=results
    )
    body = history.render_problems(report, history.ordered_results(report))
    assert "АЛТ" in body
    assert "Решта 6" in body  # too many in-range rows -> aggregated
    assert "N0" not in body


async def test_full_table_has_ps_and_omits_the_summary(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    report = await _report(
        async_session,
        user_id=user.id,
        on=date(2021, 8, 6),
        lab="Сінево",
        results=(("Глюкоза", 5.0, 3.9, 6.1),),
    )
    report.summary = "СЕКРЕТНИЙ РОЗБІР"  # the analysis is a SEPARATE view now
    body = history.render_report_results(report, history.ordered_results(report))
    assert "Глюкоза" in body and DISCLAIMER in body
    assert "СЕКРЕТНИЙ РОЗБІР" not in body
    assert "СЕКРЕТНИЙ РОЗБІР" not in body


async def test_list_view_paginates_into_pages_of_eight(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    for day in range(1, 13):  # 12 confirmed reports
        await _report(
            async_session,
            user_id=user.id,
            on=date(2021, 1, day),
            lab="Сінево",
            results=(("Глюкоза", 5.0, 3.9, 6.1),),
        )
    reports = await history.list_confirmed(async_session, user_id=user.id, limit=None)
    assert len(reports) == 12
    text, kb = history_flow._list_view(reports, 0, orphans=0)
    open_buttons = [
        row[0]
        for row in kb.inline_keyboard
        if len(row) == 1 and (row[0].callback_data or "").startswith("hist_open")
    ]
    assert len(open_buttons) == 8  # one page
    assert "сторінка 1 з 2" in text  # 12 reports / 8 -> 2 pages
    all_btns = [b for row in kb.inline_keyboard for b in row]
    assert any(b.text == locale.BTN_HIST_NEXT for b in all_btns)  # ▶ on page 0
    assert any(b.callback_data == callbacks.DYN_OPEN for b in all_btns)  # dynamics entry present


async def test_reconstruct_report_rebuilds_an_extracted_report(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    report = LabReport(
        user_id=user.id,
        report_date=date(2026, 5, 12),
        lab="Synevo",
        status=ReportStatus.CONFIRMED,
        conclusion="висновок",
        results=[
            LabResult(analyte="АЛТ", value=63.0, unit="Од/л", ref_high=50.0, flagged=True),
            LabResult(analyte="Калій", value=4.0, ref_low=3.5, ref_high=5.1, flagged=False),
        ],
    )
    async_session.add(report)
    await async_session.flush()

    extracted = history.reconstruct_report(report, history.ordered_results(report))
    assert extracted.lab == "Сінево" and extracted.conclusion == "висновок"
    assert len(extracted.results) == 2
    alt = extracted.results[0]
    assert alt.analyte == "АЛТ" and alt.value == 63.0
    assert alt.out_of_range is True  # the stored 'flagged' becomes the lab's out-of-range mark
    assert [a.analyte for a in extracted.flagged_results()] == ["АЛТ"]


async def test_render_report_results_groups_by_panel(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    report = LabReport(
        user_id=user.id,
        report_date=date(2026, 5, 12),
        lab="Synevo",
        status=ReportStatus.CONFIRMED,
        results=[
            LabResult(analyte="Глюкоза", value=5.3, section="Аналіз крові"),
            LabResult(analyte="Лейкоцити", value=7.3, section="Аналіз крові"),
            LabResult(analyte="Глюкоза", value=None, section="Аналіз сечі"),
        ],
    )
    async_session.add(report)
    await async_session.flush()
    body = history.render_report_results(report, history.ordered_results(report))
    assert "▸ Аналіз крові" in body and "▸ Аналіз сечі" in body
    assert body.index("▸ Аналіз крові") < body.index("▸ Аналіз сечі")


async def test_narrative_results_lead_with_type_split_findings_and_omit_unknown_lab(
    async_session: AsyncSession,
) -> None:
    user = await _user(async_session)
    report = LabReport(
        user_id=user.id,
        report_date=date(2023, 11, 4),
        lab=None,  # an imaging doc often has no lab name
        status=ReportStatus.CONFIRMED,
        kind=ReportKind.NARRATIVE,
        report_type="МРТ головного мозку",
        narrative="Вогнищевих змін не виявлено. Шлуночки не розширені.",
        conclusion="Без патології.",
        results=[],  # populate the collection in memory (no async lazy-load)
    )
    async_session.add(report)
    await async_session.flush()

    body = history.render_problems(report, history.ordered_results(report))
    assert locale.LAB_LAB_UNKNOWN not in body  # no bare "невідома" for a doc without a lab
    assert "МРТ головного мозку" in body  # the title leads with the study type

    full = history.render_report_results(report, history.ordered_results(report))
    # The findings wall is split into one sentence per line (scannable, not a paragraph).
    assert "Вогнищевих змін не виявлено." in full.splitlines()
    assert "Шлуночки не розширені." in full.splitlines()
    assert locale.LAB_LAB_UNKNOWN not in full


async def test_render_report_line_no_date(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    report = await _report(async_session, user_id=user.id, on=None, lab=None)
    line = history.render_report_line(report, [])
    assert history.locale.HIST_NO_DATE in line
    assert history.locale.LAB_LAB_UNKNOWN in line


# --- Trends ---------------------------------------------------------------------


async def test_trend_insufficient_single_point(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    await _report(
        async_session,
        user_id=user.id,
        on=date(2026, 5, 1),
        lab="A",
        results=(("Глюкоза", 5.0, 3.9, 6.1),),
    )
    view = await history.trend_for_analyte(async_session, user_id=user.id, analyte="глюкоза")
    assert view.found and view.chart is None
    assert history.locale.TREND_INSUFFICIENT in view.text


async def test_trend_two_points_renders_chart(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    await _report(
        async_session,
        user_id=user.id,
        on=date(2026, 1, 1),
        lab="A",
        results=(("Глюкоза", 7.5, 3.9, 6.1),),
    )
    await _report(
        async_session,
        user_id=user.id,
        on=date(2026, 5, 1),
        lab="A",
        results=(("Глюкоза", 5.0, 3.9, 6.1),),
    )
    view = await history.trend_for_analyte(async_session, user_id=user.id, analyte="Глюкоза")
    assert view.found and view.chart is not None and view.chart[:4] == b"\x89PNG"
    assert "Глюкоза" in view.text


async def test_find_interrupted_analyses_only_pending(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    pending = await _report(async_session, user_id=user.id, on=date(2021, 1, 1), lab="A")
    pending.summary = history.SUMMARY_PENDING  # "" — analysis started, never finished (restart)
    done = await _report(async_session, user_id=user.id, on=date(2021, 1, 2), lab="A")
    done.summary = "готовий розбір"  # finished
    await _report(async_session, user_id=user.id, on=date(2021, 1, 3), lab="A")  # summary NULL
    await async_session.flush()
    found = await history.find_interrupted_analyses(async_session)
    assert [r.id for r in found] == [pending.id]  # only the empty-summary (interrupted) one


async def test_aggregate_indicators_groups_by_category(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    # Each analyte on TWO dates (the dynamics browser only lists analytes with a numeric trend).
    await _report(
        async_session,
        user_id=user.id,
        on=date(2021, 1, 1),
        lab="Synevo",
        results=(("Гемоглобін", 140.0, 130.0, 160.0), ("Натрій", 140.0, 132.0, 146.0)),
    )
    await _report(
        async_session,
        user_id=user.id,
        on=date(2021, 3, 1),
        lab="Synevo",
        results=(("Гемоглобін", 145.0, 130.0, 160.0), ("Натрій", 150.0, 132.0, 146.0)),  # 150 > 146
    )
    items = await history.aggregate_indicators(async_session, user_id=user.id)
    by_key = {it.key: it for it in items}
    hb, na = series_key(None, "Гемоглобін"), series_key(None, "Натрій")
    assert by_key[hb].category == grouping.BLOOD and by_key[hb].has_trend
    assert by_key[na].category == grouping.BIOCHEM  # name-based (no section in the fixture)
    assert by_key[na].last_flagged  # latest value out of range

    counts = dict(history.category_counts(items, 0))
    assert counts[grouping.BLOOD] == 1 and counts[grouping.BIOCHEM] == 1
    # Flagged sorts first within a category; here biochem has just Натрій.
    assert [it.name for it in history.indicators_in(items, grouping.BIOCHEM)] == ["Натрій"]


async def test_aggregate_indicators_skips_qualitative_analytes(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    # A qualitative urine analyte (no numeric value) on two dates -> 0 numeric -> not chartable,
    # so it must NOT appear in the dynamics browser (tapping it would only say "замало даних").
    for d in (date(2021, 1, 1), date(2021, 2, 1)):
        await _report(
            async_session,
            user_id=user.id,
            on=d,
            lab="A",
            results=(("Кристали оксалату", None, None, None),),
        )
    assert await history.aggregate_indicators(async_session, user_id=user.id) == []


async def test_category_counts_includes_imaging_for_narratives(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    async_session.add(
        LabReport(
            user_id=user.id,
            report_date=date(2021, 6, 25),
            status=ReportStatus.CONFIRMED,
            kind=ReportKind.NARRATIVE,
            report_type="МРТ головного мозку",
            narrative="Без змін.",
        )
    )
    await async_session.flush()
    narratives = await history.list_narratives(async_session, user_id=user.id)
    assert len(narratives) == 1 and narratives[0].report_type == "МРТ головного мозку"
    items = await history.aggregate_indicators(async_session, user_id=user.id)  # no tabular rows
    counts = dict(history.category_counts(items, len(narratives)))
    assert counts == {grouping.IMAGING: 1}


async def test_list_report_trends_skips_qualitative(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    last = None
    for d in (date(2021, 1, 1), date(2021, 2, 1)):
        # Бактерії is qualitative (no numeric value) on 2 dates; Глюкоза is numeric on 2 dates.
        last = await _report(
            async_session,
            user_id=user.id,
            on=d,
            lab="A",
            results=(("Бактерії", None, None, None), ("Глюкоза", 5.0, 3.9, 6.1)),
        )
    assert last is not None
    keys = [
        it.key
        for it in await history.list_report_trends(
            async_session, user_id=user.id, report_id=last.id
        )
    ]
    assert series_key(None, "Глюкоза") in keys  # numeric on 2 dates -> chartable
    assert series_key(None, "Бактерії") not in keys  # qualitative -> empty chart, so excluded


async def test_list_report_trends_multi_date_flagged_first(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    # Глюкоза (will be out of range) + Білок measured on two dates -> trends; Калій only once.
    await _report(
        async_session,
        user_id=user.id,
        on=date(2026, 1, 1),
        lab="A",
        results=(("Глюкоза", 5.0, 3.9, 6.1), ("Білок", 70.0, 64.0, 83.0)),
    )
    r2 = await _report(
        async_session,
        user_id=user.id,
        on=date(2026, 2, 1),
        lab="A",
        results=(
            ("Глюкоза", 7.0, 3.9, 6.1),  # out of range -> flagged
            ("Білок", 72.0, 64.0, 83.0),  # in range
            ("Калій", 4.0, 3.5, 5.1),  # only one date -> no trend
        ),
    )
    items = await history.list_report_trends(async_session, user_id=user.id, report_id=r2.id)
    keys = [it.key for it in items]
    glu, bil = series_key(None, "Глюкоза"), series_key(None, "Білок")
    assert series_key(None, "Калій") not in keys  # a single measurement is not a trend
    assert set(keys) == {glu, bil}
    assert items[0].key == glu and items[0].flagged  # flagged analyte sorts first
    assert not next(it for it in items if it.key == bil).flagged


async def test_trend_not_found(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    view = await history.trend_for_analyte(async_session, user_id=user.id, analyte="невідоме")
    assert not view.found
    assert history.locale.TREND_NOT_FOUND in view.text


# --- Delete (with Tier 1.1 coupling cleanup) ------------------------------------


async def test_delete_report_cleans_couplings_and_file(
    async_session: AsyncSession, scheduler: ReminderScheduler, tmp_path: Path
) -> None:
    user = await _user(async_session)
    source = tmp_path / "labs.pdf"
    source.write_bytes(b"%PDF-1.4 fake")
    report = await _report(
        async_session,
        user_id=user.id,
        on=date(2026, 5, 1),
        lab="Synevo",
        results=(("Глюкоза", 7.0, 3.9, 6.1),),
        source_file=str(source),
    )
    # A concern proposed from this report's flag + a repeat-lab reminder, both linked.
    condition = await proactive.add_problem(
        async_session, user=user, name="висока глюкоза", scheduler=scheduler, report_id=report.id
    )
    await proactive.add_repeat_lab(
        async_session,
        user=user,
        run_at=datetime(2026, 8, 1, 9, 0),
        label="Глюкоза",
        scheduler=scheduler,
        report_id=report.id,
    )
    await async_session.commit()
    assert sum(j.type == "checkin" for j in scheduler.list_jobs()) == 1
    assert sum(j.type == "repeat_lab" for j in scheduler.list_jobs()) == 1

    await history.delete_report(async_session, report=report, scheduler=scheduler)
    await async_session.commit()

    # Report + file gone.
    assert await async_session.get(LabReport, report.id) is None
    assert not source.exists()
    # Linked concern resolved (its check-in job removed, since it was the only one).
    refreshed = await async_session.get(Condition, condition.id)
    assert refreshed is not None and refreshed.status == ConditionStatus.RESOLVED
    assert sum(j.type == "checkin" for j in scheduler.list_jobs()) == 0
    # Linked repeat-lab reminder retired.
    assert sum(j.type == "repeat_lab" for j in scheduler.list_jobs()) == 0
    reminder = await async_session.scalar(
        Reminder.__table__.select().where(Reminder.report_id == report.id)
    )
    assert reminder is None  # report_id nulled / reminder deactivated, none dangling


async def test_delete_report_without_couplings(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    user = await _user(async_session)
    report = await _report(async_session, user_id=user.id, on=date(2026, 5, 1), lab="A")
    await history.delete_report(async_session, report=report, scheduler=scheduler)
    await async_session.commit()
    assert await async_session.get(LabReport, report.id) is None


async def test_linked_helpers(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    report = await _report(async_session, user_id=user.id, on=date(2026, 5, 1), lab="A")
    async_session.add(
        Condition(user_id=user.id, name="x", status=ConditionStatus.ACTIVE, report_id=report.id)
    )
    async_session.add(
        Condition(user_id=user.id, name="y", status=ConditionStatus.RESOLVED, report_id=report.id)
    )
    await async_session.flush()
    active = await history.linked_active_concerns(async_session, report.id)
    assert [c.name for c in active] == ["x"]  # resolved one is not "active"


# --- Orphaned uploads -----------------------------------------------------------


async def test_orphans_counts_discarded_and_stale_pending_only(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    user = await _user(async_session)
    now = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)
    # Fresh PENDING (mid-confirmation) -> NOT an orphan.
    await _report(
        async_session,
        user_id=user.id,
        on=None,
        lab=None,
        status=ReportStatus.PENDING,
        created_at=now - timedelta(minutes=5),
    )
    # Stale PENDING -> orphan.
    await _report(
        async_session,
        user_id=user.id,
        on=None,
        lab=None,
        status=ReportStatus.PENDING,
        created_at=now - timedelta(hours=3),
    )
    # DISCARDED -> always an orphan.
    f = tmp_path / "junk.jpg"
    f.write_bytes(b"junk")
    await _report(
        async_session,
        user_id=user.id,
        on=None,
        lab=None,
        status=ReportStatus.DISCARDED,
        source_file=str(f),
        created_at=now - timedelta(minutes=1),
    )
    # A CONFIRMED report is never an orphan.
    await _report(async_session, user_id=user.id, on=date(2026, 5, 1), lab="A")

    assert await history.count_orphans(async_session, user_id=user.id, now=now) == 2
    removed = await history.cleanup_orphans(async_session, user_id=user.id, now=now)
    await async_session.commit()
    assert removed == 2
    assert not f.exists()  # the discarded upload's file was removed
    assert await history.count_orphans(async_session, user_id=user.id, now=now) == 0
