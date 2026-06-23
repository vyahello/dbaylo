"""Age-stratified reference resolution (e.g. ПСА: <40 -> <1.4, 50-60 -> <3.1)."""

from __future__ import annotations

from datetime import date

import pytest

from dbaylo.labs.agerefs import age_on, resolve_age_reference

# The PSA table as a few labs print it (separators / wording vary).
_TABLES = [
    "<40 років: <1.4; 40-50: <2.0; 50-60: <3.1; 60-70: <4.1; >70: <4.4",
    "<40: <1.4\n40-50: <2.0\n50-60: <3.1\n60-70: <4.1\n>70: <4.4",
    "до 40 років - <1.4, 40-50 - <2.0, 50-60 - <3.1, 60-70 - <4.1, понад 70 - <4.4",
]


@pytest.mark.parametrize("table", _TABLES)
@pytest.mark.parametrize(
    ("age", "expected"),
    [(30, (None, 1.4)), (45, (None, 2.0)), (55, (None, 3.1)), (65, (None, 4.1)), (75, (None, 4.4))],
)
def test_resolve_picks_the_row_for_the_age(table, age, expected) -> None:
    assert resolve_age_reference(table, age) == expected


def test_boundary_ages_pick_the_right_band() -> None:
    t = _TABLES[0]
    assert resolve_age_reference(t, 40) == (None, 2.0)  # 40 leaves "<40", enters "40-50"
    assert resolve_age_reference(t, 50) == (None, 3.1)  # 50 leaves "40-50", enters "50-60"


def test_a_plain_single_reference_is_not_treated_as_a_table() -> None:
    # A single bound / range must NOT be misread as an age table (needs >=2 age rows).
    assert resolve_age_reference("<1.4", 30) is None
    assert resolve_age_reference("3.9-6.1", 30) is None
    assert resolve_age_reference("не виявлено", 30) is None
    assert resolve_age_reference(None, 30) is None
    assert resolve_age_reference(_TABLES[0], None) is None  # no age -> can't resolve


# Real shapes the lab prints for CBC differentials (children bands + an adult row / sub-ranges).
_NEUTRO_PCT = (
    "Діти: до 1 року: 15-45; 1-6 років: 25-60; 6-12 років: 35-65; "
    "12-16 років: 40-65; Дорослі: 47-72"
)
_NESTED = (
    "Діти: до 2 років: ≤0.4; 10-18 років: ≤1.1; "
    "Дорослі: 18-20 років: ≤1.1; 20-60 років: ≤0.9; старше 60 років: ≤1.1"
)
_SEX_SPLIT = "Дорослі: Жінки: 1.56 – 6.13; Чоловіки: 1.78 – 5.38"


def test_resolves_an_adult_row_for_an_adult() -> None:
    assert resolve_age_reference(_NEUTRO_PCT, 30) == (47.0, 72.0)  # "Дорослі: 47-72"
    assert resolve_age_reference(_NEUTRO_PCT, 8) == (35.0, 65.0)  # a child band ("6-12: 35-65")


def test_nested_adult_subranges_pick_the_right_band_not_the_header() -> None:
    # The "Дорослі: 18-20 років: ≤1.1" header must NOT be misread as the band (18, 20); the real
    # sub-row for the age wins.
    assert resolve_age_reference(_NESTED, 30) == (None, 0.9)  # "20-60 років: ≤0.9"
    assert resolve_age_reference(_NESTED, 70) == (None, 1.1)  # "старше 60 років: ≤1.1"


def test_a_sex_split_value_is_never_guessed() -> None:
    # A sex-split adult value ("Жінки …; Чоловіки …") is NOT resolved into a band — we return None
    # rather than risk the wrong sex's range (the lone non-prefixed row gives <2 parseable rows).
    assert resolve_age_reference(_SEX_SPLIT, 30) is None  # unknown sex -> no guess
    assert resolve_age_reference(_SEX_SPLIT, 30, sex="m") is None  # male row carries no age prefix


def test_age_on_computes_whole_years() -> None:
    assert age_on(date(1993, 3, 23), date(2023, 4, 23)) == 30
    assert age_on(date(1993, 3, 23), date(2023, 3, 22)) == 29  # birthday not reached yet
    assert age_on(date(1993, 3, 23), date(2023, 3, 23)) == 30  # on the birthday
    assert age_on(None, date(2023, 1, 1)) is None
    assert age_on(date(1993, 3, 23), None) is None
