"""Backfill: re-derive numeric reference bounds on confirmed rows from their stored ``ref_text``.

``parse_ref_range`` runs at extraction time, but a row saved before a PARSER FIX keeps whatever the
old parser derived. The motivating case: the Ukrainian range spelling "від N до M" used to match
only the upper "до M" and SILENTLY DROP the lower bound N — so the row stored ``ref_high`` but a
``ref_low`` of ``None``, a value below the lower limit (anaemia / hypokalaemia) was never flagged,
and the chart drew no lower red zone. This one-off RE-PARSES each row's stored ``ref_text`` and
FILLS a currently-missing bound from it — so the owner's EXISTING labs recover the dropped bound
without a re-upload.

Purely deterministic — NO LLM, no re-extraction, no network (unlike ``backfill_refs``, which
re-reads the source file to fill rows that have NO reference at all; this one only re-parses text
already stored, so it reaches the "has a wrong upper-only bound" rows that ``backfill_refs`` skips).

Safety:
- FILLS blanks only (rail #2): a ``ref_low`` / ``ref_high`` already stored is NEVER overwritten;
  only a currently-``None`` bound is filled from the re-parse, so a value the user saw at confirm is
  untouched and a genuinely one-sided reference stays one-sided.
- Reads only the row's OWN ``ref_text`` — never guesses across rows.
- Idempotent: a re-run re-parses the same text and finds nothing new to fill.

    python -m dbaylo.maintenance.reparse_bounds --dry-run   # show fills, write nothing
    python -m dbaylo.maintenance.reparse_bounds             # apply + commit
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.db import get_session
from dbaylo.db.models import LabReport, LabResult, ReportStatus
from dbaylo.labs.refparse import parse_ref_range


@dataclass(frozen=True)
class BoundFill:
    """A single row whose re-parsed reference recovers a currently-missing bound."""

    row_id: int
    analyte: str
    old: tuple[float | None, float | None]
    new: tuple[float | None, float | None]
    ref_text: str


def plan_bound_fill(
    *,
    row_id: int,
    analyte: str,
    ref_low: float | None,
    ref_high: float | None,
    ref_text: str | None,
) -> BoundFill | None:
    """Re-parse ``ref_text``; return a fill iff it recovers a currently-``None`` bound WITHOUT
    overwriting one already set. ``None`` when there is nothing to fill (both bounds present, no
    numeric reference in the text, or the re-parse adds nothing). Pure & deterministic."""
    text = (ref_text or "").strip()
    if not text:
        return None
    if ref_low is not None and ref_high is not None:
        return None  # both bounds already present — nothing to recover
    low, high = parse_ref_range(text)
    new_low = ref_low if ref_low is not None else low
    new_high = ref_high if ref_high is not None else high
    if (new_low, new_high) == (ref_low, ref_high):
        return None  # the re-parse recovered nothing new
    return BoundFill(row_id, analyte, (ref_low, ref_high), (new_low, new_high), text)


async def find_bound_fills(session: AsyncSession) -> list[BoundFill]:
    """Confirmed-report rows whose stored ``ref_text`` re-parses to a bound they are missing.
    Read-only. Scoped to CONFIRMED reports (the ones actually shown/charted)."""
    rows = (
        await session.scalars(
            select(LabResult)
            .join(LabReport, LabResult.report_id == LabReport.id)
            .where(
                LabReport.status == ReportStatus.CONFIRMED,
                LabResult.ref_text.is_not(None),
            )
        )
    ).all()
    fills: list[BoundFill] = []
    for r in rows:
        fill = plan_bound_fill(
            row_id=r.id,
            analyte=r.analyte,
            ref_low=r.ref_low,
            ref_high=r.ref_high,
            ref_text=r.ref_text,
        )
        if fill is not None:
            fills.append(fill)
    return fills


async def _run(*, dry_run: bool) -> int:
    async with get_session() as session:
        fills = await find_bound_fills(session)
        if not fills:
            print("Every reference already has the bounds its text implies — nothing to reparse.")
            return 0
        print(f"{len(fills)} row(s) to fix:")
        for f in fills:
            print(f"    #{f.row_id} {f.analyte}: {f.old} -> {f.new}   [{f.ref_text!r}]")
        if not dry_run:
            by_id = {f.row_id: f for f in fills}
            rows = (
                await session.scalars(select(LabResult).where(LabResult.id.in_(list(by_id))))
            ).all()
            for r in rows:
                r.ref_low, r.ref_high = by_id[r.id].new
            await session.commit()
        print(f"\nDone. {'would fix' if dry_run else 'fixed'} {len(fills)} row(s).")
        if dry_run:
            print("[dry-run] nothing written.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dbaylo.maintenance.reparse_bounds")
    parser.add_argument("--dry-run", action="store_true", help="show fixes; write nothing")
    args = parser.parse_args(argv)
    return asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
