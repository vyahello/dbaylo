"""Backfill: canonicalize the stored lab brand on existing ``LabReport`` rows.

``normalize_lab()`` runs on write and on read, but rows saved before a normalizer change keep
whatever the extractor first wrote (e.g. ``Синево (Synevo), Львів``). This one-off rewrites them
to the canonical spelling (``Сінево, Львів``) so the history list and the dynamics / lab filters
are consistent at the data level too. Idempotent (a second run finds nothing); the nightly
off-box backup is the safety net.

    python -m dbaylo.maintenance.normalize_labs --dry-run   # show changes, write nothing
    python -m dbaylo.maintenance.normalize_labs             # apply + commit
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.db import get_session
from dbaylo.db.models import LabReport
from dbaylo.labs.labnames import normalize_lab


async def find_relabels(session: AsyncSession) -> list[tuple[LabReport, str]]:
    """Reports whose stored lab is not yet canonical, paired with the canonical value. Read-only."""
    reports = (await session.scalars(select(LabReport).where(LabReport.lab.is_not(None)))).all()
    changes: list[tuple[LabReport, str]] = []
    for report in reports:
        canon = normalize_lab(report.lab)
        if canon is not None and canon != report.lab:
            changes.append((report, canon))
    return changes


async def _run(*, dry_run: bool) -> int:
    async with get_session() as session:
        changes = await find_relabels(session)
        if not changes:
            print("All lab names already canonical — nothing to do.")
            return 0
        print(f"{len(changes)} report(s) to relabel:")
        for report, canon in changes:
            print(f"  - LabReport#{report.id}: {report.lab!r} -> {canon!r}")
        if dry_run:
            print("\n[dry-run] nothing written.")
            return 0
        for report, canon in changes:
            report.lab = canon
        await session.commit()
        print(f"\nUpdated {len(changes)} report(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dbaylo.maintenance.normalize_labs")
    parser.add_argument("--dry-run", action="store_true", help="show changes; write nothing")
    args = parser.parse_args(argv)
    return asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
