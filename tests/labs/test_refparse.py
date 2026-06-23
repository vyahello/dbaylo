"""Deterministic reference-range parser — recovers numeric bounds from a printed reference."""

from __future__ import annotations

import pytest

from dbaylo.labs.refparse import parse_ref_range


@pytest.mark.parametrize(
    ("ref_text", "expected"),
    [
        ("3.9-6.1", (3.9, 6.1)),  # two-sided
        ("3,9 - 6,1", (3.9, 6.1)),  # comma decimals, spaces
        ("0 – 2", (0.0, 2.0)),  # en-dash
        ("0 - 2 в п/з", (0.0, 2.0)),  # trailing unit text ignored
        ("< 5.2", (None, 5.2)),  # upper bound
        ("≤ 5", (None, 5.0)),
        ("до 50", (None, 50.0)),
        ("> 0.9", (0.9, None)),  # lower bound
        ("≥ 1", (1.0, None)),
        ("від 4", (4.0, None)),
        ("негативно", (None, None)),  # qualitative -> no numbers, never guess
        ("не виявлено", (None, None)),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_ref_range(ref_text, expected) -> None:
    assert parse_ref_range(ref_text) == expected


def test_an_age_table_is_not_flattened_to_a_range() -> None:
    # The motivating bug: an AGE table ("<40: <1.4; 40-50: <2.0; …") must NOT be flattened here — a
    # range regex would grab the age span "40-50" as a value band (40..50), drawing a wildly wrong
    # norm. It is left as free text and resolved by the patient's age at read time instead.
    psa = "Чоловіки: <40 років: <1.4; 40-50 років: <2.0; 50-60 років: <3.1; ≥70 років: <4.4"
    cbc = "Діти: <1 року: 3.3 - 4.9; 1-6 років: 3.5 - 4.5; Дорослі: 4.0 - 5.0"
    assert parse_ref_range(psa) == (None, None)
    assert parse_ref_range(cbc) == (None, None)
    # A genuine plain range with a dash is still parsed normally (not mistaken for a table).
    assert parse_ref_range("12-16") == (12.0, 16.0)
