"""Age-stratified reference resolution (e.g. ПСА: <40 -> <1.4, 50-60 -> <3.1)."""

from __future__ import annotations

from datetime import date

import pytest

from dbaylo.labs.agerefs import age_on, describe_age, is_age_table, resolve_age_reference

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


def test_a_sex_split_value_resolves_for_a_known_sex_only() -> None:
    # A sex-split adult value ("Жінки …; Чоловіки …") resolves to the patient's OWN sex band when
    # sex is known — but is NEVER guessed when sex is unknown (better no band than the wrong sex's).
    assert resolve_age_reference(_SEX_SPLIT, 30) is None  # unknown sex -> no guess
    assert resolve_age_reference(_SEX_SPLIT, 30, sex="m") == (1.78, 5.38)
    assert resolve_age_reference(_SEX_SPLIT, 30, sex="f") == (1.56, 6.13)


# Real CBC strings the labs print: a multi-age table whose ADULT row is itself SEX-split.
_RBC = (
    "Діти: <1 року: 3.3 - 4.9; 1-6 років: 3.5 - 4.5; 6-12 років: 3.5 - 4.7; "
    "12-16 років: 3.6 - 5.1; Дорослі: Чоловіки: 4.0 - 5.0; Жінки: 3.7 - 4.7"
)
_HGB = (
    "Діти: <1 року: 100.0 - 140.0; 1-6 років: 110.0 - 145.0; 6-16 років: 115.0 - 150.0; "
    "Дорослі: Чоловіки: 130.0 - 160.0; Жінки: 120.0 - 140.0"
)
_HCT = "Діти: <1 року: 32.0 - 49.0; 1-16 років: 32.0 - 45.0; Дорослі: 35.0 - 54.0"
_PSA = (
    "Чоловіки: <40 років: <1.4; 40-50 років: <2.0; 50-60 років: <3.1; "
    "60-70 років: <4.1; ≥70 років: <4.4"
)


def test_sex_split_adult_row_resolves_only_with_the_matching_sex() -> None:
    # The adult row is "Дорослі: Чоловіки: 4.0 - 5.0; Жінки: 3.7 - 4.7": a 30-y-o male must get the
    # male band, a female the female band — and with UNKNOWN sex we refuse to guess (None).
    assert resolve_age_reference(_RBC, 30, sex="m") == (4.0, 5.0)
    assert resolve_age_reference(_RBC, 30, sex="f") == (3.7, 4.7)
    assert resolve_age_reference(_RBC, 30) is None  # unknown sex -> no guess
    assert resolve_age_reference(_HGB, 30, sex="m") == (130.0, 160.0)


def test_plain_adult_row_resolves_without_sex() -> None:
    # "Дорослі: 35.0 - 54.0" carries no sex split, so age alone resolves it (a child gets a child
    # band). This is the case that was painting the WRONG band before (the <1-year child range).
    assert resolve_age_reference(_HCT, 30) == (35.0, 54.0)
    assert resolve_age_reference(_HCT, 8) == (32.0, 45.0)  # "1-16 років"


def test_is_age_table_detects_tables_and_rejects_plain_refs() -> None:
    assert is_age_table(_PSA) and is_age_table(_RBC) and is_age_table(_HCT)
    assert not is_age_table("3.9 - 6.1")  # a plain two-sided range is NOT a table
    assert not is_age_table("< 1.4") and not is_age_table("не виявлено")
    assert not is_age_table(None) and not is_age_table("")


def test_age_on_computes_whole_years() -> None:
    assert age_on(date(1993, 3, 23), date(2023, 4, 23)) == 30
    assert age_on(date(1993, 3, 23), date(2023, 3, 22)) == 29  # birthday not reached yet
    assert age_on(date(1993, 3, 23), date(2023, 3, 23)) == 30  # on the birthday
    assert age_on(None, date(2023, 1, 1)) is None
    assert age_on(date(1993, 3, 23), None) is None


def test_describe_age_is_a_short_human_recency() -> None:
    today = date(2026, 6, 25)
    assert describe_age(None, today=today) == ""
    assert describe_age(today, today=today) == "today"
    assert describe_age(date(2026, 6, 20), today=today) == "5 days ago"
    assert describe_age(date(2026, 6, 4), today=today) == "~3 weeks ago"
    assert describe_age(date(2026, 3, 25), today=today) == "~3 months ago"  # ~92 days
    assert describe_age(date(2024, 6, 25), today=today) == "~2 years ago"
    assert describe_age(date(2027, 1, 1), today=today) == "in the future"
