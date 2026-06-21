"""The one-off lab-name backfill rewrites only the rows that are not already canonical."""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.db.models import LabReport, ReportStatus, User
from dbaylo.maintenance.normalize_labs import find_relabels


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=1, name="T")
    session.add(user)
    await session.flush()
    return user


async def _report(session: AsyncSession, *, user_id: int, lab: str | None) -> LabReport:
    report = LabReport(
        user_id=user_id, report_date=date(2021, 1, 1), lab=lab, status=ReportStatus.CONFIRMED
    )
    session.add(report)
    await session.flush()
    return report


async def test_find_relabels_targets_only_non_canonical(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    drifted = await _report(async_session, user_id=user.id, lab="Синево (Synevo), Львів")
    canonical = await _report(async_session, user_id=user.id, lab="Сінево, Львів")
    unknown = await _report(async_session, user_id=user.id, lab="Медцентр Св. Параскеви")
    await _report(async_session, user_id=user.id, lab=None)

    changes = dict(await find_relabels(async_session))
    assert changes == {drifted: "Сінево, Львів"}  # only the drifted one is rewritten
    assert canonical not in changes  # already canonical
    assert unknown not in changes  # unknown brand untouched


async def test_brand_without_city_inherits_the_most_common_city(
    async_session: AsyncSession,
) -> None:
    user = await _user(async_session)
    # Three "Сінево, Львів" + one "Київ" -> Львів is the mode. A bare "Сінево" inherits Львів.
    for _ in range(3):
        await _report(async_session, user_id=user.id, lab="Синево, Львів")
    await _report(async_session, user_id=user.id, lab="Сінево, Київ")
    bare = await _report(async_session, user_id=user.id, lab="Сінево")
    changes = dict(await find_relabels(async_session))
    assert changes[bare] == "Сінево, Львів"  # missing city filled from the brand's most common


async def test_bare_brand_with_no_known_city_is_left_alone(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    # No other Сінево report carries a city, so there is nothing to inherit.
    only = await _report(async_session, user_id=user.id, lab="Сінево")
    assert dict(await find_relabels(async_session)).get(only) is None


async def test_find_relabels_is_idempotent(async_session: AsyncSession) -> None:
    user = await _user(async_session)
    report = await _report(async_session, user_id=user.id, lab="Synevo")
    for report_obj, canon in await find_relabels(async_session):
        report_obj.lab = canon
    await async_session.flush()
    assert report.lab == "Сінево"
    assert await find_relabels(async_session) == []  # a second pass finds nothing
