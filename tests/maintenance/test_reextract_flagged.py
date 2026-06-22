"""The deterministic core of the flagged-value recovery (blank-only fill, exact-name match)."""

from __future__ import annotations

from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport
from dbaylo.maintenance.reextract_flagged import RowView, plan_fills


def _view(
    row_id: int,
    analyte: str,
    *,
    value: float | None = None,
    value_text: str | None = None,
    unit: str | None = None,
    ref_low: float | None = None,
    ref_high: float | None = None,
    ref_text: str | None = None,
    flagged: bool = False,
) -> RowView:
    return RowView(row_id, analyte, value, value_text, unit, ref_low, ref_high, ref_text, flagged)


def _fresh(*analytes: ExtractedAnalyte) -> ExtractedReport:
    return ExtractedReport(results=list(analytes))


def test_fills_a_silently_flagged_qualitative_row_and_keeps_siblings_apart() -> None:
    # The real bug: 'Бактерії (диференціювання)' was flagged but blank; the boxed word is recovered,
    # while the separate numeric 'Бактерії' row must NOT be touched/confused.
    db = [
        _view(1048, "Мікроскопія осаду сечі: Бактерії", value=3.2),  # populated -> untouched
        _view(1049, "Мікроскопія осаду сечі: Бактерії (диференціювання)", flagged=True),  # blank
    ]
    fresh = _fresh(
        ExtractedAnalyte(analyte="Мікроскопія осаду сечі: Бактерії", value=3.2),
        ExtractedAnalyte(
            analyte="Мікроскопія осаду сечі: Бактерії (диференціювання)",
            value_text="некласифіковані",
            out_of_range=True,
        ),
    )
    fills = plan_fills(db, fresh)
    assert [f.row_id for f in fills] == [1049]  # only the blank flagged row
    assert fills[0].value_text == "некласифіковані"
    assert fills[0].flagged is True


def test_never_overwrites_a_populated_value() -> None:
    db = [_view(1, "Глюкоза", value=5.3)]
    fresh = _fresh(ExtractedAnalyte(analyte="Глюкоза", value=9.9))  # a DIFFERENT (wrong) read
    assert plan_fills(db, fresh) == []  # the confirmed value stands


def test_skips_ambiguous_duplicate_names() -> None:
    # Same printed name twice in the DB (different panels, both blank) -> we cannot tell which fresh
    # row is which, so we skip rather than guess.
    db = [
        _view(1, "Лейкоцити", flagged=True),
        _view(2, "Лейкоцити", flagged=True),
    ]
    fresh = _fresh(ExtractedAnalyte(analyte="Лейкоцити", value_text="поодинокі"))
    assert plan_fills(db, fresh) == []


def test_skips_when_fresh_read_is_also_blank_or_missing() -> None:
    db = [
        _view(1, "Циліндри (гіалінові)", flagged=True),  # fresh read also has no value
        _view(2, "Атипові клітини", flagged=True),  # not in the fresh read at all
    ]
    fresh = _fresh(ExtractedAnalyte(analyte="Циліндри (гіалінові)", value=None, value_text=None))
    assert plan_fills(db, fresh) == []


def test_is_idempotent_once_filled() -> None:
    # A row that already carries the recovered word is no longer blank -> not re-planned.
    db = [_view(1, "Бактерії (диференціювання)", value_text="некласифіковані", flagged=True)]
    fresh = _fresh(
        ExtractedAnalyte(analyte="Бактерії (диференціювання)", value_text="некласифіковані")
    )
    assert plan_fills(db, fresh) == []


def test_recovers_numeric_value_and_fills_blank_reference() -> None:
    db = [_view(1, "Слиз", flagged=False)]  # blank in-range row showing '—'
    fresh = _fresh(ExtractedAnalyte(analyte="Слиз", value=0.0, unit="мг/л", ref_high=2.0))
    fills = plan_fills(db, fresh)
    assert len(fills) == 1
    assert fills[0].value == 0.0 and fills[0].unit == "мг/л" and fills[0].ref_high == 2.0
