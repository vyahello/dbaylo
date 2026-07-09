"""Deterministic re-parse backfill: recover a dropped reference bound from a row's stored ref_text.

Pure planning (``plan_bound_fill``) + the async DB scan (``find_bound_fills``) over confirmed rows.
The motivating bug: "від N до M" used to store only ``ref_high``; re-parsing the same text now
recovers the ``ref_low`` — a fill, never an overwrite."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.labs.intake import create_pending_report, ensure_user, persist_confirmed
from dbaylo.labs.schema import ExtractedAnalyte
from dbaylo.maintenance.reparse_bounds import find_bound_fills, plan_bound_fill


def _plan(rid, low, high, text):
    return plan_bound_fill(row_id=rid, analyte="X", ref_low=low, ref_high=high, ref_text=text)


def test_recovers_the_dropped_lower_bound() -> None:
    # The bug: an old parse of "від 3.9 до 6.1" stored ref_high=6.1 but ref_low=None. A re-parse of
    # the SAME text now recovers ref_low=3.9 — a fill of the missing bound.
    fill = _plan(1, None, 6.1, "від 3.9 до 6.1")
    assert fill is not None
    assert fill.new == (3.9, 6.1) and fill.old == (None, 6.1)


def test_never_overwrites_a_present_bound() -> None:
    # Both bounds already present -> nothing to do, even if the text would re-parse to the same.
    assert _plan(1, 3.9, 6.1, "3.9-6.1") is None
    # A genuinely one-sided reference stays one-sided: "< 5.2" re-parses to (None, 5.2) which fills
    # nothing new (ref_high already 5.2), and MUST NOT invent a lower bound.
    assert _plan(1, None, 5.2, "< 5.2") is None


def test_ignores_non_numeric_or_empty_text() -> None:
    assert _plan(1, None, None, "негативно") is None
    assert _plan(1, None, None, "") is None
    assert _plan(1, None, None, None) is None


async def test_find_bound_fills_scans_confirmed_rows(async_session: AsyncSession) -> None:
    # A confirmed row stored with only the upper bound but a two-sided ref_text is picked up; a
    # fully-bounded row is not.
    user = await ensure_user(async_session, 1)
    report = await create_pending_report(async_session, user=user, file_path=Path("/tmp/r.png"))
    await persist_confirmed(
        async_session,
        report=report,
        analytes=[
            # Simulate the old-parser state: ref_text carries both bounds, but only ref_high stored.
            ExtractedAnalyte(
                analyte="Гемоглобін", value=120.0, ref_high=160.0, ref_text="від 130 до 160"
            ),
            ExtractedAnalyte(
                analyte="Глюкоза", value=5.0, ref_low=3.9, ref_high=6.1, ref_text="3.9-6.1"
            ),
        ],
        report_date=date(2023, 5, 2),
        lab="Synevo",
    )
    fills = await find_bound_fills(async_session)
    assert len(fills) == 1
    assert fills[0].analyte == "Гемоглобін" and fills[0].new == (130.0, 160.0)
