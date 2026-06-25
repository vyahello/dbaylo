"""Durable cross-session consultation memory (``companion.consult_memory``).

The point of the feature: when a NEW consultation opens, Дбайло recalls what was discussed in
earlier ones — so the grounded context must carry a MEMORY block built from persisted turns. These
are deterministic, DB-only tests (no LLM)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.companion import consult_memory
from dbaylo.companion.consult_context import KIND_REPORT, Subject, build_context
from dbaylo.companion.consult_memory import _RETENTION_ROWS
from dbaylo.companion.scheduler import ReminderScheduler
from dbaylo.db.models import ConsultMemory
from dbaylo.labs.intake import create_pending_report, ensure_user, persist_confirmed
from dbaylo.labs.schema import ExtractedAnalyte

_TODAY = date(2026, 6, 25)


async def _sender(telegram_id: int, text: str, *, buttons: object | None = None) -> None:
    """A no-op sender — the delete path only unschedules jobs, it never delivers."""


@pytest_asyncio.fixture
async def scheduler(async_session: AsyncSession) -> AsyncIterator[ReminderScheduler]:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield async_session

    rs = ReminderScheduler(sender=_sender, session_factory=factory)
    await rs.start()
    yield rs
    rs.shutdown()


def _analyte(name, value, low=None, high=None):
    return ExtractedAnalyte(analyte=name, value=value, unit="ммоль/л", ref_low=low, ref_high=high)


async def _confirmed_report(session: AsyncSession, user):
    report = await create_pending_report(session, user=user, file_path=Path("/tmp/ct.png"))
    await persist_confirmed(
        session,
        report=report,
        analytes=[_analyte("Холестерин", 6.2, None, 5.2)],
        report_date=date(2026, 1, 21),
        lab="Synevo",
    )
    return report


async def test_record_and_recall_are_chronological_and_labelled(
    async_session: AsyncSession,
) -> None:
    user = await ensure_user(async_session, 1)
    await consult_memory.record_turn(async_session, user_id=user.id, role="user", text="перше")
    await consult_memory.record_turn(async_session, user_id=user.id, role="assistant", text="друге")
    turns = await consult_memory.recent_turns(async_session, user_id=user.id)
    assert [t.text for t in turns] == ["перше", "друге"]  # oldest -> newest
    block = consult_memory.format_block(turns)
    assert "MEMORY" in block
    assert "Користувач: перше" in block and "Дбайло: друге" in block


async def test_blank_text_is_not_remembered(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    await consult_memory.record_turn(async_session, user_id=user.id, role="user", text="   ")
    await consult_memory.record_turn(async_session, user_id=user.id, role="user", text="")
    assert await consult_memory.recent_turns(async_session, user_id=user.id) == []


async def test_exclude_drops_lines_already_in_the_live_transcript(
    async_session: AsyncSession,
) -> None:
    user = await ensure_user(async_session, 1)
    await consult_memory.record_turn(async_session, user_id=user.id, role="user", text="давнє")
    await consult_memory.record_turn(async_session, user_id=user.id, role="user", text="свіже")
    block = await consult_memory.recall_block(
        async_session, user_id=user.id, exclude=frozenset({"свіже"})
    )
    assert "давнє" in block and "свіже" not in block


async def test_recall_is_empty_when_nothing_stored(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    assert await consult_memory.recall_block(async_session, user_id=user.id) == ""


async def test_recall_is_scoped_per_user(async_session: AsyncSession) -> None:
    a = await ensure_user(async_session, 1)
    b = await ensure_user(async_session, 2)
    await consult_memory.record_turn(async_session, user_id=a.id, role="user", text="секрет A")
    block_b = await consult_memory.recall_block(async_session, user_id=b.id)
    assert "секрет A" not in block_b  # B never sees A's conversation


async def test_retention_prunes_oldest_rows(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    for i in range(_RETENTION_ROWS + 5):
        await consult_memory.record_turn(
            async_session, user_id=user.id, role="user", text=f"повідомлення {i}"
        )
    total = len(
        (
            await async_session.execute(
                ConsultMemory.__table__.select().where(ConsultMemory.user_id == user.id)
            )
        ).all()
    )
    assert total == _RETENTION_ROWS  # capped — the oldest fell off
    # The newest survive; the very first do not.
    recent = await consult_memory.recent_turns(async_session, user_id=user.id, limit=1)
    assert recent[0].text == f"повідомлення {_RETENTION_ROWS + 4}"


async def test_build_context_injects_the_memory_block(async_session: AsyncSession) -> None:
    # The real payoff: opening a consultation surfaces prior conversation in the grounded context.
    user = await ensure_user(async_session, 1)
    report = await _confirmed_report(async_session, user)
    await consult_memory.record_turn(
        async_session,
        user_id=user.id,
        role="assistant",
        text="Ми обговорювали двобічний нефролітіаз.",
        report_id=report.id,
    )
    built = await build_context(
        async_session, user.id, Subject(kind=KIND_REPORT, report_id=report.id), today=_TODAY
    )
    assert built is not None
    context, _label = built
    assert "MEMORY" in context
    assert "двобічний нефролітіаз" in context  # the prior conversation is recalled


async def test_count_and_clear_all(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    assert await consult_memory.count(async_session, user_id=user.id) == 0
    for i in range(3):
        await consult_memory.record_turn(async_session, user_id=user.id, role="user", text=f"q{i}")
    assert await consult_memory.count(async_session, user_id=user.id) == 3
    deleted = await consult_memory.clear_all(async_session, user_id=user.id)
    assert deleted == 3
    assert await consult_memory.count(async_session, user_id=user.id) == 0
    assert await consult_memory.recall_block(async_session, user_id=user.id) == ""


async def test_clear_all_is_scoped_per_user(async_session: AsyncSession) -> None:
    a = await ensure_user(async_session, 1)
    b = await ensure_user(async_session, 2)
    await consult_memory.record_turn(async_session, user_id=a.id, role="user", text="A")
    await consult_memory.record_turn(async_session, user_id=b.id, role="user", text="B")
    await consult_memory.clear_all(async_session, user_id=a.id)
    assert await consult_memory.count(async_session, user_id=a.id) == 0
    assert await consult_memory.count(async_session, user_id=b.id) == 1  # B untouched


async def test_memory_view_escapes_html_and_truncates(async_session: AsyncSession) -> None:
    from dbaylo.bot.consult_flow import _MEMORY_LINE_CAP, _render_turns

    user = await ensure_user(async_session, 1)
    await consult_memory.record_turn(
        async_session, user_id=user.id, role="user", text="<b>x</b> & y " + "довго" * 100
    )
    turns = await consult_memory.recent_turns(async_session, user_id=user.id)
    rendered = _render_turns("Заголовок", turns, total=1)
    assert "&lt;b&gt;" in rendered and "&amp;" in rendered  # angle brackets / ampersand escaped
    assert "<b>x</b>" not in rendered  # the raw user tag is never injected as markup
    assert "…" in rendered  # the long line is truncated
    assert len("довго" * 100) > _MEMORY_LINE_CAP  # sanity: it really exceeded the cap


def test_hub_group_keyboard_goes_back_to_the_groups_list() -> None:
    # Per-analysis memory is browsed from the 🧠 Памʼять menu (a group's «Назад» returns to the
    # groups list). The report card no longer has its own memory button — memory is folded into
    # «Запитати Дбайло», so there is no separate card-memory navigation to verify.
    from dbaylo.bot.consult_flow import _group_keyboard
    from dbaylo.companion import callbacks

    hub = [b.callback_data for row in _group_keyboard(7).inline_keyboard for b in row]
    assert callbacks.MEMORY_HUB in hub  # back to the groups list
    assert callbacks.memory_forget_one(7) in hub  # forget just this conversation


def test_memory_clean_strips_markup_and_disclaimer() -> None:
    from dbaylo.bot.consult_flow import _clean
    from dbaylo.triage.safety import DISCLAIMER

    raw = "Це *важливо* і _обережно_." + "\n\n" + DISCLAIMER
    cleaned = _clean(raw)
    assert "*" not in cleaned and "_" not in cleaned  # the bold/italic markers are gone
    assert "важливо" in cleaned and "обережно" in cleaned  # the words stay
    assert DISCLAIMER.split("\n")[0] not in cleaned  # the appended disclaimer is dropped


async def test_groups_and_per_report_memory(async_session: AsyncSession) -> None:
    # Memory is grouped by analysis: a report's conversation is its own group, plus a general group
    # for non-anchored chats (report_id None).
    user = await ensure_user(async_session, 1)
    report = await _confirmed_report(async_session, user)
    await consult_memory.record_turn(
        async_session, user_id=user.id, role="user", text="про КТ", report_id=report.id
    )
    await consult_memory.record_turn(
        async_session,
        user_id=user.id,
        role="assistant",
        text="відповідь про КТ",
        report_id=report.id,
    )
    await consult_memory.record_turn(
        async_session, user_id=user.id, role="user", text="загальне питання"
    )  # no report -> general group

    groups = dict(await consult_memory.list_groups(async_session, user_id=user.id))
    assert groups == {report.id: 2, None: 1}

    # Per-report turns are scoped to that conversation only.
    turns = await consult_memory.recent_turns_for_report(
        async_session, user_id=user.id, report_id=report.id
    )
    assert [t.text for t in turns] == ["про КТ", "відповідь про КТ"]
    assert (
        await consult_memory.count_for_report(async_session, user_id=user.id, report_id=None) == 1
    )


async def test_clear_report_forgets_one_conversation_only(async_session: AsyncSession) -> None:
    user = await ensure_user(async_session, 1)
    report = await _confirmed_report(async_session, user)
    await consult_memory.record_turn(
        async_session, user_id=user.id, role="user", text="про КТ", report_id=report.id
    )
    await consult_memory.record_turn(async_session, user_id=user.id, role="user", text="загальне")
    deleted = await consult_memory.clear_report(async_session, user_id=user.id, report_id=report.id)
    assert deleted == 1
    # The report's conversation is gone; the general one stays.
    assert (
        await consult_memory.count_for_report(async_session, user_id=user.id, report_id=report.id)
        == 0
    )
    assert await consult_memory.count(async_session, user_id=user.id) == 1


async def test_deleting_a_report_decouples_but_keeps_the_memory(
    async_session: AsyncSession, scheduler: ReminderScheduler
) -> None:
    from dbaylo.companion import history

    user = await ensure_user(async_session, 1)
    report = await _confirmed_report(async_session, user)
    report_id = report.id
    await consult_memory.record_turn(
        async_session,
        user_id=user.id,
        role="user",
        text="що з моїм КТ?",
        report_id=report_id,
    )
    await history.delete_report(async_session, report=report, scheduler=scheduler)
    rows = await consult_memory.recent_turns(async_session, user_id=user.id)
    assert [r.text for r in rows] == ["що з моїм КТ?"]  # the conversation survives
    assert rows[0].report_id is None  # but is decoupled from the deleted report
