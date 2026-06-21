"""Lab pipeline orchestration + a --dry-run CLI.

Wires the pieces that run *after* user confirmation: load the confirmed series,
compute trends in code, render charts, and humanize. The interactive
confirmation itself lives in the bot (``dbaylo.bot.lab_flow``).

The ``--dry-run`` CLI runs extraction over a file and prints the parsed JSON
without touching the DB or Telegram — the required no-persist path.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.db.models import LabReport, LabResult, ReportStatus
from dbaylo.labs.charts import render_trend_chart
from dbaylo.labs.extraction import ExtractionFailed, extract_document
from dbaylo.labs.humanize import humanize, interpret
from dbaylo.labs.schema import ExtractedReport
from dbaylo.labs.trends import LabPoint, TrendSummary, build_series, compute_flag, compute_trend


@dataclass
class ReportSummary:
    """What the bot sends back after a confirmed report: the expert text + how many analytes
    have a real trend worth charting (the charts themselves are rendered on demand)."""

    text: str
    chart_count: int  # analytes with measurements on >=2 distinct dates (drives the 📈 button)


async def load_series_points(session: AsyncSession, user_id: int) -> list[LabPoint]:
    """Load all confirmed, dated results for a user as engine input points."""
    stmt = (
        select(
            LabResult.analyte,
            LabReport.report_date,
            LabResult.value,
            LabResult.unit,
            LabResult.ref_low,
            LabResult.ref_high,
        )
        .join(LabReport, LabResult.report_id == LabReport.id)
        .where(
            LabReport.user_id == user_id,
            LabReport.status == ReportStatus.CONFIRMED,
            LabReport.report_date.is_not(None),
        )
    )
    rows = (await session.execute(stmt)).all()
    return [
        LabPoint(
            analyte=row.analyte,
            taken_on=row.report_date,
            value=row.value,
            unit=row.unit,
            ref_low=row.ref_low,
            ref_high=row.ref_high,
        )
        for row in rows
    ]


async def compute_report_summary(
    session: AsyncSession,
    *,
    user_id: int,
    analyte_keys: set[str],
    report: ExtractedReport | None = None,
) -> ReportSummary:
    """Compute trends + charts + a summary for the given analyte keys.

    When ``report`` is given (the just-confirmed report), the text is the Stage 5 expert
    interpretation (values + the lab's own flags + trends + guidance); otherwise it is the
    plain trend humanization.
    """
    series = build_series(await load_series_points(session, user_id))
    summaries: list[TrendSummary] = [
        compute_trend(points) for key, points in series.items() if key in analyte_keys
    ]
    summaries.sort(key=lambda s: s.analyte.casefold())

    # Only analytes measured on >=2 DISTINCT dates have a real trend worth a chart — a same-day
    # re-upload (two points on one date) is not a trend. Charts are rendered on demand (the 📈
    # button), so the analysis is never buried under a flood of images.
    chart_count = sum(1 for s in summaries if _has_trend(series[s.key]))

    text = await interpret(report, summaries) if report is not None else await humanize(summaries)
    return ReportSummary(text=text, chart_count=chart_count)


def _has_trend(points: list[LabPoint]) -> bool:
    """A real trend needs measurements on at least two different dates."""
    return len({p.taken_on for p in points}) >= 2


async def render_one_chart(
    session: AsyncSession, *, user_id: int, key: str, title: str
) -> bytes | None:
    """Render the trend chart (PNG) for a SINGLE analyte key, on demand from the picker — so the
    user sees one indicator at a time instead of a wall of images. None if it has no real trend."""
    series = build_series(await load_series_points(session, user_id))
    points = series.get(key)
    if points and _has_trend(points):
        return render_trend_chart(points, title=title)
    return None


async def render_report_charts(
    session: AsyncSession, *, user_id: int, analyte_keys: set[str]
) -> list[tuple[str, bytes]]:
    """Render trend charts (PNG) for the report's analytes that have a real trend. Deterministic,
    no LLM — built on demand when the user taps 📈, so a confirm never dumps a wall of images."""
    series = build_series(await load_series_points(session, user_id))
    charts: list[tuple[str, bytes]] = []
    for key in sorted(analyte_keys):
        points = series.get(key)
        if points and _has_trend(points):
            summary = compute_trend(points)
            charts.append((summary.analyte, render_trend_chart(points, title=summary.analyte)))
    return charts


# --- Dry-run CLI ----------------------------------------------------------------


def _report_to_dict(report: ExtractedReport) -> dict[str, object]:
    data = dataclasses.asdict(report)
    data["report_date"] = report.report_date.isoformat() if report.report_date else None
    # Annotate each row with the deterministically computed flag.
    for row, analyte in zip(data["results"], report.results, strict=True):
        row["flag"] = compute_flag(analyte.value, analyte.ref_low, analyte.ref_high).value
    return data


async def _dry_run(path: str, model: str | None) -> int:
    models = (model,) if model else ("sonnet", "opus")
    outcome = await extract_document(path, models=models)
    if isinstance(outcome, ExtractionFailed):
        print(json.dumps({"error": outcome.reason}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(_report_to_dict(outcome), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dbaylo.labs.pipeline")
    parser.add_argument("--dry-run", action="store_true", help="extract only; do not persist")
    parser.add_argument("--model", default=None, help="override the extraction model")
    parser.add_argument("file", help="path to a lab photo or PDF")
    args = parser.parse_args(argv)

    if not args.dry_run:
        parser.error("only --dry-run is supported from the CLI")
    return asyncio.run(_dry_run(args.file, args.model))


if __name__ == "__main__":
    sys.exit(main())
