"""Durable, cross-session memory for contextual consultations ("Запитати Дбайло").

The FSM transcript only lives for ONE open consultation; when it ends (the ✅ button, a command, or
a menu tap) it is cleared. This module persists each consultation turn to the DB so a LATER
consultation can recall what was discussed before — real continuity instead of a cold start. The
recalled turns are injected into the grounded context (:mod:`consult_context`) exactly like the
patient profile: the model treats them as its genuine memory of past talks and is told to ground in
them, never to invent beyond them.

Pure DB read/write — NO LLM, NO escalation, NO network. It only stores and returns plain text, so it
adds no new path to the model and the safety choke-point invariant is untouched.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbaylo.db.models import ConsultMemory

# How much history to recall, and how much to keep. Bounded so the prompt never grows unbounded and
# the table never grows forever — the oldest talks age out naturally after each write.
_RECALL_TURNS = 12  # most-recent turns injected into a new consultation's context
_RETENTION_ROWS = 400  # per-user cap; rows older than this are pruned on every write
_VIEW_TURNS = 16  # how many recent turns the user sees in the /memory view

_ROLE_LABEL = {"user": "Користувач", "assistant": "Дбайло"}


async def record_turn(
    session: AsyncSession,
    *,
    user_id: int,
    role: str,
    text: str,
    report_id: int | None = None,
) -> None:
    """Persist one consultation turn, then prune this user's oldest rows past the retention cap.
    Blank text is ignored (nothing worth remembering)."""
    text = (text or "").strip()
    if not text:
        return
    session.add(
        ConsultMemory(
            user_id=user_id,
            role=role,
            text=text,
            report_id=report_id if report_id else None,
        )
    )
    await session.flush()
    await _prune(session, user_id=user_id)


async def _prune(session: AsyncSession, *, user_id: int) -> None:
    """Keep only this user's most recent ``_RETENTION_ROWS`` rows (id order == insert order)."""
    stale = (
        (
            await session.execute(
                select(ConsultMemory.id)
                .where(ConsultMemory.user_id == user_id)
                .order_by(ConsultMemory.id.desc())
                .offset(_RETENTION_ROWS)
            )
        )
        .scalars()
        .all()
    )
    if stale:
        await session.execute(delete(ConsultMemory).where(ConsultMemory.id.in_(stale)))


async def recent_turns(
    session: AsyncSession, *, user_id: int, limit: int = _RECALL_TURNS
) -> list[ConsultMemory]:
    """The most recent consultation turns for this user, oldest→newest (chronological order)."""
    rows = (
        (
            await session.execute(
                select(ConsultMemory)
                .where(ConsultMemory.user_id == user_id)
                .order_by(ConsultMemory.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


def format_block(turns: list[ConsultMemory], *, exclude: frozenset[str] = frozenset()) -> str:
    """Format recalled turns as a grounded English context block, or ``""`` when there is nothing to
    recall. ``exclude`` drops any turn whose text is already in the live FSM transcript, so the same
    line is never shown twice within a single open consultation."""
    lines: list[str] = []
    for turn in turns:
        body = turn.text.strip()
        if not body or body in exclude:
            continue
        who = _ROLE_LABEL.get(turn.role, turn.role)
        day = turn.created_at.date().isoformat() if turn.created_at else "?"
        lines.append(f"- [{day}] {who}: {body}")
    if not lines:
        return ""
    header = (
        "MEMORY — your earlier conversations with this user (from previous consultations; this is "
        "your genuine memory of past talks — use it for continuity, never invent beyond it):"
    )
    return header + "\n" + "\n".join(lines)


async def recall_block(
    session: AsyncSession,
    *,
    user_id: int,
    exclude: frozenset[str] = frozenset(),
    limit: int = _RECALL_TURNS,
) -> str:
    """Load + format this user's recent memory block in one call (``""`` when there is none)."""
    turns = await recent_turns(session, user_id=user_id, limit=limit)
    return format_block(turns, exclude=exclude)


async def count(session: AsyncSession, *, user_id: int) -> int:
    """How many consultation turns are remembered for this user."""
    total = await session.scalar(
        select(func.count()).select_from(ConsultMemory).where(ConsultMemory.user_id == user_id)
    )
    return int(total or 0)


async def clear_all(session: AsyncSession, *, user_id: int) -> int:
    """Forget EVERYTHING remembered for this user ("забути все"). Returns how many turns were
    deleted. Irreversible — the caller confirms first."""
    result = await session.execute(delete(ConsultMemory).where(ConsultMemory.user_id == user_id))
    return cast("CursorResult[Any]", result).rowcount or 0


# --- Grouping by analysis (a conversation is anchored to a report, or general) ----


def _report_cond(report_id: int | None):  # type: ignore[no-untyped-def]
    """Match rows of ONE conversation group: a specific report, or the general (no-report) group."""
    return (
        ConsultMemory.report_id == report_id
        if report_id is not None
        else (ConsultMemory.report_id.is_(None))
    )


async def list_groups(session: AsyncSession, *, user_id: int) -> list[tuple[int | None, int]]:
    """The user's conversation groups as ``(report_id_or_None, turn_count)``, most-recently-active
    first. ``None`` is the general group (consults not anchored to a specific report)."""
    rows = (
        await session.execute(
            select(
                ConsultMemory.report_id,
                func.count().label("n"),
                func.max(ConsultMemory.id).label("mx"),
            )
            .where(ConsultMemory.user_id == user_id)
            .group_by(ConsultMemory.report_id)
            .order_by(func.max(ConsultMemory.id).desc())
        )
    ).all()
    return [(row.report_id, int(row.n)) for row in rows]


async def recent_turns_for_report(
    session: AsyncSession, *, user_id: int, report_id: int | None, limit: int = _VIEW_TURNS
) -> list[ConsultMemory]:
    """The most recent turns of ONE conversation (a report's, or general), oldest→newest."""
    rows = (
        (
            await session.execute(
                select(ConsultMemory)
                .where(ConsultMemory.user_id == user_id, _report_cond(report_id))
                .order_by(ConsultMemory.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


async def count_for_report(session: AsyncSession, *, user_id: int, report_id: int | None) -> int:
    """How many turns are remembered for ONE conversation group."""
    total = await session.scalar(
        select(func.count())
        .select_from(ConsultMemory)
        .where(ConsultMemory.user_id == user_id, _report_cond(report_id))
    )
    return int(total or 0)


async def clear_report(session: AsyncSession, *, user_id: int, report_id: int | None) -> int:
    """Forget ONE conversation ("забути цю розмову") — a report's, or the general group. Returns how
    many turns were deleted; leaves every other conversation untouched."""
    result = await session.execute(
        delete(ConsultMemory).where(ConsultMemory.user_id == user_id, _report_cond(report_id))
    )
    return cast("CursorResult[Any]", result).rowcount or 0
