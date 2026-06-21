"""Backfill: canonicalize the stored lab brand on existing ``LabReport`` rows.

``normalize_lab()`` runs on write and on read, but rows saved before a normalizer change keep
whatever the extractor first wrote (e.g. ``Синево (Synevo), Львів``). This one-off rewrites them
to the canonical spelling (``Сінево, Львів``) so the history list and the dynamics / lab filters
are consistent at the data level too. It also fills in a MISSING city from the most common city
of the same brand (``Сінево`` → ``Сінево, Львів`` when that lab's other reports say Львів), so a
report where the lab printed no city still groups with the rest. Idempotent (a second run finds
nothing); the nightly off-box backup is the safety net.

    python -m dbaylo.maintenance.normalize_labs --dry-run   # show changes, write nothing
    python -m dbaylo.maintenance.normalize_labs             # apply + commit
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.db import get_session
from dbaylo.db.models import LabReport
from dbaylo.labs.labnames import normalize_lab


def _split(lab: str) -> tuple[str, str]:
    """Brand (before the first comma) and city (after), both trimmed; city may be empty."""
    brand, _, rest = lab.partition(",")
    return brand.strip(), rest.strip()


async def find_relabels(session: AsyncSession) -> list[tuple[LabReport, str]]:
    """Reports whose stored lab is not yet canonical, paired with the target value. Read-only.

    Target = canonical brand (``normalize_lab``), then — when the brand carries no city — the most
    common city seen for that same brand (per user), so ``Сінево`` becomes ``Сінево, Львів``.
    """
    reports = (await session.scalars(select(LabReport).where(LabReport.lab.is_not(None)))).all()
    canon = {r.id: (normalize_lab(r.lab) or r.lab or "") for r in reports}

    # Most common city per (user, canonical brand), from the reports that DO carry a city.
    cities: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    for r in reports:
        brand, city = _split(canon[r.id])
        if city:
            cities[(r.user_id, brand)][city] += 1

    changes: list[tuple[LabReport, str]] = []
    for r in reports:
        target = canon[r.id]
        brand, city = _split(target)
        if not city and (counter := cities.get((r.user_id, brand))):
            target = f"{brand}, {counter.most_common(1)[0][0]}"
        if target and target != r.lab:
            changes.append((r, target))
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
