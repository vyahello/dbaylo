"""Tests for the pure helpers of the lab confirmation flow."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from dbaylo import locale
from dbaylo.bot import lab_flow
from dbaylo.bot.lab_flow import (
    _CB_CONCERN_NO,
    _CB_CONCERN_YES,
    _CB_EDIT_DATE,
    _CB_EDIT_KEEP,
    _CB_EDIT_LAB,
    _CB_REPEAT_NO,
    _CB_REPEAT_OTHER,
    _CB_SHOW_ALL,
    _concern_keyboard,
    _parse_rid,
    _prompt_edit_date,
    _prompt_edit_lab,
    _repeat_keyboard,
    _report_from_state,
    _report_to_state,
    _rid_cb,
    confirmation_keyboard,
    parse_edit_target,
    parse_value,
    render_confirmation_full,
    render_confirmation_text,
)
from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport


def _cb_datas(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _report() -> ExtractedReport:
    return ExtractedReport(
        report_date=date(2026, 5, 12),
        lab="Synevo",
        results=[
            ExtractedAnalyte("Глюкоза", value=7.0, unit="ммоль/л", ref_low=3.9, ref_high=6.1),
            ExtractedAnalyte("Кетони", value=None, value_text="не виявлено"),
        ],
    )


# --- Auto-routing: a freely-dropped photo classified as a prescription -----------


def _patch_upload(monkeypatch, *, outcome: ExtractedReport) -> AsyncMock:
    """Stub _handle_upload's heavy deps; return the present-prescription spy."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=SimpleNamespace(status=None, raw_ocr=None))

    @asynccontextmanager
    async def fake_session():
        yield session

    monkeypatch.setattr(lab_flow, "get_session", fake_session)
    monkeypatch.setattr(lab_flow, "ensure_user", AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(lab_flow, "find_confirmed_by_hash", AsyncMock(return_value=None))
    monkeypatch.setattr(lab_flow, "save_original_file", Mock(return_value="/tmp/up.jpg"))
    monkeypatch.setattr(
        lab_flow, "create_pending_report", AsyncMock(return_value=SimpleNamespace(id=42))
    )
    monkeypatch.setattr(lab_flow, "extract_document", AsyncMock(return_value=outcome))
    present = AsyncMock()
    monkeypatch.setattr(lab_flow.prescription_flow, "present_prescription_from_path", present)
    monkeypatch.setattr(lab_flow, "answer_chunked", AsyncMock())
    return present


def _upload_message() -> AsyncMock:
    message = AsyncMock()
    message.from_user = SimpleNamespace(id=4242, full_name="Owner")
    return message


async def test_dropped_prescription_is_auto_routed_to_the_meds_flow(monkeypatch) -> None:
    # The read classified the upload as a prescription AND it has no analyte rows → hand off to the
    # medication flow (no need to pre-tap 📷 З фото рецепта); the lab confirm is NOT rendered.
    present = _patch_upload(
        monkeypatch, outcome=ExtractedReport(document_type="prescription", results=[])
    )
    message, state = _upload_message(), AsyncMock()
    await lab_flow._handle_upload(message, state, file_id="f", suffix=".jpg")
    present.assert_awaited_once()
    assert present.await_args.kwargs["path"] == "/tmp/up.jpg"
    lab_flow.answer_chunked.assert_not_awaited()  # the lab confirm view never shows


async def test_a_lab_with_results_is_never_hijacked_as_a_prescription(monkeypatch) -> None:
    # Even if the model tags document_type=prescription, a real results table stays a lab report.
    present = _patch_upload(
        monkeypatch,
        outcome=ExtractedReport(
            document_type="prescription",
            results=[ExtractedAnalyte("Глюкоза", value=5.4)],
        ),
    )
    message, state = _upload_message(), AsyncMock()
    await lab_flow._handle_upload(message, state, file_id="f", suffix=".jpg")
    present.assert_not_awaited()
    lab_flow.answer_chunked.assert_awaited()  # the normal lab confirm view


def test_render_confirmation_is_problems_first() -> None:
    text = render_confirmation_text(_report())
    assert "2026-05-12" in text and "Synevo" in text  # bold header, date · lab
    assert "норма" in text
    assert "1. Глюкоза" in text  # 7.0 > 6.1 -> out of range -> the attention group
    assert "⚠️" in text
    # Only a couple of rows, so the in-range Кетони is listed by name under the "у межах норми"
    # header — NOT hidden, and with NO green ✅ on the row itself (rail #4).
    assert "2. Кетони" in text
    assert "У межах норми" in text
    assert "Звір" in text  # the verify prompt (there are rows to check)
    assert text.count("✅") == 1  # only the "✅ У межах норми:" header, never per-row


def test_render_few_in_range_listed_by_name() -> None:
    # The single-analyte ДІЛА case: one in-range result must be shown by name, not hidden behind
    # "✅ Усі 1 — у межах норми".
    report = ExtractedReport(
        report_date=date(2026, 5, 12),
        results=[ExtractedAnalyte("Натрій", value=135.0, ref_low=132.0, ref_high=146.0)],
    )
    text = render_confirmation_text(report)
    assert "Натрій" in text and "135" in text  # the one result is named
    assert "У межах норми" in text
    assert "Усе правильно?" in text
    assert "Усі 1 — у межах норми" not in text  # no opaque aggregate for a single row


def test_render_many_in_range_collapses_to_aggregate() -> None:
    report = ExtractedReport(
        results=[ExtractedAnalyte(f"A{i}", value=4.0, ref_low=3.5, ref_high=5.1) for i in range(7)]
    )
    text = render_confirmation_text(report)
    assert "Усі 7 — у межах норми" in text  # too many to list -> collapsed
    assert "A0" not in text  # individual in-range rows are hidden
    assert _CB_SHOW_ALL in _cb_datas(confirmation_keyboard(report))  # ... behind the expand button


def test_render_surfaces_unreadable_row_with_question_mark() -> None:
    report = ExtractedReport(
        results=[
            ExtractedAnalyte("Гемоглобін", value=None),  # OCR could not read it
            ExtractedAnalyte("Калій", value=4.0, ref_low=3.5, ref_high=5.1),  # fine
        ]
    )
    text = render_confirmation_text(report)
    assert "1. Гемоглобін" in text and "❔" in text  # surfaced for a look (rail #5)
    assert "потребують уваги" in text  # summary umbrella when a row is unreadable
    assert "Калій" in text and "У межах норми" in text  # the one in-range row is listed by name


def test_render_handles_unknown_date_and_lab() -> None:
    report = ExtractedReport(results=[ExtractedAnalyte("X", value=1.0)])
    text = render_confirmation_text(report)
    assert "невідома" in text


def test_render_full_groups_rows_by_panel_section() -> None:
    # A combined report: same name (Глюкоза, Лейкоцити) in two panels must stay apart.
    report = ExtractedReport(
        results=[
            ExtractedAnalyte("Глюкоза", value=5.3, unit="ммоль/л", section="Аналіз крові"),
            ExtractedAnalyte("Лейкоцити", value=7.3, unit="10⁹/л", section="Аналіз крові"),
            ExtractedAnalyte("Глюкоза", value_text="не виявлена", section="Аналіз сечі"),
            ExtractedAnalyte("Лейкоцити", value_text="15-50", section="Аналіз сечі"),
        ]
    )
    text = render_confirmation_full(report)
    assert "Аналіз крові" in text and "Аналіз сечі" in text
    # Headers come before their rows; numbering stays global and contiguous (edit-by-number).
    assert text.index("Аналіз крові") < text.index("Аналіз сечі")
    assert "1. Глюкоза" in text and "3. Глюкоза" in text  # both panels' Глюкоза present, numbered
    assert "✅" not in text  # in-range rows carry no green check (rail #4)


def test_render_confirmation_narrative_document() -> None:
    report = ExtractedReport(
        report_date=date(2021, 6, 25),
        report_type="МРТ головного мозку",
        narrative="Без вогнищевих змін.",
        conclusion="Патології не виявлено",
    )
    text = render_confirmation_text(report)
    assert "МРТ головного мозку" in text
    assert "Без вогнищевих змін" in text
    assert "Патології не виявлено" in text
    assert "Усе правильно?" in text  # still goes through the confirm loop (rail #5)
    assert "невідома" not in text  # an imaging study with no lab does not show a bare "невідома"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("дата", "date"),
        ("Дата", "date"),
        ("лаб", "lab"),
        ("лабораторія", "lab"),
        ("2", 2),
        ("1", 1),
        ("3", None),  # out of range (only 2 rows)
        ("0", None),
        ("abc", None),
    ],
)
def test_parse_edit_target(text, expected) -> None:
    assert parse_edit_target(text, n_rows=2) == expected


