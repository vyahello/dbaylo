"""Trend engine tests — the deterministic lab core. High coverage, no network."""

from __future__ import annotations

from datetime import date

import pytest

from dbaylo.db.models import ResultFlag
from dbaylo.labs.trends import (
    LabPoint,
    Polarity,
    TrendDirection,
    _distance_outside,
    build_series,
    classify,
    compute_flag,
    compute_trend,
    normalize_analyte,
    polarity,
    qualitative_match,
)


def p(day: int, value: float | None, low=None, high=None, analyte="Глюкоза", unit="ммоль/л"):
    return LabPoint(
        analyte=analyte,
        taken_on=date(2026, 1, day),
        value=value,
        unit=unit,
        ref_low=low,
        ref_high=high,
    )


# --- compute_flag ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "low", "high", "expected"),
    [
        (5.0, 4.0, 6.0, ResultFlag.NORMAL),
        (3.0, 4.0, 6.0, ResultFlag.LOW),
        (7.0, 4.0, 6.0, ResultFlag.HIGH),
        (5.0, None, 6.0, ResultFlag.NORMAL),
        (7.0, None, 6.0, ResultFlag.HIGH),
        (3.0, 4.0, None, ResultFlag.LOW),
        (None, 4.0, 6.0, ResultFlag.UNKNOWN),
        (5.0, None, None, ResultFlag.UNKNOWN),
    ],
)
def test_compute_flag(value, low, high, expected) -> None:
    assert compute_flag(value, low, high) == expected


# --- qualitative_match / classify (smarter ❔ for non-numeric results) -----------


@pytest.mark.parametrize(
    ("value_text", "ref_text", "matches"),
    [
        ("білувато-сіруватий", "білувато-сіруватий, сіруватий", True),  # one of the options
        ("специфічний", "специфічний", True),  # exact
        ("поодинокі", "поодинокі або не виявлені", True),  # "або"-separated option
        ("слабко виражена", "відсутня або слабко виражена", True),
        ("не виявлені", "не виявлені", True),
        ("у великій кількості", "у великій кількості", True),
        ("виявлені", "не виявлені", False),  # negation must NOT be called normal
        ("ізольована", "відсутня", False),  # abnormal -> stays unknown
        ("в невеликій кількості", "не виявлений", False),
        ("каламутна", None, False),  # no reference to match against
        (None, "не виявлені", False),
    ],
)
def test_qualitative_match(value_text, ref_text, matches) -> None:
    assert qualitative_match(value_text, ref_text) is matches


def test_classify_prefers_numeric_then_qualitative() -> None:
    # Numeric still wins.
    assert classify(7.0, None, 3.9, 6.1, None) == ResultFlag.HIGH
    assert classify(5.0, None, 3.9, 6.1, None) == ResultFlag.NORMAL
    # Qualitative match -> NORMAL; mismatch / negation -> UNKNOWN (never LOW/HIGH).
    assert classify(None, "поодинокі", None, None, "поодинокі або не виявлені") == ResultFlag.NORMAL
    assert classify(None, "виявлені", None, None, "не виявлені") == ResultFlag.UNKNOWN
    assert classify(None, "каламутна", None, None, None) == ResultFlag.UNKNOWN


# --- normalize + alias ----------------------------------------------------------


def test_normalize_collapses_and_casefolds() -> None:
    assert normalize_analyte("  Глюкоза   крові ") == "глюкоза"  # via alias


def test_normalize_aliases_map_to_canonical() -> None:
    assert normalize_analyte("HB") == "гемоглобін"
    assert normalize_analyte("Глюкоза (натще)") == "глюкоза"


def test_normalize_unknown_name_is_its_own_key() -> None:
    assert normalize_analyte("Тестостерон") == "тестостерон"


def test_build_series_groups_aliases_and_sorts() -> None:
    points = [
        p(3, 5.5, analyte="Глюкоза"),
        p(1, 5.0, analyte="Глюкоза крові"),  # alias -> same series
        p(2, 9.0, analyte="Сечовина"),
    ]
    series = build_series(points)
    assert set(series) == {"глюкоза", "сечовина"}
    glucose_dates = [pt.taken_on.day for pt in series["глюкоза"]]
    assert glucose_dates == [1, 3]  # sorted ascending


# --- compute_trend: data sufficiency --------------------------------------------


def test_insufficient_data_single_point() -> None:
    s = compute_trend([p(1, 5.0, 4.0, 6.0)])
    assert s.direction == TrendDirection.INSUFFICIENT_DATA
    assert s.n_points == 1
    assert s.latest is not None and s.latest_flag == ResultFlag.NORMAL
    assert s.delta is None


