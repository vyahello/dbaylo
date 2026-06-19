"""Check-in: parsing, symptom -> triage routing, persistence, single no-nag nudge."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion.checkin import (
    build_prompt,
    has_checkin_on,
    parse_checkin,
    process_checkin,
    should_send_nudge,
)
from dbaylo.db.models import CheckIn, User
from dbaylo.triage.types import Symptom


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=7, name="Test")
    session.add(user)
    await session.flush()
    return user


def test_build_prompt_is_safe_and_nonempty() -> None:
    assert build_prompt().strip()


def test_parse_extracts_fields() -> None:
    parsed = parse_checkin("спав 7 годин, випив 2 л води, настрій 4, трохи побігав")
    assert parsed.sleep_hours == 7
    assert parsed.water_ml == 2000
    assert parsed.mood == 4
    assert parsed.training == "так"
    assert parsed.symptoms == frozenset()


def test_parse_millilitres_and_no_training() -> None:
    parsed = parse_checkin("спав 6 годин, 1500 мл води, настрій 3")
    assert parsed.water_ml == 1500
    assert parsed.training is None


def test_parse_picks_up_symptoms() -> None:
    parsed = parse_checkin("погано спав, температура і озноб")
    assert Symptom.FEVER in parsed.symptoms and Symptom.CHILLS in parsed.symptoms


async def test_process_persists_checkin(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    result = await process_checkin(async_session, user=user, text="спав 8 годин, настрій 5")
    assert not result.escalated
    count = await async_session.scalar(select(func.count()).select_from(CheckIn))
    assert count == 1


async def test_process_routes_symptoms_to_triage(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    result = await process_checkin(
        async_session, user=user, text="кепсько: температура, озноб і біль у боці"
    )
    assert result.escalated
    # The triage message is deterministic; it always escalates toward care.
    assert "екстрену" in result.message or "швидку" in result.message
    row = await async_session.scalar(select(CheckIn))
    assert row is not None and row.symptoms is not None
    assert "fever" in row.symptoms


async def test_single_no_nag_logic(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    today = date(2026, 6, 19)
    # No check-in yet -> a nudge is due.
    assert await should_send_nudge(async_session, user_id=user.id, day=today)
    await process_checkin(async_session, user=user, text="спав 7 годин", check_date=today)
    # Once a check-in exists, the nudge is suppressed (never nags).
    assert await has_checkin_on(async_session, user_id=user.id, day=today)
    assert not await should_send_nudge(async_session, user_id=user.id, day=today)
