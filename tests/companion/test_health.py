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


def _analyte(name, value, low=None, high=None, unit="ммоль/л", section=None):
    return ExtractedAnalyte(
        analyte=name, value=value, unit=unit, ref_low=low, ref_high=high, section=section
    )


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


async def test_watch_flags_an_in_range_value_trending_toward_a_bound(
    async_session: AsyncSession,
) -> None:
    user = await ensure_user(async_session, 1)
    # Гемоглобін: 145 -> 158 (both in 130–160, rising, 158 is within 15% of the 160 bound) -> WATCH.
    await _confirm(
        async_session,
        user,
        day=date(2026, 1, 1),
        analytes=[_analyte("Гемоглобін", 145.0, 130, 160, unit="г/л")],
    )
    await _confirm(
        async_session,
        user,
        day=date(2026, 6, 1),
        analytes=[_analyte("Гемоглобін", 158.0, 130, 160, unit="г/л")],
    )
    picture = await health.analyze_health(async_session, user.id, today=_TODAY)
    assert {f.name for f in picture.watch} == {"Гемоглобін"}  # early warning, not yet a problem
    assert not picture.current  # still in range
    ctx = await health.build_health_context(async_session, user.id, today=_TODAY)
    assert "EARLY WARNING" in ctx and "UPPER limit" in ctx


async def test_stable_in_range_is_not_a_watch(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    # Comfortably mid-range and barely moving -> not a watch.
    await _confirm(
        async_session, user, day=date(2026, 1, 1), analytes=[_analyte("Глюкоза", 4.8, 3.9, 6.1)]
    )
    await _confirm(
        async_session, user, day=date(2026, 6, 1), analytes=[_analyte("Глюкоза", 4.9, 3.9, 6.1)]
    )
    picture = await health.analyze_health(async_session, user.id, today=_TODAY)
    assert not picture.watch and not picture.current


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


async def test_propose_problems_excludes_tracked_and_dismissed(
    async_session: AsyncSession,
) -> None:
    user = await ensure_user(async_session, 1)
    await _confirm(
        async_session,
        user,
        day=date(2026, 6, 2),
        analytes=[_analyte("Глюкоза", 7.0, 3.9, 6.1), _analyte("Сечовина", 12.0, 2.5, 8.3)],
    )
    proposed = {f.name for f in await health.propose_problems(async_session, user.id, today=_TODAY)}
    assert proposed == {"Глюкоза", "Сечовина"}  # the agent proposes both out-of-range findings

    # Track one, wave the other off -> neither is proposed again.
    await concerns.add_active(async_session, user=user, name="Глюкоза")
    await concerns.dismiss(async_session, user=user, name="Сечовина")
    again = {f.name for f in await health.propose_problems(async_session, user.id, today=_TODAY)}
    assert again == set()


async def test_same_analyte_in_blood_and_urine_are_distinct_problems(
    async_session: AsyncSession,
) -> None:
    # The owner's complaint: "Еритроцити" — blood or urine? They are SEPARATE findings, and tracking
    # one must not silently suppress the other.
    user = await ensure_user(async_session, 1)
    await _confirm(
        async_session,
        user,
        day=date(2026, 6, 2),
        analytes=[
            _analyte("Еритроцити", 6.5, 4.0, 5.5, unit="млн/мкл", section="Загальний аналіз крові"),
            _analyte("Еритроцити", 25, 0, 20, unit="у п/з", section="Загальний аналіз сечі"),
        ],
    )
    findings = {
        f.specimen: f for f in await health.propose_problems(async_session, user.id, today=_TODAY)
    }
    assert set(findings) == {"blood", "urine"}  # two distinct specimens
    assert findings["blood"].display_name == "Еритроцити"  # blood stays bare
    assert findings["urine"].display_name == "Еритроцити (сеча)"  # urine is disambiguated

    # Track the BLOOD one (under its display name) -> the URINE one is STILL proposed.
    await concerns.add_active(async_session, user=user, name=findings["blood"].display_name)
    still = await health.propose_problems(async_session, user.id, today=_TODAY)
    assert [f.specimen for f in still] == ["urine"]  # urine not suppressed by the blood concern


async def test_dismissed_flag_stops_the_data_driven_checkin(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    await _confirm(
        async_session, user, day=date(2026, 6, 2), analytes=[_analyte("Глюкоза", 7.0, 3.9, 6.1)]
    )
    assert await health.should_have_checkin(async_session, user.id, today=_TODAY)
    # The user waves the only out-of-range finding off -> no concern, no data-driven check-in.
    await concerns.dismiss(async_session, user=user, name="Глюкоза")
    assert not await health.has_current_flags(async_session, user.id, today=_TODAY)
    assert not await health.should_have_checkin(async_session, user.id, today=_TODAY)


async def test_relevant_dismissed_drops_a_resolved_dismissal(async_session: AsyncSession) -> None:
    # «🙈 Приховані» must only list dismissals that are STILL off — a waved-off finding whose value
    # has since returned to range is stale (restoring it would do nothing), so it is omitted.
    user = await ensure_user(async_session, 1)
    await _confirm(
        async_session, user, day=date(2026, 6, 2), analytes=[_analyte("Глюкоза", 7.0, 3.9, 6.1)]
    )
    await concerns.dismiss(async_session, user=user, name="Глюкоза")
    relevant = await health.list_relevant_dismissed(async_session, user.id, today=_TODAY)
    assert [c.name for c in relevant] == ["Глюкоза"]  # still off -> shown

    # A newer in-range result -> the dismissal is now stale and no longer listed.
    await _confirm(
        async_session, user, day=date(2026, 6, 20), analytes=[_analyte("Глюкоза", 5.0, 3.9, 6.1)]
    )
    relevant = await health.list_relevant_dismissed(async_session, user.id, today=_TODAY)
    assert relevant == []  # returned to range -> not shown


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
