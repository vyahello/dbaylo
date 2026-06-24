"""The proactive indicator-note warmer (``companion.notewarm``).

It fills the persistent note cache for the owner's indicators so every chart / table / PDF carries
an educational description AND renders without a claude call. These tests stub
``describe_indicator`` (no real LLM) and point the warmer's ``get_session`` at the test session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import notecache, notewarm
from dbaylo.db.models import LabReport, LabResult, ReportStatus, User
from dbaylo.labs.humanize import note_cache_key
from dbaylo.labs.trends import compute_flag


async def _user(session: AsyncSession) -> User:
    user = User(telegram_id=777, name="Test")
    session.add(user)
    await session.flush()
    return user


async def _numeric_trend(session: AsyncSession, user_id: int, analyte: str = "Гемоглобін") -> None:
    """One blood analyte measured on two dates — a real numeric trend, so it is a charted indicator
    whose note the warmer should fill."""
    for d, v in [(date(2023, 1, 1), 140.0), (date(2023, 2, 1), 145.0)]:
        session.add(
            LabReport(
                user_id=user_id,
                report_date=d,
                lab="ДІЛА",
                status=ReportStatus.CONFIRMED,
                results=[
                    LabResult(
                        analyte=analyte,
                        value=v,
                        ref_low=130.0,
                        ref_high=160.0,
                        section="Загальний аналіз крові",
                        flag=compute_flag(v, 130.0, 160.0),
                    )
                ],
            )
        )
    await session.flush()


@pytest.fixture
def _patch_session(async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncSession:
    """Point the warmer's ``get_session`` at the test session (it opens its own sessions)."""

    @asynccontextmanager
    async def fake() -> AsyncIterator[AsyncSession]:
        yield async_session

    monkeypatch.setattr(notewarm, "get_session", fake)
    return async_session


async def test_warm_generates_and_persists_a_missing_note(
    _patch_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _patch_session
    user = await _user(session)
    await _numeric_trend(session, user.id)

    items = await notewarm._collect_note_items(user.id)  # the (title, specimen) the PDF keys on
    assert len(items) == 1
    title, spec = items[0]

    calls: list[tuple[str, str | None]] = []

    async def stub(t: str, *, specimen: str | None = None) -> str:
        calls.append((t, specimen))
        return f"опис: {t}"

    monkeypatch.setattr(notewarm, "describe_indicator", stub)

    warmed = await notewarm.warm_user_notes(user.id)
    assert warmed == 1
    assert calls == [(title, spec)]  # the one missing indicator was generated
    key = note_cache_key(spec, title)
    cached = await notecache.fetch_cached(session, [key])
    assert cached[key] == f"опис: {title}"  # and persisted, so the PDF needs no claude call


async def test_warm_skips_an_already_cached_note(
    _patch_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _patch_session
    user = await _user(session)
    await _numeric_trend(session, user.id)

    title, spec = (await notewarm._collect_note_items(user.id))[0]
    await notecache.store_many(session, {note_cache_key(spec, title): "вже є"})
    await session.commit()

    calls: list[str] = []

    async def stub(t: str, *, specimen: str | None = None) -> str:
        calls.append(t)
        return "не має значення"

    monkeypatch.setattr(notewarm, "describe_indicator", stub)

    warmed = await notewarm.warm_user_notes(user.id)
    assert warmed == 0 and calls == []  # nothing missing -> no claude call at all


async def test_warm_on_no_indicators_is_a_noop(
    _patch_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _patch_session
    user = await _user(session)  # no reports at all

    async def boom(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("must not generate when there are no indicators")

    monkeypatch.setattr(notewarm, "describe_indicator", boom)
    assert await notewarm.warm_user_notes(user.id) == 0


async def test_warm_retries_a_transiently_failed_note(
    _patch_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real-world bug: during the warm burst some notes hit a transient claude blip, returned "",
    # and were lost. A later round (this loop) must retry ONLY the still-missing ones and recover.
    monkeypatch.setattr(notewarm, "_WARM_BACKOFF_S", 0)  # no real wait in the test
    session = _patch_session
    user = await _user(session)
    await _numeric_trend(session, user.id, analyte="Гемоглобін")  # succeeds round 1
    await _numeric_trend(session, user.id, analyte="Еритроцити")  # fails round 1, succeeds round 2

    fail_once = {"Еритроцити"}

    async def stub(t: str, *, specimen: str | None = None) -> str:
        if t in fail_once:
            fail_once.discard(t)
            return ""  # one transient failure, then it works
        return f"опис: {t}"

    monkeypatch.setattr(notewarm, "describe_indicator", stub)

    warmed = await notewarm.warm_user_notes(user.id)
    assert warmed == 2  # both eventually persisted (one on a retry round)
    keys = [note_cache_key("blood", a) for a in ("Гемоглобін", "Еритроцити")]
    cached = await notecache.fetch_cached(session, keys)
    assert set(cached) == set(keys)


async def test_warm_purges_stale_version_orphans(
    _patch_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A previous persona version's notes carry an old version tag and are never read again; the warm
    # drops them so they don't linger. A current-version note is kept.
    session = _patch_session
    user = await _user(session)
    await _numeric_trend(session, user.id)
    title, spec = (await notewarm._collect_note_items(user.id))[0]
    await notecache.store_many(
        session,
        {
            "1\x1furine\x1fстарий показник": "застаріла нотатка",  # v1 orphan
            note_cache_key(spec, title): "поточна нотатка",  # current (v2) note
        },
    )
    await session.commit()

    async def stub(t: str, *, specimen: str | None = None) -> str:
        return f"опис: {t}"

    monkeypatch.setattr(notewarm, "describe_indicator", stub)
    await notewarm.warm_user_notes(user.id)

    remaining = await notecache.fetch_cached(
        session, ["1\x1furine\x1fстарий показник", note_cache_key(spec, title)]
    )
    assert "1\x1furine\x1fстарий показник" not in remaining  # orphan purged
    assert remaining[note_cache_key(spec, title)] == "поточна нотатка"  # current kept
