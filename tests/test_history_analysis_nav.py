"""The navigable (drill-down) delivery of the expert analysis: overview + per-section buttons."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

from dbaylo import locale
from dbaylo.bot.history_flow import (
    _chart_caption,
    _chart_filename,
    _charts_picker_view,
    _pdf_filename,
    _render_analysis_view,
    _safe_filename,
    _source_filename,
)
from dbaylo.companion import callbacks
from dbaylo.companion.history import TrendChartItem
from dbaylo.labs.trends import LabPoint, compute_trend, series_key
from dbaylo.triage.safety import DISCLAIMER

_SUMMARY = (
    f"{locale.INTERPRET_SECTION_OVERALL}\nТри системи потребують уваги.\n\n"
    f"{locale.INTERPRET_SECTION_ATTENTION}\n• АЛТ 63 — підвищений.\n\n"
    f"{locale.INTERPRET_SECTION_HELP}\n• Більше води.\n\n"
    f"{locale.INTERPRET_SECTION_DOCTOR}\n• До терапевта.\n\n"
    f"{DISCLAIMER}"
)


def _datas(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_view_callback_round_trips() -> None:
    data = callbacks.history_interpret_view(7, 2)
    assert callbacks.parse_history_interpret_view(data) == (7, 2)
    assert callbacks.parse_history_interpret_view("nope:1:2") is None
    assert callbacks.parse_history_interpret_view("hist_iview:7") is None


def test_overview_shows_section_buttons_plus_refresh_delete() -> None:
    view = _render_analysis_view(_SUMMARY, report_id=3, idx=0)
    assert view is not None
    text, keyboard = view
    # The default view is the Загалом overview.
    assert f"🩺 <b>{locale.INTERPRET_SECTION_OVERALL}</b>" in text
    assert "Три системи" in text
    datas = _datas(keyboard)
    # A button to each OTHER section (attention=1, help=2, doctor=3) — never to itself.
    assert callbacks.history_interpret_view(3, 1) in datas
    assert callbacks.history_interpret_view(3, 2) in datas
    assert callbacks.history_interpret_view(3, 3) in datas
    assert callbacks.history_interpret_view(3, 0) not in datas
    # Refresh / delete live only on the overview.
    assert callbacks.history_interpret_refresh(3) in datas
    assert callbacks.history_interpret_del(3) in datas


def test_section_view_offers_back_to_overview_and_other_sections() -> None:
    view = _render_analysis_view(_SUMMARY, report_id=3, idx=1)  # the ⚠️ attention section
    assert view is not None
    text, keyboard = view
    assert f"⚠️ <b>{locale.INTERPRET_SECTION_ATTENTION}</b>" in text
    datas = _datas(keyboard)
    assert callbacks.history_interpret_view(3, 0) in datas  # 🩺 Огляд (back)
    assert callbacks.history_interpret_view(3, 2) in datas  # other sections reachable directly
    assert callbacks.history_interpret_view(3, 1) not in datas  # never to itself
    # No refresh / delete on a section view (they belong to the overview).
    assert callbacks.history_interpret_refresh(3) not in datas


def test_analysis_back_to_card_when_in_history_flow() -> None:
    # In the /history flow (back_page set) every analysis view offers a '◀ Назад' to the card, so
    # you are never stranded; the post-confirm flow (back_page None) shows none.
    from dbaylo.bot.history_flow import _render_analysis_view

    overview = _render_analysis_view(_SUMMARY, report_id=3, idx=0, back_page=0)
    section = _render_analysis_view(_SUMMARY, report_id=3, idx=1, back_page=0)
    assert overview is not None and section is not None
    assert callbacks.history_open(3, 0) in _datas(overview[1])  # back to the report card
    assert callbacks.history_open(3, 0) in _datas(section[1])
    # Without a back_page (post-confirm) there is no back-to-card button.
    no_back = _render_analysis_view(_SUMMARY, report_id=3, idx=0)
    assert no_back is not None
    assert callbacks.history_open(3, 0) not in _datas(no_back[1])


def test_non_canonical_text_is_not_navigable() -> None:
    # A narrative reading / deterministic fallback (no section headers) -> caller sends it whole.
    assert _render_analysis_view(f"Вільний текст.\n\n{DISCLAIMER}", report_id=3, idx=0) is None


# --- Charts picker (one button per trending analyte, not a wall of images) -------


def _items(n: int) -> list[TrendChartItem]:
    # 2 flagged + (n-2) normal, given out of alphabetical order to prove the sort.
    flagged = [TrendChartItem(name=f"Z-flag{i}", key=f"z{i}", flagged=True) for i in range(2)]
    normal = [TrendChartItem(name=f"A-norm{i}", key=f"a{i}", flagged=False) for i in range(n - 2)]
    return flagged + normal


def test_chart_caption_leads_with_the_source_report_context() -> None:
    # Opening a chart from a report keeps "which analysis / date" visible — so flipping the carousel
    # never strands you in a nameless graph. Without a report there is no context line.
    summary = compute_trend(
        [LabPoint("X", date(2024, 1, 1), 1.0), LabPoint("X", date(2026, 1, 1), 2.0)]
    )
    report = SimpleNamespace(report_date=date(2023, 4, 5), lab="ДІЛА")
    caption = _chart_caption(report, summary)
    assert caption.startswith("🔬")
    assert "2023-04-05" in caption  # the source report's date
    assert history_caption_is_below(caption)  # the dynamics line follows the context line
    assert not _chart_caption(None, summary).startswith("🔬")  # no report → no context line


def history_caption_is_below(caption: str) -> bool:
    return caption.count("\n") >= 1 and "вимірів" in caption.split("\n", 1)[1]


def test_pdf_and_source_filenames_are_per_report_and_transport_safe() -> None:
    from dbaylo.db.models import LabResult

    # The filename says WHAT it is (kind), WHEN (date), WHERE (lab, no city) — not one name for all.
    report = SimpleNamespace(
        report_date=date(2023, 4, 5),
        lab="Сінево, Львів",
        results=[LabResult(analyte="Лейкоцити", section="Загальний аналіз сечі", value_text="2-3")],
    )
    pdf_name = _pdf_filename(report)
    assert "2023-04-05" in pdf_name and pdf_name.endswith(".pdf")
    assert "Сеча" in pdf_name  # the analysis kind is in the name
    assert "Львів" not in pdf_name and "Сінево" in pdf_name  # lab without the city
    src_name = _source_filename(report, Path("/uploads/9f3 a1b2.jpg"))
    assert "2023-04-05" in src_name and src_name.endswith(".jpg") and "Сеча" in src_name
    # A report whose results aren't loaded simply omits the kind (no crash).
    bare = SimpleNamespace(report_date=date(2023, 4, 5), lab="ДІЛА")
    assert _pdf_filename(bare).endswith(".pdf")
    # Control chars / path separators / whitespace are made safe for a Content-Disposition header.
    safe = _safe_filename("a/b\x1fc d")
    assert "/" not in safe and "\x1f" not in safe and " " not in safe


async def test_gather_notes_bounded_keeps_order_and_handles_empty(monkeypatch) -> None:
    from dbaylo.bot import history_flow

    async def fake_describe(title, *, specimen=None, **_):
        return f"note:{title}:{specimen}"

    monkeypatch.setattr(history_flow, "describe_indicator", fake_describe)
    notes = await history_flow._gather_notes_bounded([("АЛТ", "blood"), ("Сеча-pH", "urine")])
    assert notes == ["note:АЛТ:blood", "note:Сеча-pH:urine"]  # one per item, in order
    assert await history_flow._gather_notes_bounded([]) == []


def test_pdf_cover_uses_readable_category_names() -> None:
    # The cover must read like prose ("Аналіз сечі — 19"), not the crude chip "Сеча — 19".
    from dbaylo.bot.history_flow import _pdf_cover
    from dbaylo.companion.history import ReportBreakdown

    report = SimpleNamespace(report_date=date(2026, 2, 1), lab="ДІЛА")
    breakdown = ReportBreakdown(
        total=39, numeric=19, qualitative=14, single=6, categories=[("urine", 19)]
    )
    cover = _pdf_cover(report, breakdown)
    assert cover.category_rows == ("Аналіз сечі — 19",)
    assert "39" in cover.notes[-1]  # the honest total
    assert any("14" in n for n in cover.notes)  # qualitative count surfaced


def test_chart_filename_is_descriptive_and_control_char_safe() -> None:
    # A saved chart says WHAT it is ("Дбайло-динаміка-<analyte>.png"), not a bare "Еритроцити.png".
    assert _chart_filename("Еритроцити") == "Дбайло-динаміка-Еритроцити.png"
    # Spaces become dashes for a tidy name.
    assert _chart_filename("Загальний білок") == "Дбайло-динаміка-Загальний-білок.png"
    # The series key carries a \x1f separator; using it (or any name with control chars) as the
    # attachment filename made aiohttp reject the upload ("Forbidden control character"), which
    # silently killed every single-chart pick. The filename must be control-char-free.
    key = series_key("Мікроскопія осаду сечі", "Неплаский епітелій")
    assert "\x1f" in key  # the bug's source
    fname = _chart_filename(key)
    assert not any(ord(ch) < 0x20 for ch in fname)
    assert fname.endswith(".png")
    # An empty / unreadable name still yields a usable, descriptive filename.
    assert _chart_filename("\x1f\x00") == "Дбайло-динаміка-показник.png"


def test_chart_nav_keyboard_lets_you_flip_without_scrolling_up() -> None:
    from dbaylo.bot.history_flow import _chart_nav_keyboard

    # Middle of 5: one row — ⬅️ / 📋 i/n / ➡️.
    kb = _chart_nav_keyboard(report_id=7, index=2, total=5)
    assert len(kb.inline_keyboard) == 1  # single row, no separate list button
    datas = _datas(kb)
    assert callbacks.chart_nav(7, 1) in datas  # ⬅️ prev
    assert callbacks.chart_nav(7, 3) in datas  # ➡️ next
    # The position counter IS the back-to-list button — exactly once, not duplicated.
    assert datas.count(callbacks.history_dynamics(7)) == 1
    middle = next(b for row in kb.inline_keyboard for b in row if "/" in b.text)
    assert "3/5" in middle.text  # 1-based position (index 2 of 5)
    # First chart has no prev arrow; last has no next arrow.
    first = _datas(_chart_nav_keyboard(report_id=7, index=0, total=5))
    assert callbacks.chart_nav(7, -1) not in first and callbacks.chart_nav(7, 1) in first
    last = _datas(_chart_nav_keyboard(report_id=7, index=4, total=5))
    assert callbacks.chart_nav(7, 5) not in last and callbacks.chart_nav(7, 3) in last
    assert callbacks.parse_chart_nav(callbacks.chart_nav(7, 3)) == (7, 3)


def test_picker_lists_one_button_per_analyte_with_pick_callbacks() -> None:
    items = [
        TrendChartItem(name="АЛТ", key="алт", flagged=True),
        TrendChartItem(name="Калій", key="калій", flagged=False),
    ]
    text, kb = _charts_picker_view(items, report_id=5, page=0)
    assert locale.CHART_PICK_HEADER.split(" ")[0] in text  # the picker header
    datas = _datas(kb)
    assert callbacks.chart_pick(5, 0) in datas and callbacks.chart_pick(5, 1) in datas
    assert callbacks.chart_all(5) in datas  # opt-in text report
    assert callbacks.chart_pdf(5) in datas  # opt-in one-PDF export
    assert callbacks.parse_chart_pdf(callbacks.chart_pdf(5)) == 5
    assert callbacks.history_open(5, 0) in datas  # '◀ Назад' back to the report card
    # The flagged analyte carries the ⚠️ prefix on its button.
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any(lbl.startswith(locale.CHART_FLAGGED_PREFIX) and "АЛТ" in lbl for lbl in labels)


def test_picker_paginates_and_indices_stay_global() -> None:
    items = _items(10)  # > one page of 8
    _, kb0 = _charts_picker_view(items, report_id=5, page=0)
    datas0 = _datas(kb0)
    assert callbacks.chart_page(5, 1) in datas0  # a "next" pager
    assert callbacks.chart_pick(5, 7) in datas0  # last item on page 0
    _, kb1 = _charts_picker_view(items, report_id=5, page=1)
    datas1 = _datas(kb1)
    assert callbacks.chart_pick(5, 8) in datas1  # page 2 continues the GLOBAL index
    assert callbacks.chart_page(5, 0) in datas1  # a "prev" pager


def test_chart_callbacks_round_trip() -> None:
    assert callbacks.parse_chart_pick(callbacks.chart_pick(4, 9)) == (4, 9)
    assert callbacks.parse_chart_page(callbacks.chart_page(4, 2)) == (4, 2)
    assert callbacks.parse_chart_open(callbacks.chart_open(4)) == 4
    assert callbacks.parse_chart_all(callbacks.chart_all(4)) == 4
    assert callbacks.parse_chart_pick("chart_pick:4") is None
