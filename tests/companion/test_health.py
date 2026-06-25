"""Deterministic health analyzer (``companion.health``) — current vs resolved findings.

No LLM: it reads the lab history through the trend engine and states, in DATA terms only, what's
currently out of range and what was off before but is now back. These are async DB tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import concerns, health
from dbaylo.labs.intake import create_pending_report, ensure_user, persist_confirmed
from dbaylo.labs.schema import ExtractedAnalyte

_TODAY = date(2026, 6, 25)


def _analyte(name, value, low=None, high=None, unit="ммоль/л"):
    return ExtractedAnalyte(analyte=name, value=value, unit=unit, ref_low=low, ref_high=high)


async def _confirm(session, user, *, day: date, analytes):
    report = await create_pending_report(session, user=user, file_path=Path(f"/tmp/{day}.png"))
    await persist_confirmed(
        session, report=report, analytes=analytes, report_date=day, lab="Synevo"
    )
    return report


async def test_analyze_splits_current_and_resolved(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    # Холестерин: 6.2 (>5.2, OUT) then 5.0 (in range) -> RESOLVED (was off, latest fine).
    await _confirm(
        async_session, user, day=date(2026, 1, 1), analytes=[_analyte("Холестерин", 6.2, None, 5.2)]
    )
    await _confirm(
        async_session, user, day=date(2026, 6, 1), analytes=[_analyte("Холестерин", 5.0, None, 5.2)]
    )
    # Глюкоза: latest 7.0 (>6.1) -> CURRENT.
    await _confirm(
        async_session, user, day=date(2026, 6, 2), analytes=[_analyte("Глюкоза", 7.0, 3.9, 6.1)]
    )

    picture = await health.analyze_health(async_session, user.id, today=_TODAY)
    current_names = {f.name for f in picture.current}
    resolved_names = {f.name for f in picture.resolved}
    assert "Глюкоза" in current_names  # latest out of range -> current
    assert "Холестерин" in resolved_names  # was off, latest in range -> resolved
    assert "Холестерин" not in current_names


async def test_has_current_flags_and_should_have_checkin(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    assert not await health.has_current_flags(async_session, user.id, today=_TODAY)
    assert not await health.should_have_checkin(async_session, user.id, today=_TODAY)

    # A flagged latest value -> a check-in is warranted even with NO manually added concern.
    await _confirm(
        async_session, user, day=date(2026, 6, 2), analytes=[_analyte("Глюкоза", 7.0, 3.9, 6.1)]
    )
    assert await health.has_current_flags(async_session, user.id, today=_TODAY)
    assert await health.should_have_checkin(async_session, user.id, today=_TODAY)


async def test_should_have_checkin_true_for_a_tracked_concern_without_labs(
    async_session: AsyncSession,
) -> None:
    user = await ensure_user(async_session, 1)
    await concerns.add_active(async_session, user=user, name="Камені в нирках")
    assert await health.should_have_checkin(async_session, user.id, today=_TODAY)


async def test_build_health_context_lists_current_and_resolved(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    await _confirm(
        async_session, user, day=date(2026, 1, 1), analytes=[_analyte("Холестерин", 6.2, None, 5.2)]
    )
    await _confirm(
        async_session, user, day=date(2026, 6, 1), analytes=[_analyte("Холестерин", 5.0, None, 5.2)]
    )
    await _confirm(
        async_session, user, day=date(2026, 6, 2), analytes=[_analyte("Глюкоза", 7.0, 3.9, 6.1)]
    )
    ctx = await health.build_health_context(async_session, user.id, today=_TODAY)
    assert "CURRENTLY out-of-range" in ctx and "Глюкоза" in ctx
    assert "back in range" in ctx and "Холестерин" in ctx


async def test_build_health_context_empty_without_data(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    assert await health.build_health_context(async_session, user.id, today=_TODAY) == ""