def test_no_numeric_points() -> None:
    s = compute_trend([p(1, None), p(2, None)])
    assert s.direction == TrendDirection.INSUFFICIENT_DATA
    assert s.n_points == 0
    assert s.latest is None
    assert s.latest_flag == ResultFlag.UNKNOWN


# --- compute_trend: range-relative directions -----------------------------------


def test_stable_in_range() -> None:
    s = compute_trend([p(1, 5.0, 4.0, 6.0), p(2, 5.2, 4.0, 6.0)])
    assert s.direction == TrendDirection.STABLE_IN_RANGE
    assert s.delta == pytest.approx(0.2)


def test_returned_to_range() -> None:
    s = compute_trend([p(1, 7.0, 4.0, 6.0), p(2, 5.0, 4.0, 6.0)])
    assert s.direction == TrendDirection.RETURNED_TO_RANGE


def test_left_range() -> None:
    s = compute_trend([p(1, 5.0, 4.0, 6.0), p(2, 7.0, 4.0, 6.0)])
    assert s.direction == TrendDirection.LEFT_RANGE


def test_approaching_range_both_out() -> None:
    s = compute_trend([p(1, 9.0, 4.0, 6.0), p(2, 7.0, 4.0, 6.0)])
    assert s.direction == TrendDirection.APPROACHING_RANGE


def test_moving_away_both_out() -> None:
    s = compute_trend([p(1, 7.0, 4.0, 6.0), p(2, 9.0, 4.0, 6.0)])
    assert s.direction == TrendDirection.MOVING_AWAY


def test_approaching_range_both_below_low() -> None:
    # both under the lower bound; latest closer -> approaching.
    s = compute_trend([p(1, 1.0, 4.0, 6.0), p(2, 2.0, 4.0, 6.0)])
    assert s.direction == TrendDirection.APPROACHING_RANGE


@pytest.mark.parametrize(
    ("value", "low", "high", "expected"),
    [
        (None, 1.0, 2.0, 0.0),  # no value -> 0
        (2.0, 4.0, 6.0, 2.0),  # below low -> low - value
        (9.0, 4.0, 6.0, 3.0),  # above high -> value - high
        (5.0, 4.0, 6.0, 0.0),  # in range -> 0
    ],
)
def test_distance_outside_branches(value, low, high, expected) -> None:
    point = LabPoint("x", date(2026, 1, 1), value, ref_low=low, ref_high=high)
    assert _distance_outside(point) == expected


def test_stable_out_of_range_equal_distance() -> None:
    s = compute_trend([p(1, 8.0, 4.0, 6.0), p(2, 8.0, 4.0, 6.0)])
    assert s.direction == TrendDirection.STABLE_OUT_OF_RANGE


def test_unknown_range_when_refs_missing() -> None:
    s = compute_trend([p(1, 5.0, None, None), p(2, 6.0, None, None)])
    assert s.direction == TrendDirection.UNKNOWN_RANGE


def test_unknown_range_when_previous_has_no_refs() -> None:
    s = compute_trend([p(1, 5.0, None, None), p(2, 6.0, 4.0, 6.0)])
    assert s.direction == TrendDirection.UNKNOWN_RANGE


def test_one_sided_lower_bound_returned_to_range() -> None:
    # ref_low only: 3 (below) -> 5 (at/above) = returned to range.
    s = compute_trend([p(1, 3.0, 4.0, None), p(2, 5.0, 4.0, None)])
    assert s.direction == TrendDirection.RETURNED_TO_RANGE


def test_latest_used_is_most_recent_by_date_not_insertion_order() -> None:
    s = compute_trend([p(2, 7.0, 4.0, 6.0), p(1, 5.0, 4.0, 6.0)])
    assert s.latest is not None and s.latest.taken_on == date(2026, 1, 2)
    assert s.first_date == date(2026, 1, 1)
    assert s.last_date == date(2026, 1, 2)


# --- polarity (internal only) ---------------------------------------------------


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        (TrendDirection.RETURNED_TO_RANGE, Polarity.IMPROVING),
        (TrendDirection.APPROACHING_RANGE, Polarity.IMPROVING),
        (TrendDirection.LEFT_RANGE, Polarity.WORSENING),
        (TrendDirection.MOVING_AWAY, Polarity.WORSENING),
        (TrendDirection.STABLE_IN_RANGE, Polarity.NEUTRAL),
        (TrendDirection.STABLE_OUT_OF_RANGE, Polarity.NEUTRAL),
        (TrendDirection.UNKNOWN_RANGE, Polarity.UNKNOWN),
        (TrendDirection.INSUFFICIENT_DATA, Polarity.UNKNOWN),
    ],
)
def test_polarity_mapping(direction, expected) -> None:
    assert polarity(direction) == expected
