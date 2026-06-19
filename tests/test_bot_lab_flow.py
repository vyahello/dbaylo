"""Tests for the pure helpers of the lab confirmation flow."""

from __future__ import annotations

from datetime import date

import pytest

from dbaylo.bot.lab_flow import (
    _report_from_state,
    _report_to_state,
    confirmation_keyboard,
    parse_edit_target,
    parse_value,
    render_confirmation_text,
)
from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport


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
    assert "⬆️" in text  # 7.0 > 6.1 high flag


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