@pytest.mark.parametrize(
    ("text", "expected"), [("5,4", 5.4), ("6.1", 6.1), ("  7 ", 7.0), ("x", None), ("", None)]
)
def test_parse_value(text, expected) -> None:
    assert parse_value(text) == expected


def test_state_round_trip_preserves_data() -> None:
    original = _report()
    restored = _report_from_state(_report_to_state(original))
    assert restored.report_date == original.report_date
    assert restored.lab == original.lab
    assert [a.analyte for a in restored.results] == ["Глюкоза", "Кетони"]
    assert restored.results[0].value == 7.0
    assert restored.results[1].value_text == "не виявлено"


def test_state_round_trip_preserves_narrative() -> None:
    # The МРТ bug: the FSM round-trip used to drop narrative/report_type/conclusion, so a confirmed
    # imaging study lost its findings between confirm and persist -> stored as an empty tabular.
    original = ExtractedReport(
        report_date=date(2021, 6, 25),
        report_type="МРТ головного мозку",
        narrative="Без вогнищевих змін.",
        conclusion="Патології не виявлено",
    )
    restored = _report_from_state(_report_to_state(original))
    assert restored.is_narrative
    assert restored.report_type == "МРТ головного мозку"
    assert restored.narrative == "Без вогнищевих змін."
    assert restored.conclusion == "Патології не виявлено"


