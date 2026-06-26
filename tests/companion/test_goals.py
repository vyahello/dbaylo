"""Goals: parsing, guardrail validation before persistence, listing."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion.goals import list_goals, parse_goal, set_goal
from dbaylo.db.models import Goal, User
from dbaylo.locale import GOAL_ACCEPTED
from dbaylo.safety import GateSource
from dbaylo.wellness import Concern


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=42, name="Test")
    session.add(user)
    await session.flush()
    return user


def test_parse_weight_loss_rate() -> None:
    spec = parse_goal("хочу схуднути на 10 кг за 2 тижні")
    assert spec.kind == "weight_loss"
    assert spec.loss_kg == 10
    assert spec.weeks == 2
    assert spec.weekly_loss_kg() == pytest.approx(5.0)


def test_parse_from_to_weight() -> None:
    spec = parse_goal("хочу з 90 до 80 кг за місяць")
    assert spec.current_kg == 90
    assert spec.target_kg == 80
    assert spec.weeks == pytest.approx(4.345)


def test_parse_non_weight_goal_is_general() -> None:
    spec = parse_goal("хочу краще спати")
    assert spec.kind == "general"
    assert spec.weekly_loss_kg() is None


async def test_safe_goal_is_persisted(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    result = await set_goal(async_session, user=user, text="хочу пити достатньо води")
    assert result.saved
    assert result.concern == Concern.OK
    assert result.message == GOAL_ACCEPTED
    count = await async_session.scalar(select(func.count()).select_from(Goal))
    assert count == 1


async def test_aggressive_goal_is_redirected_not_persisted(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    result = await set_goal(async_session, user=user, text="схуднути на 10 кг за тиждень")
    assert not result.saved
    assert result.concern == Concern.REDIRECT
    count = await async_session.scalar(select(func.count()).select_from(Goal))
    assert count == 0


async def test_disordered_goal_text_is_supported_not_persisted(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    result = await set_goal(async_session, user=user, text="хочу нічого не їсти цілими днями")
    assert not result.saved
    assert result.concern == Concern.SUPPORT
    count = await async_session.scalar(select(func.count()).select_from(Goal))
    assert count == 0


async def test_goal_naming_a_red_flag_symptom_routes_to_triage(
    async_session: AsyncSession,
) -> None:
    """A goal that mentions a red-flag symptom is a triage escalation, not stored."""
    user = await _user(async_session)
    result = await set_goal(
        async_session, user=user, text="хочу схуднути, бо в мене температура і озноб"
    )
    assert not result.saved
    assert result.source == GateSource.TRIAGE
    assert result.concern is None  # medical escalation, not a wellness concern
    assert "медичну" in result.message or "швидку" in result.message
    count = await async_session.scalar(select(func.count()).select_from(Goal))
    assert count == 0


async def test_list_goals(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    assert "поки немає" in (await list_goals(async_session, user=user))
    await set_goal(async_session, user=user, text="краще спати")
    listed = await list_goals(async_session, user=user)
    assert "краще спати" in listed


async def test_propose_goals_suggests_from_data_and_excludes_existing(
    async_session: AsyncSession,
) -> None:
    from datetime import date
    from pathlib import Path

    from dbaylo.companion import goals as goals_module
    from dbaylo.labs.intake import create_pending_report, persist_confirmed
    from dbaylo.labs.schema import ExtractedAnalyte

    user = await _user(async_session)
    # No labs yet -> only the generic wellness goals are proposed.
    generic = await goals_module.propose_goals(async_session, user.id, today=date(2026, 6, 25))
    assert any("сну" in g.text for g in generic)

    report = await create_pending_report(async_session, user=user, file_path=Path("/tmp/g.png"))
    await persist_confirmed(
        async_session,
        report=report,
        analytes=[
            ExtractedAnalyte(
                analyte="Глюкоза", value=7.0, unit="ммоль/л", ref_low=3.9, ref_high=6.1
            )
        ],
        report_date=date(2026, 6, 2),
        lab="Synevo",
    )
    proposed = await goals_module.propose_goals(async_session, user.id, today=date(2026, 6, 25))
    assert any("Глюкоза" in g.text for g in proposed)  # a data-derived goal for the out-of-range
    glucose = next(g for g in proposed if "Глюкоза" in g.text)
    assert glucose.subject == "Глюкоза" and glucose.series_key  # carries the analyte for the detail

    # Adopting it removes it from future suggestions (and it persisted as a real goal).
    result = await set_goal(async_session, user=user, text=glucose.text)
    assert result.saved
    again = await goals_module.propose_goals(async_session, user.id, today=date(2026, 6, 25))
    assert not any("Глюкоза" in g.text for g in again)  # not re-proposed once it's a goal


async def test_suggested_goals_pass_the_guardrail(async_session: AsyncSession) -> None:
    # Every suggested goal must adopt cleanly (no dose/diet/guardrail trip) — they're agent output.

    from dbaylo.companion import goals as goals_module

    user = await _user(async_session)
    for text in goals_module.GENERIC_GOALS:
        result = await set_goal(async_session, user=user, text=text)
        assert result.saved, f"generic goal rejected: {text!r}"


async def test_achieve_and_remove_change_status_and_stop_re_suggestion(
    async_session: AsyncSession,
) -> None:
    from datetime import date

    from dbaylo.companion import goals as goals_module
    from dbaylo.db.models import GoalStatus

    user = await _user(async_session)
    await set_goal(async_session, user=user, text="Налагодити режим сну")
    await set_goal(async_session, user=user, text="Пити достатньо води щодня")
    active = await goals_module.list_active_goals(async_session, user_id=user.id)
    assert len(active) == 2

    # ✅ achieve one, 🗑 remove the other -> both leave the ACTIVE list.
    achieved = await goals_module.achieve_goal(async_session, goal_id=active[0].id, user_id=user.id)
    removed = await goals_module.remove_goal(async_session, goal_id=active[1].id, user_id=user.id)
    assert achieved.status == GoalStatus.ACHIEVED and removed.status == GoalStatus.ABANDONED
    assert await goals_module.list_active_goals(async_session, user_id=user.id) == []

    # Neither an achieved nor a removed goal is re-suggested (known at any status).
    again = await goals_module.propose_goals(async_session, user.id, today=date(2026, 6, 25))
    assert not any("сну" in g.text or "води" in g.text for g in again)

    # Guard: a goal that isn't this user's is never mutated.
    assert await goals_module.remove_goal(async_session, goal_id=active[0].id, user_id=999) is None


async def test_suggested_goal_name_is_specimen_disambiguated(async_session: AsyncSession) -> None:
    # A urine finding's goal carries its specimen so it's never confused with the blood twin.
    from datetime import date
    from pathlib import Path

    from dbaylo.companion import goals as goals_module
    from dbaylo.labs.intake import create_pending_report, persist_confirmed
    from dbaylo.labs.schema import ExtractedAnalyte

    user = await _user(async_session)
    report = await create_pending_report(async_session, user=user, file_path=Path("/tmp/u.png"))
    await persist_confirmed(
        async_session,
        report=report,
        analytes=[
            ExtractedAnalyte(
                analyte="Еритроцити",
                value=25,
                unit="у п/з",
                ref_low=0,
                ref_high=20,
                section="Загальний аналіз сечі",
            )
        ],
        report_date=date(2026, 6, 2),
        lab="Synevo",
    )
    proposed = await goals_module.propose_goals(async_session, user.id, today=date(2026, 6, 25))
    assert any("Еритроцити (сеча)" in g.text for g in proposed)  # disambiguated, not bare
