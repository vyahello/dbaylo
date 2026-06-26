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
    find_series,
    is_negative_qualitative,
    is_out_of_range,
    normalize_analyte,
    polarity,
    qualitative_match,
    series_key,
    specimen,
)


def p(day, value, low=None, high=None, analyte="Глюкоза", unit="ммоль/л", section=None):
    return LabPoint(
        analyte=analyte,
        taken_on=date(2026, 1, day),
        value=value,
        unit=unit,
        ref_low=low,
        ref_high=high,
        section=section,
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


@pytest.mark.parametrize(
    ("value", "low", "high", "out_of_range", "expected"),
    [
        (7.0, 3.9, 6.1, None, True),  # numeric high, no lab mark
        (5.0, 3.9, 6.1, None, False),  # numeric ok
        (5.0, 3.9, 6.1, True, True),  # lab flags it even though numeric is in range (escalate up)
        (None, None, None, True, True),  # qualitative, lab-flagged
        (None, None, None, False, False),  # qualitative, lab says ok
        (None, None, None, None, False),  # nothing to judge -> not flagged
    ],
)
def test_is_out_of_range(value, low, high, out_of_range, expected) -> None:
    assert is_out_of_range(value, low, high, out_of_range) is expected


@pytest.mark.parametrize(
    ("text", "negative"),
    [
        ("не виявлено", True),
        ("не виявлені", True),
        ("Не виявлено (методом ІФА)", True),  # parens stripped, case-folded
        ("відсутні", True),
        ("відсутній", True),
        ("негативно", True),
        ("негативний", True),
        ("немає", True),
        ("not detected", True),
        ("absent", True),
        ("виявлено", False),  # a POSITIVE find is NOT negative
        ("виявлені поодинокі", False),
        ("10-15 в п/з", False),
        ("солом'яний", False),
        ("", False),
        (None, False),
    ],
)
def test_is_negative_qualitative(text, negative) -> None:
    assert is_negative_qualitative(text) is negative


def test_is_out_of_range_never_flags_a_negative_qualitative() -> None:
    # The reported bug: the SAME 'не виявлено' was painted red in one report (its lab mark captured
    # as out_of_range) and normal in the next. A negative/absence result is the reassuring direction
    # — never flagged, even when the lab's OCR'd indicator says out-of-range.
    assert is_out_of_range(None, None, None, True, "не виявлено") is False
    assert is_out_of_range(None, None, None, True, "відсутні") is False
    # A POSITIVE qualitative the lab flagged is STILL flagged (we suppress only clear negatives).
    assert is_out_of_range(None, None, None, True, "виявлено") is True
    # A numeric out-of-range with a stray text is still flagged (suppression is qualitative-only).
    assert is_out_of_range(7.0, 3.9, 6.1, None, "не виявлено") is True


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


def test_normalize_strips_leading_enumerator_and_unifies_apostrophes() -> None:
    # A list marker ("1. " / "а) ") is layout, not identity; different apostrophes must collapse.
    assert normalize_analyte("1. З нормальною морфологією (%)") == "нормальні форми сперматозоїдів"
    assert normalize_analyte("а) патологія голівки") == "патологія голови"
    assert normalize_analyte("Об’єм в мл") == normalize_analyte("Об'єм")  # U+2019 vs U+0027


def test_spermogram_names_unify_across_labs() -> None:
    # Two labs name the same spermogram parameter differently; they must land in ONE series so the
    # three reports trend together (Сінево "Живі сперматозоїди" == Параскеви "Живі (%)", etc.).
    pairs = [
        ("Об'єм в мл", "Об'єм"),
        ("Живі (%)", "Живі сперматозоїди"),
        ("Мертві (%)", "Мертві сперматозоїди"),
        ("Реакція (pH)", "рН"),
        ("Прогресивна рухливість (%) (a+b)", "Рухливість прогресивна (А+В)"),
        ("Кількість сперматозоїдів в еякуляті", "Загальна кількість сперматозоїдів у еякуляті"),
        ("Кількість сперматозоїдів в 1 мл", "Загальна концентрація сперматозоїдів"),
        ("в) патологія хвоста", "Патологія хвоста"),
        ("Нерухливих (%) (c)", "Нерухомі сперматозоїди (D)"),
    ]
    for a, b in pairs:
        assert series_key("Спермограма", a) == series_key("Спермограма", b), (a, b)
    # ...but genuinely different parameters stay apart (progressive != non-progressive motility).
    assert series_key("Спермограма", "Рухливість прогресивна (А+В)") != series_key(
        "Спермограма", "Рухливість непрогресивна (С)"
    )


def test_build_series_groups_aliases_and_sorts() -> None:
    points = [
        p(3, 5.5, analyte="Глюкоза"),
        p(1, 5.0, analyte="Глюкоза крові"),  # alias -> same series
        p(2, 9.0, analyte="Сечовина"),
    ]
    series = build_series(points)
    assert set(series) == {series_key(None, "Глюкоза"), series_key(None, "Сечовина")}
    glucose_dates = [pt.taken_on.day for pt in series[series_key(None, "Глюкоза")]]
    assert glucose_dates == [1, 3]  # sorted ascending


def test_specimen_keeps_same_name_in_different_fluids_apart() -> None:
    # "Еритроцити" in blood, urine and semen are three different readings — never one chart.
    assert specimen("Загальний аналіз крові", "Еритроцити") == "blood"
    assert specimen("Загальний аналіз сечі", "Еритроцити") == "urine"
    assert specimen("Спермограма", "Еритроцити") == "semen"
    points = [
        p(1, 4.5, analyte="Еритроцити", section="Загальний аналіз крові"),
        p(2, 5.0, analyte="Еритроцити", section="Загальний аналіз крові"),
        p(1, 2.0, analyte="Еритроцити", section="Загальний аналіз сечі"),
        p(2, 3.0, analyte="Еритроцити", section="Загальний аналіз сечі"),
        p(1, 1.0, analyte="Еритроцити", section="Спермограма"),
    ]
    series = build_series(points)
    assert len(series) == 3  # blood / urine / semen — not merged into one
    # A bare-name /trend lookup picks the richest matching series (here a 2-point one).
    found = find_series(series, "еритроцити")
    assert found is not None and len(found) == 2


def test_specimen_falls_back_to_blood_without_a_section() -> None:
    # A section-less row (single-analyte report) stays with its blood twin, not a phantom split.
    assert specimen(None, "Натрій") == "blood"
    assert series_key(None, "Натрій") == series_key("Біохімічний аналіз крові", "Натрій")
    # Semen-specific names are recognised even with no section.
    assert specimen(None, "Кількість сперматозоїдів в еякуляті") == "semen"


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


def test_uses_the_series_reference_when_a_point_lacks_its_own() -> None:
    # A report that did not RE-print the reference is still judged against the one that did — the
    # trend (and the caption) use the most-recent-available ref, matching the chart band, instead of
    # falsely reporting "немає референсних меж".
    # The latest carries the ref; the previous (no ref) is judged by it -> both in range.
    s = compute_trend([p(1, 5.0, None, None), p(2, 6.0, 4.0, 6.0)])
    assert s.direction == TrendDirection.STABLE_IN_RANGE
    # Only an OLDER report carried the ref; the latest value is still judged by it (the Гемоглобін
    # case: latest with no ref, but an earlier report said 4-6 -> out of range, not "no reference").
    s2 = compute_trend([p(1, 9.0, 4.0, 6.0), p(2, 9.0, None, None)])
    assert s2.direction == TrendDirection.STABLE_OUT_OF_RANGE
    assert s2.latest_flag == ResultFlag.HIGH


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


def test_direction_phrase_is_human_and_never_the_raw_token() -> None:
    from dbaylo.labs.trends import direction_phrase

    # Every member maps to a readable, range-relative phrase (no "LEFT_RANGE" leaks to the prompt).
    for direction in TrendDirection:
        phrase = direction_phrase(direction)
        assert phrase and phrase != direction.name
    assert direction_phrase(TrendDirection.LEFT_RANGE) == "moved out of range"
    assert direction_phrase(TrendDirection.RETURNED_TO_RANGE) == "came back into range"
