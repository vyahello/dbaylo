"""Tests for the pure helpers of the lab confirmation flow."""

from __future__ import annotations

from datetime import date

import pytest

from dbaylo.bot.lab_flow import (
    _CB_CONCERN_NO,
    _CB_CONCERN_YES,
    _CB_REPEAT_NO,
    _CB_REPEAT_OTHER,
    _concern_keyboard,
    _parse_rid,
    _repeat_keyboard,
    _report_from_state,
    _report_to_state,
    _rid_cb,
    confirmation_keyboard,
    parse_edit_target,
    parse_value,
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


def test_render_confirmation_text_is_ukrainian_and_complete() -> None:
    text = render_confirmation_text(_report())
    assert "Дата: 2026-05-12" in text
    assert "Лабораторія: Synevo" in text
    assert "норма" in text
    assert "Усе правильно?" in text
    assert "1. Глюкоза" in text and "2. Кетони" in text
    assert "⚠️" in text  # 7.0 > 6.1 -> out of range, attention marker
    assert "✅" in text  # Кетони "не виявлено" is not flagged -> ok


def test_render_handles_unknown_date_and_lab() -> None:
    report = ExtractedReport(results=[ExtractedAnalyte("X", value=1.0)])
    text = render_confirmation_text(report)
    assert "невідома" in text


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


def test_confirmation_keyboard_has_three_buttons() -> None:
    kb = confirmation_keyboard()
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 3


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
