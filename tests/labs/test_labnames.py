"""The lab-brand canonicalizer keeps the history list from splitting one lab into two."""

from __future__ import annotations

import pytest

from dbaylo.labs.labnames import normalize_lab


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Синево", "Сінево"),  # the OCR drift the user hit (и -> canonical і)
        ("синево", "Сінево"),
        ("Synevo", "Сінево"),
        ("Синево, Львів", "Сінево, Львів"),  # city suffix preserved
        ("  синево , Київ", "Сінево, Київ"),  # brand trimmed, suffix kept
        ("dila", "ДІЛА"),
        ("Інвітро", "Інвітро"),
    ],
)
def test_known_brands_canonicalized(raw: str, expected: str) -> None:
    assert normalize_lab(raw) == expected


@pytest.mark.parametrize("value", [None, "", "Лабораторія Львів", "Якась нова лабораторія"])
def test_unknown_or_blank_passthrough(value: str | None) -> None:
    assert normalize_lab(value) == value


def test_idempotent() -> None:
    once = normalize_lab("Синево, Львів")
    assert once == "Сінево, Львів"
    assert normalize_lab(once) == once
