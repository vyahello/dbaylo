"""The navigable (drill-down) delivery of the expert analysis: overview + per-section buttons."""

from __future__ import annotations

from dbaylo import locale
from dbaylo.bot.history_flow import _render_analysis_view
from dbaylo.companion import callbacks
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


def test_non_canonical_text_is_not_navigable() -> None:
    # A narrative reading / deterministic fallback (no section headers) -> caller sends it whole.
    assert _render_analysis_view(f"Вільний текст.\n\n{DISCLAIMER}", report_id=3, idx=0) is None
