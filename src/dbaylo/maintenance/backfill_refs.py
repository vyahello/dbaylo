"""Recover MISSING references + the patient's DATE OF BIRTH on already-confirmed reports.

The first extraction sometimes dropped a reference — a one-sided range, or (the motivating case)
an AGE-STRATIFIED table like ПСА's "<40: <1.4; 40-50: <2.0; …" — and never captured the patient's
DOB. Without them the chart shows "норму не вказано". The original file still has both, so we
RE-EXTRACT each affected report (the prompt now captures the DOB and the whole age table) and fill
the BLANK fields from the fresh read.

Safety:
- Fills BLANKS only (rail #2): a reference / DOB already stored is never overwritten.
- Rows matched by EXACT printed name, only when unique on BOTH sides (ambiguous -> skipped).
- The age table is stored verbatim in ``ref_text``; the band is then resolved by age at READ time
  (``load_series_points``) — we never bake a guessed numeric threshold here.
- Idempotent: a now-filled row/report is skipped on a re-run.

    python -m dbaylo.maintenance.backfill_refs --dry-run   # show fills, write nothing
    python -m dbaylo.maintenance.backfill_refs             # apply (back up the DB first!)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.db import get_session
from dbaylo.db.models import LabReport, LabResult, ReportStatus
from dbaylo.labs.extraction import ExtractionFailed, ExtractionOutcome, extract_document
from dbaylo.labs.schema import ExtractedReport
from dbaylo.maintenance.reextract_flagged import RowView, _norm, _to_view, _unique_index


def _has_ref(row: RowView) -> bool:
    return row.ref_low is not None or row.ref_high is not None or bool((row.ref_text or "").strip())


def plan_ref_fills(
    db_rows: Sequence[RowView], fresh: ExtractedReport
) -> dict[int, tuple[float | None, float | None, str | None]]:
    """{row_id: (ref_low, ref_high, ref_text)} for currently reference-LESS rows whose unique fresh
    match has one. Numeric AND age-table (ref_text) references are recovered; blanks only."""
    fresh_idx = _unique_index(fresh.results)
    db_counts = Counter(_norm(r.analyte) for r in db_rows)
    fills: dict[int, tuple[float | None, float | None, str | None]] = {}
    for r in db_rows:
        if _has_ref(r):  # already has a reference
            continue
        key = _norm(r.analyte)
        if db_counts[key] != 1:  # ambiguous in the DB -> skip rather than guess
            continue
        fa = fresh_idx.get(key)
        if fa is None:
            continue
        if fa.ref_low is not None or fa.ref_high is not None or (fa.ref_text or "").strip():
            fills[r.id] = (fa.ref_low, fa.ref_high, fa.ref_text)
    return fills


async def _reports_to_backfill(session: AsyncSession) -> list[int]:
    """Confirmed reports with a source file that are missing the DOB OR have a value row with no
    reference at all — the ones a re-extraction can enrich."""
    reports = (
        await session.scalars(
            select(LabReport).where(
                LabReport.status == ReportStatus.CONFIRMED,
                LabReport.source_file.is_not(None),
            )
        )
    ).all()
    out: list[int] = []
    for report in reports:
        rows = (
            await session.scalars(select(LabResult).where(LabResult.report_id == report.id))
        ).all()
        missing_ref = any(r.value is not None and not _has_ref(_to_view(r)) for r in rows)
        if report.birth_date is None or missing_ref:
            out.append(report.id)
    return out


async def _backfill_one(session: AsyncSession, report_id: int, *, dry_run: bool) -> tuple[int, str]:
    report = await session.get(LabReport, report_id)
    if report is None or not report.source_file:
        return 0, f"report#{report_id}: no source file"
    rows = (await session.scalars(select(LabResult).where(LabResult.report_id == report_id))).all()
    outcome: ExtractionOutcome = await extract_document(report.source_file)
    if isinstance(outcome, ExtractionFailed):
        return 0, f"report#{report_id}: re-extraction failed ({outcome.reason})"
    ref_fills = plan_ref_fills([_to_view(r) for r in rows], outcome)
    got_dob = report.birth_date is None and outcome.birth_date is not None
    if not ref_fills and not got_dob:
        return 0, f"report#{report_id}: nothing new"
    if got_dob:
        print(f"    DOB -> {outcome.birth_date}")
    by_id = {r.id: r for r in rows}
    for row_id, (low, high, text) in ref_fills.items():
        shown = text or f"{low}–{high}"
        print(f"    {by_id[row_id].analyte} -> reference {shown!r}")
    if not dry_run:
        if got_dob:
            report.birth_date = outcome.birth_date
        for row_id, (low, high, text) in ref_fills.items():
            res = by_id[row_id]
            res.ref_low, res.ref_high, res.ref_text = low, high, text
        await session.commit()
    return len(ref_fills) + int(got_dob), f"report#{report_id}: {len(ref_fills)} ref(s)" + (
        " + DOB" if got_dob else ""
    )


async def _run(*, dry_run: bool) -> int:
    async with get_session() as session:
        report_ids = await _reports_to_backfill(session)
        if not report_ids:
            print("Every report already has its DOB and references — nothing to backfill.")
            return 0
        print(f"{len(report_ids)} report(s) to re-extract: {report_ids}")
        total = 0
        for report_id in report_ids:
            print(f"\n→ re-extracting report#{report_id} …")
            filled, status = await _backfill_one(session, report_id, dry_run=dry_run)
            total += filled
            print(f"  {status}")
        print(f"\nDone. {'would fill' if dry_run else 'filled'} {total} item(s).")
        if dry_run:
            print("[dry-run] nothing written.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dbaylo.maintenance.backfill_refs")
    parser.add_argument("--dry-run", action="store_true", help="show fills; write nothing")
    args = parser.parse_args(argv)
    return asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
