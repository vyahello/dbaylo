"""Multi-user data isolation — the security boundary that makes the family allow-list safe.

Every by-id lookup a user can reach through a callback MUST be scoped to that user, or one
authorized family member could address another's medication / reminder / concern by guessing an id.
These tests assert the ownership checks on the shared helpers (``get_owned``) and the mutating
concern helpers (``resolve`` / ``rename``) — the sites a forged callback would hit.
"""

from __future__ import annotations

from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import concerns, medications, reminders
from dbaylo.config import Settings, _parse_ids
from dbaylo.db.models import ConditionStatus, User


async def _two_users(session: AsyncSession) -> tuple[User, User]:
    a = User(telegram_id=101, name="A")
    b = User(telegram_id=202, name="B")
    session.add_all([a, b])
    await session.flush()
    return a, b


async def test_medication_get_owned_is_scoped(async_session: AsyncSession) -> None:
    a, b = await _two_users(async_session)
    med, _ = await medications.add_medication(
        async_session, user=a, name="Аспірин", times=[time(8, 0)]
    )
    # The owner sees it; another user does not (a forged id addresses nobody else's row).
    assert await medications.get_owned(async_session, med.id, user_id=a.id) is not None
    assert await medications.get_owned(async_session, med.id, user_id=b.id) is None


async def test_reminder_get_owned_is_scoped(async_session: AsyncSession) -> None:
    a, b = await _two_users(async_session)
    _, created = await medications.add_medication(
        async_session, user=a, name="Аспірин", times=[time(8, 0)]
    )
    reminder_id = created[0].id
    assert await reminders.get_owned(async_session, reminder_id, user_id=a.id) is not None
    assert await reminders.get_owned(async_session, reminder_id, user_id=b.id) is None


async def test_resolve_refuses_another_users_concern(async_session: AsyncSession) -> None:
    a, b = await _two_users(async_session)
    condition = await concerns.add_active(async_session, user=a, name="високий тиск")
    # B cannot resolve A's concern: no mutation, and it is still active for A.
    assert await concerns.resolve(async_session, user_id=b.id, condition_id=condition.id) is None
    assert condition.status == ConditionStatus.ACTIVE
    assert await concerns.count_active(async_session, user_id=a.id) == 1
    # A can resolve their own.
    assert (
        await concerns.resolve(async_session, user_id=a.id, condition_id=condition.id) is not None
    )
    assert condition.status == ConditionStatus.RESOLVED


async def test_rename_refuses_another_users_concern(async_session: AsyncSession) -> None:
    a, b = await _two_users(async_session)
    condition = await concerns.add_active(async_session, user=a, name="оригінальна назва")
    assert (
        await concerns.rename(
            async_session, user_id=b.id, condition_id=condition.id, new_name="зламано"
        )
        is None
    )
    assert condition.name == "оригінальна назва"  # untouched


# --- The allow-list config -----------------------------------------------------


def test_parse_ids_tolerates_messy_input() -> None:
    assert _parse_ids("101, 202 ;303") == (101, 202, 303)
    assert _parse_ids("") == ()
    assert _parse_ids("abc, 5, , 6") == (5, 6)  # non-integers dropped, not a crash


def test_authorized_ids_merges_owner_and_allow_list_dropping_zero() -> None:
    s = Settings(owner_telegram_id=1, allowed_telegram_ids=(2, 3))
    assert s.authorized_ids() == frozenset({1, 2, 3})
    # Unset owner + empty allow-list => fully locked (fail-closed).
    assert Settings(owner_telegram_id=0, allowed_telegram_ids=()).authorized_ids() == frozenset()
    # Owner unset but family listed still works (owner 0 is dropped, not treated as a real id).
    assert Settings(owner_telegram_id=0, allowed_telegram_ids=(2,)).authorized_ids() == frozenset(
        {2}
    )
