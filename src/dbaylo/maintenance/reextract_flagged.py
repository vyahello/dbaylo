"""Recover dropped qualitative values on already-confirmed reports by re-reading the file.

Some rows were stored *flagged* (the lab boxed them) but with NO value and NO value_text — the
extractor recorded "this is out of range" yet dropped the boxed WORD itself (e.g.
'некласифіковані'). The render then shows ⚠️ next to a bare '—', which hides the real reason. The
word lives only in the original file, so we RE-EXTRACT each affected report (with the improved
prompt that now captures boxed qualitative words) and fill the BLANK cells from the fresh read.

Safety:
- We only ever FILL BLANKS — a value the user already confirmed is never overwritten (rail #2).
- Rows are matched to the fresh read by EXACT printed name (casefold + collapsed spaces), and only
  when that name is unique on BOTH sides — so 'Бактерії' and 'Бактерії (диференціювання)' can never
  be confused, and an ambiguous row is skipped rather than guessed.
- Idempotent: a now-filled row is no longer blank, so a re-run skips it. Safe to resume.

    python -m dbaylo.maintenance.reextract_flagged --dry-run   # show fills, write nothing
    python -m dbaylo.maintenance.reextract_flagged             # apply (back up the DB first!)
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.db import get_session
from dbaylo.db.models import LabReport, LabResult, ReportStatus
from dbaylo.labs.extraction import ExtractionFailed, ExtractionOutcome, extract_document
from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport
from dbaylo.labs.trends import compute_flag

_WS = re.compile(r"\s+")


def _norm(name: str) -> str:
    """Match key: casefold + collapse whitespace, NO aliasing. Within one report the same physical
    row keeps its printed name across extractions, so an exact-name match is both safe and precise —
    it keeps 'Бактерії' and 'Бактерії (диференціювання)' apart."""
    return _WS.sub(" ", name).strip().casefold()


def _is_blank(value: float | None, value_text: str | None) -> bool:
    """A row that currently renders as a bare '—' — nothing for the user to read."""
    return value is None and not (value_text or "").strip()


@dataclass(frozen=True)
class RowView:
    """A confirmed lab row, as far as this recovery cares (decouples planning from the ORM)."""

    id: int
    analyte: str
    value: float | None
    value_text: str | None
    unit: str | None
    ref_low: float | None
    ref_high: float | None
    ref_text: str | None
    flagged: bool


@dataclass(frozen=True)
class RowFill:
    """The fields a fresh re-extraction can fill on one blank DB row (only the currently-blank
    ones are set; the rest stay None = leave the stored value untouched)."""

    row_id: int
    analyte: str
    flagged: bool
    value: float | None
    value_text: str | None
    unit: str | None
    ref_low: float | None
    ref_high: float | None
    ref_text: str | None

    def shows_value(self) -> bool:
        """True if this fill recovers something the user can actually read (a number or a word)."""
        return self.value is not None or bool(self.value_text)


def _unique_index(rows: Sequence[ExtractedAnalyte]) -> dict[str, ExtractedAnalyte]:
    """Fresh rows keyed by normalized name, keeping ONLY names that are unique in the read — a
    duplicated name is ambiguous and must not be matched."""
    counts = Counter(_norm(a.analyte) for a in rows)
    return {_norm(a.analyte): a for a in rows if counts[_norm(a.analyte)] == 1}


def plan_fills(db_rows: Sequence[RowView], fresh: ExtractedReport) -> list[RowFill]:
    """For each currently-blank DB row, the fields a unique fresh match can fill. Only blanks are
    filled, and only when the fresh read actually recovers a value/value_text."""
    fresh_idx = _unique_index(fresh.results)
    db_counts = Counter(_norm(r.analyte) for r in db_rows)
    fills: list[RowFill] = []
    for r in db_rows:
        if not _is_blank(r.value, r.value_text):
            continue
        key = _norm(r.analyte)
        if db_counts[key] != 1:  # ambiguous in the DB -> skip rather than guess
            continue
        fa = fresh_idx.get(key)
        if fa is None:
            continue
        fill = RowFill(
            row_id=r.id,
            analyte=r.analyte,
            flagged=r.flagged,
            value=fa.value,
            value_text=fa.value_text,
            unit=fa.unit if not (r.unit or "").strip() else None,
            ref_low=fa.ref_low if r.ref_low is None else None,
            ref_high=fa.ref_high if r.ref_high is None else None,
            ref_text=fa.ref_text if not (r.ref_text or "").strip() else None,
        )
        if fill.shows_value():
            fills.append(fill)
    return fills


def _to_view(r: LabResult) -> RowView:
    return RowView(
        id=r.id,
        analyte=r.analyte,
        value=r.value,
        value_text=r.value_text,
        unit=r.unit,
        ref_low=r.ref_low,
        ref_high=r.ref_high,
        ref_text=r.ref_text,
        flagged=bool(r.flagged),
    )


async def _reports_to_recover(session: AsyncSession) -> list[int]:
    """Confirmed reports that have ≥1 silently-flagged row (flagged, but no value and no
    value_text) — the ones whose ⚠️ currently shows a bare '—'."""
    stmt = (
        select(LabResult.report_id)
        .join(LabReport, LabResult.report_id == LabReport.id)
        .where(
            LabReport.status == ReportStatus.CONFIRMED,
            LabResult.flagged.is_(True),
            LabResult.value.is_(None),
        )
    )
    ids: set[int] = set()
    for (report_id,) in (await session.execute(stmt)).all():
        ids.add(report_id)
    # narrow the value_text test in Python (an empty string is blank too)
    confirmed: list[int] = []
    for report_id in sorted(ids):
        rows = (
            await session.scalars(select(LabResult).where(LabResult.report_id == report_id))
        ).all()
        if any(r.flagged and _is_blank(r.value, r.value_text) for r in rows):
            confirmed.append(report_id)
    return confirmed


def _apply(rows: Sequence[LabResult], fills: Sequence[RowFill]) -> None:
    """Write the planned fills onto the ORM rows (blanks only; the flag is recomputed when a numeric
    value is recovered, but the lab's `flagged` verdict is never lowered)."""
    by_id = {f.row_id: f for f in fills}
    for res in rows:
        fill = by_id.get(res.id)
        if fill is None:
            continue
        if res.value is None and fill.value is not None:
            res.value = fill.value
        if not (res.value_text or "").strip() and fill.value_text:
            res.value_text = fill.value_text
        if not (res.unit or "").strip() and fill.unit:
            res.unit = fill.unit
        if res.ref_low is None and fill.ref_low is not None:
            res.ref_low = fill.ref_low
        if res.ref_high is None and fill.ref_high is not None:
            res.ref_high = fill.ref_high
        if not (res.ref_text or "").strip() and fill.ref_text:
            res.ref_text = fill.ref_text
        if res.value is not None:
            res.flag = compute_flag(res.value, res.ref_low, res.ref_high)


async def _recover_one(session: AsyncSession, report_id: int, *, dry_run: bool) -> tuple[int, str]:
    """Re-extract one report and fill its blanks. Returns (#rows filled, status line)."""
    report = await session.get(LabReport, report_id)
    if report is None:
        return 0, f"report#{report_id}: vanished"
    if not report.source_file:
        return 0, f"report#{report_id}: no source file — cannot re-extract"
    rows = (await session.scalars(select(LabResult).where(LabResult.report_id == report_id))).all()
    outcome: ExtractionOutcome = await extract_document(report.source_file)
    if isinstance(outcome, ExtractionFailed):
        return 0, f"report#{report_id}: re-extraction failed ({outcome.reason})"
    fills = plan_fills([_to_view(r) for r in rows], outcome)
    if not fills:
        return 0, f"report#{report_id}: nothing recoverable"
    flagged_n = sum(1 for f in fills if f.flagged)
    for f in fills:
        shown = f.value_text or (f"{f.value:g}" if f.value is not None else "—")
        mark = " ⚠️" if f.flagged else ""
        print(f"    {f.analyte} -> {shown!r}{mark}")
    if not dry_run:
        _apply(rows, fills)
        await session.commit()
    return len(fills), (f"report#{report_id}: {len(fills)} filled ({flagged_n} were flagged ⚠️)")


async def _run(*, dry_run: bool) -> int:
    async with get_session() as session:
        report_ids = await _reports_to_recover(session)
        if not report_ids:
            print("No reports have a silently-flagged row — nothing to recover.")
            return 0
        print(f"{len(report_ids)} report(s) to re-extract: {report_ids}")
        total = 0
        for report_id in report_ids:
            print(f"\n→ re-extracting report#{report_id} …")
            filled, status = await _recover_one(session, report_id, dry_run=dry_run)
            total += filled
            print(f"  {status}")
        verb = "would fill" if dry_run else "filled"
        print(f"\nDone. {verb} {total} row(s) across {len(report_ids)} report(s).")
        if dry_run:
            print("[dry-run] nothing written.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dbaylo.maintenance.reextract_flagged")
    parser.add_argument("--dry-run", action="store_true", help="show fills; write nothing")
    args = parser.parse_args(argv)
    return asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
