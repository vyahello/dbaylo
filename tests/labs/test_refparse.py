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
