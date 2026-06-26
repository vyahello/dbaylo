"""Smart routing (#3): deterministic detection of a 'question about MY data'."""

from __future__ import annotations

from dbaylo.companion import dataquery
from dbaylo.companion.health import HealthFinding


def _f(name: str, *, series_key: str = "k", specimen: str = "blood") -> HealthFinding:
    return HealthFinding(
        name=name,
        value="",
        ref="",
        flag_text="",
        direction="STABLE",
        last_date=None,
        n_points=1,
        series_key=series_key,
        specimen=specimen,
    )


def test_is_data_question() -> None:
    assert dataquery.is_data_question("чому залізо низьке?")
    assert dataquery.is_data_question("що з моїми аналізами")
    assert dataquery.is_data_question("розкажи про рівень глюкози")
    assert not dataquery.is_data_question("люблю шпинат")
    assert not dataquery.is_data_question("залізо")  # a bare noun, no ask


def test_match_indicator_basic() -> None:
    findings = [_f("Залізо", series_key="blood\x1fзалізо")]
    match = dataquery.match_indicator("чому в мене низьке залізо?", findings)
    assert match is not None and match.series_key == "blood\x1fзалізо"


def test_match_indicator_tolerates_ukrainian_inflection() -> None:
    findings = [_f("Залізо")]
    assert dataquery.match_indicator("що із залізом?", findings) is not None  # instrumental
    assert dataquery.match_indicator("який рівень заліза?", findings) is not None  # genitive


def test_match_indicator_understands_lay_aliases() -> None:
    findings = [_f("Глюкоза")]
    # "цукор" is the everyday word for glucose -> maps to the глюкоз stem.
    assert dataquery.match_indicator("високий цукор, що робити?", findings) is not None


def test_match_indicator_requires_a_question() -> None:
    findings = [_f("Залізо")]
    assert dataquery.match_indicator("приймаю залізо щодня", findings) is None


def test_match_indicator_returns_none_without_a_named_indicator() -> None:
    findings = [_f("Залізо")]
    assert dataquery.match_indicator("чому я постійно втомлений?", findings) is None
    assert dataquery.match_indicator("що з аналізами?", []) is None


def test_match_indicator_prefers_the_more_specific_name() -> None:
    # The text names two analytes; the longer (more specific) stem wins.
    findings = [_f("Залізо", series_key="fe"), _f("Гемоглобін", series_key="hgb")]
    match = dataquery.match_indicator("розкажи про залізо і гемоглобін", findings)
    assert match is not None and match.series_key == "hgb"


def test_match_indicator_keeps_the_most_interesting_on_a_tie() -> None:
    # Same name in two specimens (findings come most-interesting first); the first wins on a tie.
    findings = [
        _f("Глюкоза", series_key="blood\x1fглюкоза", specimen="blood"),
        _f("Глюкоза", series_key="urine\x1fглюкоза", specimen="urine"),
    ]
    match = dataquery.match_indicator("що з моєю глюкозою?", findings)
    assert match is not None and match.series_key == "blood\x1fглюкоза"
