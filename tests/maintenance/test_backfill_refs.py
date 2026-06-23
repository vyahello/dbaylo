"""Pure planning for the reference / DOB backfill (re-extract -> fill blank references)."""

from __future__ import annotations

from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport
from dbaylo.maintenance.backfill_refs import plan_ref_fills
from dbaylo.maintenance.reextract_flagged import RowView


def _row(
    rid: int, analyte: str, *, value=None, ref_low=None, ref_high=None, ref_text=None
) -> RowView:
    return RowView(rid, analyte, value, None, None, ref_low, ref_high, ref_text, False)


def test_fills_a_missing_age_table_and_a_numeric_ref() -> None:
    db = [
        _row(1, "ПСА", value=0.519),  # has a value, NO reference -> backfill candidate
        _row(2, "Глюкоза", value=5.0),  # also missing a reference
        _row(3, "Калій", value=4.0, ref_low=3.5, ref_high=5.1),  # already has one -> skip
    ]
    fresh = ExtractedReport(
        results=[
            ExtractedAnalyte(analyte="ПСА", value=0.519, ref_text="<40: <1.4; 40-50: <2.0"),
            ExtractedAnalyte(analyte="Глюкоза", value=5.0, ref_low=3.9, ref_high=6.1),
            ExtractedAnalyte(analyte="Калій", value=4.0, ref_low=3.5, ref_high=5.1),
        ]
    )
    fills = plan_ref_fills(db, fresh)
    assert fills[1] == (None, None, "<40: <1.4; 40-50: <2.0")  # the age table, verbatim
    assert fills[2] == (3.9, 6.1, None)  # a numeric ref
    assert 3 not in fills  # the row that already had a reference is untouched


def test_skips_ambiguous_and_unmatched_rows() -> None:
    db = [_row(1, "Лейкоцити", value=5.0), _row(2, "Лейкоцити", value=6.0)]  # ambiguous name in DB
    fresh = ExtractedReport(
        results=[ExtractedAnalyte(analyte="Лейкоцити", value=5.0, ref_low=4.0, ref_high=9.0)]
    )
    assert plan_ref_fills(db, fresh) == {}  # ambiguous -> never guessed
    # A row with no fresh match is left alone.
    assert plan_ref_fills([_row(9, "Невідомий", value=1.0)], fresh) == {}