def test_confirmation_keyboard_offers_expand_when_many_in_range() -> None:
    # one out-of-range row + several in-range rows -> the in-range ones collapse, so the "show
    # all" expand button is offered, plus the quick-edit date/lab buttons.
    rows = [ExtractedAnalyte("Bad", value=10.0, ref_low=1.0, ref_high=5.0)]
    rows += [ExtractedAnalyte(f"Ok{i}", value=3.0, ref_low=1.0, ref_high=5.0) for i in range(6)]
    datas = _cb_datas(confirmation_keyboard(ExtractedReport(results=rows)))
    assert _CB_SHOW_ALL in datas
    assert _CB_EDIT_DATE in datas and _CB_EDIT_LAB in datas


def test_confirmation_keyboard_no_expand_for_a_small_report() -> None:
    # A few in-range rows are listed inline, so nothing is hidden -> no expand button.
    assert _CB_SHOW_ALL not in _cb_datas(confirmation_keyboard(_report()))


def test_confirmation_keyboard_hides_expand_when_nothing_collapsed() -> None:
    # Every row needs a look -> nothing is hidden -> no expand button. Also hidden on the
    # full view itself.
    report = ExtractedReport(results=[ExtractedAnalyte("X", value=10.0, ref_low=1.0, ref_high=5.0)])
    assert _CB_SHOW_ALL not in _cb_datas(confirmation_keyboard(report))
    assert _CB_SHOW_ALL not in _cb_datas(confirmation_keyboard(_report(), full=True))


async def test_field_edit_prompt_shows_the_recognised_value() -> None:
    # The date/lab are auto-recognised; the edit prompt must show what was recognised (and a
    # "leave as is" escape), so the user isn't confused into thinking nothing was extracted.
    message = AsyncMock()
    await _prompt_edit_date(message, _report())
    text = message.answer.await_args.args[0]
    assert "2026-05-12" in text  # the recognised date is echoed back
    keep = message.answer.await_args.kwargs["reply_markup"]
    assert _cb_datas(keep) == [_CB_EDIT_KEEP]


async def test_field_edit_prompt_lab_value_and_unknown_fallback() -> None:
    message = AsyncMock()
    await _prompt_edit_lab(message, _report())
    assert "Synevo" in message.answer.await_args.args[0]
    # When nothing was recognised, the prompt says so rather than implying a silent gap.
    message2 = AsyncMock()
    await _prompt_edit_date(message2, ExtractedReport(results=[ExtractedAnalyte("X", value=1.0)]))
    assert locale.LAB_DATE_UNKNOWN in message2.answer.await_args.args[0]


def test_confirmation_keyboard_narrative_has_no_number_edit() -> None:
    # A narrative document has no numbered rows, so the number-typing "✏️ Виправити" is dropped;
    # date/lab quick-edit and confirm/cancel remain.
    report = ExtractedReport(report_type="МРТ", narrative="текст")
    datas = _cb_datas(confirmation_keyboard(report))
    assert "lab:edit" not in datas  # _CB_EDIT
    assert _CB_EDIT_DATE in datas and _CB_EDIT_LAB in datas
    assert "lab:confirm" in datas and "lab:cancel" in datas


# --- Post-confirm offers are stateless: report_id rides in the callback data --------


def test_rid_callback_round_trips() -> None:
    assert _parse_rid("lab:rep:1m", _rid_cb("lab:rep:1m", 42)) == 42
    assert _parse_rid(_CB_REPEAT_NO, _rid_cb(_CB_REPEAT_NO, 7)) == 7


def test_parse_rid_rejects_wrong_prefix_or_non_digit() -> None:
    assert _parse_rid("lab:rep:1m", "lab:rep:3m:5") is None  # different interval
    assert _parse_rid("lab:con:y", "lab:con:y:abc") is None  # non-numeric id
    assert _parse_rid("lab:con:y", None) is None


def test_repeat_keyboard_every_button_carries_the_report_id() -> None:
    datas = _cb_datas(_repeat_keyboard(99))
    assert all(d.endswith(":99") for d in datas)
    # The three intervals + "other" + "no".
    assert datas == [
        "lab:rep:1m:99",
        "lab:rep:3m:99",
        "lab:rep:6m:99",
        f"{_CB_REPEAT_OTHER}:99",
        f"{_CB_REPEAT_NO}:99",
    ]


def test_concern_keyboard_carries_the_report_id() -> None:
    assert _cb_datas(_concern_keyboard(3)) == [f"{_CB_CONCERN_YES}:3", f"{_CB_CONCERN_NO}:3"]
