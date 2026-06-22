"""The navigable (drill-down) delivery of the expert analysis: overview + per-section buttons."""

from __future__ import annotations

from dbaylo import locale
from dbaylo.bot.history_flow import _chart_filename, _charts_picker_view, _render_analysis_view
from dbaylo.companion import callbacks
from dbaylo.companion.history import TrendChartItem
from dbaylo.labs.trends import series_key
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


def test_chart_filename_strips_control_chars() -> None:
    # The series key carries a \x1f separator; using it (or any name with control chars) as the
    # attachment filename made aiohttp reject the upload ("Forbidden control character"), which
    # silently killed every single-chart pick. The filename must be control-char-free.
    key = series_key("Мікроскопія осаду сечі", "Неплаский епітелій")
    assert "\x1f" in key  # the bug's source
    fname = _chart_filename(key)
    assert not any(ord(ch) < 0x20 for ch in fname)
    assert fname.endswith(".png")
    # A normal display name is preserved; an empty/blank name still yields a usable filename.
    assert _chart_filename("Еритроцити") == "Еритроцити.png"
    assert _chart_filename("\x1f\x00") == "chart.png"


def test_chart_nav_keyboard_lets_you_flip_without_scrolling_up() -> None:
    from dbaylo.bot.history_flow import _chart_nav_keyboard

    # Middle of 5: both arrows present + a jump back to the list.
    datas = _datas(_chart_nav_keyboard(report_id=7, index=2, total=5))
    assert callbacks.chart_nav(7, 1) in datas  # ⬅️ prev
    assert callbacks.chart_nav(7, 3) in datas  # ➡️ next
    assert callbacks.history_dynamics(7) in datas  # 📋 back to the picker
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
