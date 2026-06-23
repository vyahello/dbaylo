"""Async intake + persistence + pipeline tests (no network, no Telegram)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import dbaylo.labs.pipeline as pipeline_mod
from dbaylo.config import Settings
from dbaylo.db.models import ReportStatus, ResultFlag
from dbaylo.labs.intake import (
    create_pending_report,
    ensure_user,
    file_hash,
    find_confirmed_by_hash,
    persist_confirmed,
    save_original_file,
)
from dbaylo.labs.pipeline import (
    compute_report_summary,
    load_series_points,
    render_report_charts,
)
from dbaylo.labs.schema import ExtractedAnalyte
from dbaylo.labs.trends import series_key

_GLU = series_key(None, "Глюкоза")  # the composite series key the pipeline now uses


def _analyte(name, value, low, high, unit="ммоль/л"):
    return ExtractedAnalyte(analyte=name, value=value, unit=unit, ref_low=low, ref_high=high)


def test_file_hash_is_stable_and_distinguishing() -> None:
    assert file_hash(b"same bytes") == file_hash(b"same bytes")
    assert file_hash(b"a") != file_hash(b"b")
    assert len(file_hash(b"x")) == 64  # sha256 hex digest


async def test_find_confirmed_by_hash_only_matches_a_confirmed_dup(async_session) -> None:
    user = await ensure_user(async_session, 1)
    confirmed_hash = file_hash(b"the-pdf")
    rep = await create_pending_report(
        async_session, user=user, file_path=Path("/tmp/a.pdf"), content_hash=confirmed_hash
    )
    await persist_confirmed(
        async_session,
        report=rep,
        analytes=[_analyte("Глюкоза", 5.0, 3.9, 6.1)],
        report_date=date(2026, 1, 1),
        lab="Synevo",
    )
    found = await find_confirmed_by_hash(
        async_session, user_id=user.id, content_hash=confirmed_hash
    )
    assert found is not None and found.id == rep.id

    # a different file -> not a duplicate
    assert (
        await find_confirmed_by_hash(
            async_session, user_id=user.id, content_hash=file_hash(b"other")
        )
        is None
    )

    # a PENDING (un-confirmed) upload with a hash must NOT count as a duplicate
    pend_hash = file_hash(b"pending-only")
    await create_pending_report(
        async_session, user=user, file_path=Path("/tmp/b.pdf"), content_hash=pend_hash
    )
    assert (
        await find_confirmed_by_hash(async_session, user_id=user.id, content_hash=pend_hash) is None
    )

    # another user's confirmed report with the same bytes is not THIS user's duplicate
    other = await ensure_user(async_session, 2)
    assert (
        await find_confirmed_by_hash(async_session, user_id=other.id, content_hash=confirmed_hash)
        is None
    )


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
    assert report.lab == "Сінево"  # canonicalized on write
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


async def test_age_table_overrides_bad_stored_bounds(async_session) -> None:
    # The PSA bug: extraction once stored the AGE span "40-50" as the band (40, 50). The lab's age
    # table in ref_text + the patient's DOB must OVERRIDE that at read time -> the real <1.4 band
    # for a 30-y-o, so a healthy 0.58 is in range (was painted red on a 0-50 axis).
    user = await ensure_user(async_session, 1)
    report = await create_pending_report(async_session, user=user, file_path=Path("/tmp/psa.png"))
    psa = ExtractedAnalyte(
        analyte="ПСА",
        value=0.58,
        unit="нг/мл",
        ref_low=40.0,  # the WRONG band a mis-parse stored
        ref_high=50.0,
        ref_text="<40 років: <1.4; 40-50 років: <2.0; 50-60 років: <3.1; ≥70 років: <4.4",
    )
    await persist_confirmed(
        async_session,
        report=report,
        analytes=[psa],
        report_date=date(2023, 4, 18),
        lab="Synevo",
        birth_date=date(1993, 3, 24),
    )
    point = next(p for p in await load_series_points(async_session, user.id) if p.analyte == "ПСА")
    assert (point.ref_low, point.ref_high) == (None, 1.4)  # resolved by age, not the stored 40-50


async def test_sex_split_band_uses_the_patients_sex(async_session) -> None:
    # A CBC adult row is itself sex-split ("Дорослі: Чоловіки …; Жінки …"). With the report's sex,
    # the right band is picked at read time — not the child range a flat parse had stored.
    user = await ensure_user(async_session, 1)
    report = await create_pending_report(async_session, user=user, file_path=Path("/tmp/rbc.png"))
    rbc = ExtractedAnalyte(
        analyte="Еритроцити",
        value=4.5,
        unit="10^12/л",
        ref_low=3.5,  # a stored child-band mis-parse
        ref_high=4.7,
        ref_text="Діти: 1-6 років: 3.5 - 4.5; 6-12 років: 3.5 - 4.7; "
        "Дорослі: Чоловіки: 4.0 - 5.0; Жінки: 3.7 - 4.7",
    )
    await persist_confirmed(
        async_session,
        report=report,
        analytes=[rbc],
        report_date=date(2023, 4, 18),
        lab="Synevo",
        birth_date=date(1993, 3, 24),
        sex="m",
    )
    point = next(
        p for p in await load_series_points(async_session, user.id) if p.analyte == "Еритроцити"
    )
    assert (point.ref_low, point.ref_high) == (4.0, 5.0)  # the MALE adult band


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

    summary = await compute_report_summary(async_session, user_id=user.id, analyte_keys={_GLU})
    assert summary.text == "Підсумок українською."
    assert summary.chart_count == 1  # one analyte measured on two distinct dates

    charts = await render_report_charts(async_session, user_id=user.id, analyte_keys={_GLU})
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

    summary = await compute_report_summary(async_session, user_id=user.id, analyte_keys={_GLU})
    assert summary.chart_count == 0
    charts = await render_report_charts(async_session, user_id=user.id, analyte_keys={_GLU})
    assert charts == []
