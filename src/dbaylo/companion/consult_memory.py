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

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, and_, case, delete, func, select
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
    analyte_key: str | None = None,
    subject_label: str | None = None,
) -> None:
    """Persist one consultation turn, then prune this user's oldest rows past the retention cap.
    Blank text is ignored (nothing worth remembering).

    A consultation about a TREND chart carries ``analyte_key`` (+ a display ``subject_label``)
    instead of a ``report_id`` — a chart spans many reports, so the turn is grouped by its analyte,
    not dumped into the general bucket. A report/section consult carries ``report_id``; a turn with
    neither is a genuinely general chat."""
    text = (text or "").strip()
    if not text:
        return
    session.add(
        ConsultMemory(
            user_id=user_id,
            role=role,
            text=text,
            report_id=report_id if report_id else None,
            analyte_key=analyte_key or None,
            subject_label=(subject_label or None) if analyte_key else None,
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


# --- Grouping by subject (anchored to an analyte, a report, or general) ----------
# A trend-chart conversation is grouped by its analyte (``analyte_key``); a report/section one by
# its ``report_id``; anything anchored to neither is the general group. Charts store report_id NULL,
# reports store analyte_key NULL, so the three groups never overlap.

KIND_ANALYTE = "analyte"
KIND_REPORT = "report"
KIND_GENERAL = "general"


@dataclass(frozen=True)
class MemoryGroup:
    """One conversation group in the memory view, most-recently-active ordered by the caller."""

    kind: str  # KIND_ANALYTE | KIND_REPORT | KIND_GENERAL
    report_id: int | None  # set for a report group
    analyte_key: str | None  # set for an analyte (trend-chart) group
    label: str | None  # the analyte's display name (analyte group only)
    count: int


def _group_cond(report_id: int | None, analyte_key: str | None) -> ColumnElement[bool]:
    """Match the rows of exactly ONE conversation group: an analyte's chart, a specific report, or
    the general (anchored to neither) group."""
    if analyte_key is not None:
        return ConsultMemory.analyte_key == analyte_key
    if report_id is not None:
        return and_(ConsultMemory.report_id == report_id, ConsultMemory.analyte_key.is_(None))
    return and_(ConsultMemory.report_id.is_(None), ConsultMemory.analyte_key.is_(None))


async def list_groups(session: AsyncSession, *, user_id: int) -> list[MemoryGroup]:
    """The user's conversation groups, most-recently-active first: one per analyte (trend chart) we
    talked about, one per report, plus the general group for chats anchored to neither.

    An analyte group collapses ACROSS reports (a chart is about the analyte over time): the report
    column is folded to NULL whenever ``analyte_key`` is set, so every turn about an analyte lands
    in one group even if a chart consult carried a report id. This mirrors ``_group_cond``."""
    report_bucket = case((ConsultMemory.analyte_key.is_(None), ConsultMemory.report_id), else_=None)
    rows = (
        await session.execute(
            select(
                report_bucket.label("report_id"),
                ConsultMemory.analyte_key,
                func.count().label("n"),
                func.max(ConsultMemory.subject_label).label("label"),
                func.max(ConsultMemory.id).label("mx"),
            )
            .where(ConsultMemory.user_id == user_id)
            .group_by(report_bucket, ConsultMemory.analyte_key)
            .order_by(func.max(ConsultMemory.id).desc())
        )
    ).all()
    groups: list[MemoryGroup] = []
    for row in rows:
        if row.analyte_key is not None:
            kind, report_id = KIND_ANALYTE, None
        elif row.report_id is not None:
            kind, report_id = KIND_REPORT, row.report_id
        else:
            kind, report_id = KIND_GENERAL, None
        groups.append(
            MemoryGroup(
                kind=kind,
                report_id=report_id,
                analyte_key=row.analyte_key,
                label=row.label if row.analyte_key is not None else None,
                count=int(row.n),
            )
        )
    return groups


async def group_at(session: AsyncSession, *, user_id: int, index: int) -> MemoryGroup | None:
    """The conversation group at ``index`` in the freshly-derived groups list, or ``None`` if it no
    longer exists (the list is re-derived on every tap, like the charts/problems pickers)."""
    groups = await list_groups(session, user_id=user_id)
    return groups[index] if 0 <= index < len(groups) else None


async def recent_turns_for_group(
    session: AsyncSession,
    *,
    user_id: int,
    report_id: int | None = None,
    analyte_key: str | None = None,
    limit: int = _VIEW_TURNS,
) -> list[ConsultMemory]:
    """The most recent turns of ONE group (analyte / report / general), oldest→newest."""
    rows = (
        (
            await session.execute(
                select(ConsultMemory)
                .where(ConsultMemory.user_id == user_id, _group_cond(report_id, analyte_key))
                .order_by(ConsultMemory.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


async def count_for_group(
    session: AsyncSession,
    *,
    user_id: int,
    report_id: int | None = None,
    analyte_key: str | None = None,
) -> int:
    """How many turns are remembered for ONE conversation group."""
    total = await session.scalar(
        select(func.count())
        .select_from(ConsultMemory)
        .where(ConsultMemory.user_id == user_id, _group_cond(report_id, analyte_key))
    )
    return int(total or 0)


async def clear_group(
    session: AsyncSession,
    *,
    user_id: int,
    report_id: int | None = None,
    analyte_key: str | None = None,
) -> int:
    """Forget ONE conversation ("забути цю розмову") — an analyte's, a report's, or the general
    group. Returns how many turns were deleted; leaves every other conversation untouched."""
    result = await session.execute(
        delete(ConsultMemory).where(
            ConsultMemory.user_id == user_id, _group_cond(report_id, analyte_key)
        )
    )
    return cast("CursorResult[Any]", result).rowcount or 0
