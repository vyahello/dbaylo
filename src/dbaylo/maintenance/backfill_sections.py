"""Backfill: tag a few GENERIC panel sections with their specimen so the trend engine groups
them correctly.

Most rows already carry a clear section ("Загальний аналіз сечі", "Спермограма", "Біохімічний
аналіз крові", …) that :func:`dbaylo.labs.trends.specimen` maps to blood / urine / semen. A handful
of sub-panels are named generically — "Фізичні властивості", "Мікроскопічне дослідження",
"Фізико-хімічні властивості", "Кінезисграма", "Фарбування за Блумом" — and carry no specimen
keyword, so the classifier falls back to *blood*. That mis-files a urine/semen reading under blood
in the dynamics browser (and could, in principle, merge a same-named reading across specimens).

A generic name alone is ambiguous ("Фізичні властивості" is urine in a urinalysis but semen in a
spermogram), so this resolves it from the REPORT CONTEXT — the specimen of the report's other,
unambiguous sections — which the per-row classifier cannot see. It only rewrites a row that
currently keys to blood, sits under one of those generic sections, AND belongs to a report whose
non-blood specimen is unambiguous (semen-only or urine-only). A blood test that merely shares a
report with a urine panel (ПСА, гормони) is never touched. Idempotent; the nightly backup is safety.

    python -m dbaylo.maintenance.backfill_sections --dry-run   # show changes, write nothing
    python -m dbaylo.maintenance.backfill_sections             # apply + commit
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.db import get_session
from dbaylo.db.models import LabReport, LabResult, ReportStatus
from dbaylo.labs.trends import specimen

# Generic sub-panel names that carry no specimen keyword (lower-cased for matching).
_GENERIC_SECTIONS: frozenset[str] = frozenset(
    {
        "фізичні властивості",
        "фізико-хімічні властивості",
        "мікроскопічне дослідження",
        "кінезисграма",
        "фарбування за блумом",
    }
)

# Canonical specimen prefix applied to a generic section so it classifies correctly while keeping
# the original sub-panel name for display.
_PREFIX = {"semen": "Спермограма", "urine": "Аналіз сечі"}


@dataclass(frozen=True)
class RowView:
    """A confirmed lab row, as far as this backfill cares."""

    id: int
    analyte: str
    section: str | None


def report_specimen(rows: list[RowView]) -> str | None:
    """The report's single NON-blood specimen, inferred from its section keywords, or None when it
    is blood-only / mixed / unknown. A semen-only report -> "semen"; a urine-only report -> "urine";
    a report carrying both (or neither) is ambiguous and returns None (we then change nothing)."""
    text = " ".join((r.section or "") for r in rows).casefold()
    has_semen = "сперм" in text or "еякулят" in text
    has_urine = "сеч" in text
    if has_semen and not has_urine:
        return "semen"
    if has_urine and not has_semen:
        return "urine"
    return None


def plan_report(rows: list[RowView]) -> list[tuple[int, str]]:
    """(row id, new section) for the generic-section rows of ONE report that should be re-tagged."""
    spec = report_specimen(rows)
    if spec is None:
        return []
    prefix = _PREFIX[spec]
    changes: list[tuple[int, str]] = []
    for r in rows:
        section = (r.section or "").strip()
        if not section or section.casefold() not in _GENERIC_SECTIONS:
            continue
        # Only touch rows the classifier currently mis-files as blood (a sperm-count row is already
        # semen via its analyte name and must keep its key).
        if specimen(section, r.analyte) != "blood":
            continue
        changes.append((r.id, f"{prefix}: {section}"))
    return changes


async def find_section_backfills(session: AsyncSession) -> list[tuple[int, str, str]]:
    """(row id, old section, new section) across all confirmed reports. Read-only."""
    stmt = (
        select(LabResult.id, LabResult.analyte, LabResult.section, LabResult.report_id)
        .join(LabReport, LabResult.report_id == LabReport.id)
        .where(LabReport.status == ReportStatus.CONFIRMED)
    )
    rows = (await session.execute(stmt)).all()
    by_report: dict[int, list[RowView]] = defaultdict(list)
    sections: dict[int, str | None] = {}
    for row in rows:
        by_report[row.report_id].append(RowView(row.id, row.analyte, row.section))
        sections[row.id] = row.section
    out: list[tuple[int, str, str]] = []
    for report_rows in by_report.values():
        for row_id, new_section in plan_report(report_rows):
            out.append((row_id, sections[row_id] or "", new_section))
    return out


async def _run(*, dry_run: bool) -> int:
    async with get_session() as session:
        changes = await find_section_backfills(session)
        if not changes:
            print("Every section is already specimen-clear — nothing to do.")
            return 0
        print(f"{len(changes)} row(s) to re-tag:")
        for row_id, old, new in changes:
            print(f"  - LabResult#{row_id}: {old!r} -> {new!r}")
        if dry_run:
            print("\n[dry-run] nothing written.")
            return 0
        ids = [row_id for row_id, _, _ in changes]
        results = (await session.scalars(select(LabResult).where(LabResult.id.in_(ids)))).all()
        new_by_id = {row_id: new for row_id, _, new in changes}
        for result in results:
            result.section = new_by_id[result.id]
        await session.commit()
        print(f"\nUpdated {len(results)} row(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dbaylo.maintenance.backfill_sections")
    parser.add_argument("--dry-run", action="store_true", help="show changes; write nothing")
    args = parser.parse_args(argv)
    return asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
