"""Goals: parsing, guardrail validation before persistence, listing."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion.goals import list_goals, parse_goal, set_goal
from dbaylo.db.models import Goal, User
from dbaylo.locale import GOAL_ACCEPTED
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


async def test_list_goals(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    assert "ще не" in (await list_goals(async_session, user=user))
    await set_goal(async_session, user=user, text="краще спати")
    listed = await list_goals(async_session, user=user)
    assert "краще спати" in listed
