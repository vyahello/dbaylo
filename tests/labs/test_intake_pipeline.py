"""Async intake + persistence + pipeline tests (no network, no Telegram)."""

from __future__ import annotations

from datetime import date

import dbaylo.labs.pipeline as pipeline_mod
from dbaylo.config import Settings
from dbaylo.db.models import ReportStatus, ResultFlag
from dbaylo.labs.intake import (
    create_pending_report,
    ensure_user,
    persist_confirmed,
    save_original_file,
)
from dbaylo.labs.pipeline import (
    compute_report_summary,
    load_series_points,
    render_report_charts,
)
from dbaylo.labs.schema import ExtractedAnalyte


def _analyte(name, value, low, high, unit="ммоль/л"):
    return ExtractedAnalyte(analyte=name, value=value, unit=unit, ref_low=low, ref_high=high)


async def test_ensure_user_get_or_create(async_session) -> None:
    u1 = await ensure_user(async_session, 555, "Test")
    u2 = await ensure_user(async_session, 555, "Test")
    assert u1.id == u2.id


def test_save_original_file_writes(tmp_path) -> None:
    settings = Settings(storage_dir=tmp_path)
    path = save_original_file(b"hello", user_id=7, suffix=".PNG", settings=settings)
    assert path.exists()
    assert path.read_bytes() == b"hello"
    assert path.suffix == ".png"  # lowercased
    assert path.parent.name == "7"


async def test_pending_then_confirmed_flow(async_session) -> None:
    user = await ensure_user(async_session, 1)
    report = await create_pending_report(
        async_session, user=user, file_path=__import__("pathlib").Path("/tmp/x.png")
    )
    assert report.status == ReportStatus.PENDING

    results = await persist_confirmed(
        async_session,
        report=report,
        analytes=[_analyte("Глюкоза", 7.0, 3.9, 6.1)],
        report_date=date(2026, 5, 1),
        lab="Synevo",
    )
    assert report.status == ReportStatus.CONFIRMED
    assert report.lab == "Synevo"
    assert len(results) == 1
    assert results[0].flag == ResultFlag.HIGH  # 7.0 > 6.1, computed deterministically


async def _confirm(session, user, day, value):
    from pathlib import Path

    report = await create_pending_report(session, user=user, file_path=Path(f"/tmp/{day}.png"))
    await persist_confirmed(
        session,
        report=report,
        analytes=[_analyte("Глюкоза", value, 3.9, 6.1)],
        report_date=date(2026, 1, day),
        lab="Synevo",
    )


async def test_load_series_points_only_confirmed_dated(async_session) -> None:
    user = await ensure_user(async_session, 1)
    await _confirm(async_session, user, 1, 7.0)
    await _confirm(async_session, user, 10, 5.4)
    points = await load_series_points(async_session, user.id)
    assert len(points) == 2
    assert {p.analyte for p in points} == {"Глюкоза"}


async def test_compute_report_summary_counts_real_trends(async_session, monkeypatch) -> None:
    async def fake_humanize(summaries, **kwargs):
        return "Підсумок українською."

    monkeypatch.setattr(pipeline_mod, "humanize", fake_humanize)

    user = await ensure_user(async_session, 1)
    await _confirm(async_session, user, 1, 7.0)
    await _confirm(async_session, user, 10, 5.4)  # a second, DIFFERENT date -> a real trend

    summary = await compute_report_summary(async_session, user_id=user.id, analyte_keys={"глюкоза"})
    assert summary.text == "Підсумок українською."
    assert summary.chart_count == 1  # one analyte measured on two distinct dates

    charts = await render_report_charts(async_session, user_id=user.id, analyte_keys={"глюкоза"})
    assert len(charts) == 1
    assert charts[0][0] == "Глюкоза"
    assert charts[0][1].startswith(b"\x89PNG")


async def test_same_day_reupload_is_not_a_trend(async_session, monkeypatch) -> None:
    # Two measurements on the SAME date (re-uploading the same file) is not a trend -> no chart.
    async def fake_humanize(summaries, **kwargs):
        return "Підсумок."

    monkeypatch.setattr(pipeline_mod, "humanize", fake_humanize)

    user = await ensure_user(async_session, 1)
    await _confirm(async_session, user, 5, 7.0)
    await _confirm(async_session, user, 5, 7.0)  # same day again

    summary = await compute_report_summary(async_session, user_id=user.id, analyte_keys={"глюкоза"})
    assert summary.chart_count == 0
    charts = await render_report_charts(async_session, user_id=user.id, analyte_keys={"глюкоза"})
    assert charts == []
