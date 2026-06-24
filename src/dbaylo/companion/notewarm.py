"""Proactive warmer for the educational indicator notes.

A note is a pure function of (persona version, specimen, normalized analyte) and is persisted once
(see :mod:`notecache`). Generating notes lazily during a PDF build made the FIRST export both slow
AND incomplete: within its time budget only some of a category's notes finished, so the rest carried
only the deterministic dynamics line. This module fills the cache ahead of time — it walks the
user's confirmed indicators (the SAME (title, specimen) pairs the charts/PDF key their notes on, so
the keys match exactly) and generates every missing note in the background, persisting each as soon
as it is ready. Once the cache is warm, every chart, table and PDF carries its description and
renders with no claude call at all.

Run best-effort on startup and after each lab confirm: it never raises and never blocks.
Concurrency is bounded (memory) but there is no overall deadline — it is a background task, free to
take minutes, and a restart mid-warm simply keeps whatever already landed.
"""

from __future__ import annotations

import asyncio
import contextlib

from dbaylo.companion import notecache
from dbaylo.companion.history import all_dynamics_bundle
from dbaylo.config import get_settings
from dbaylo.db import get_session
from dbaylo.labs.humanize import describe_indicator, note_cache_key


async def _collect_note_items(user_id: int) -> list[tuple[str, str | None]]:
    """The (title, specimen) pairs of every charted/tabled indicator for this user — exactly the
    pairs the dynamics PDF keys its notes on, so a warmed note is a guaranteed cache hit there."""
    async with get_session() as session:
        charts, quals, _ = await all_dynamics_bundle(session, user_id=user_id)
    return [(d.title, d.specimen) for d in charts] + [(q.title, q.specimen) for q in quals]


async def _missing_items(user_id: int) -> list[tuple[str, str | None]]:
    items = await _collect_note_items(user_id)
    if not items:
        return []
    keys = [note_cache_key(spec, title) for title, spec in items]
    async with get_session() as session:
        cached = await notecache.fetch_cached(session, keys)
    return [
        (title, spec) for (title, spec), key in zip(items, keys, strict=True) if key not in cached
    ]


async def warm_user_notes(user_id: int) -> int:
    """Generate + persist every still-missing indicator note for this user. Returns how many notes
    were freshly persisted. Best-effort: swallows its own failures, never raises — safe to fire and
    forget from startup or a post-confirm hook."""
    try:
        missing = await _missing_items(user_id)
    except Exception:
        return 0
    if not missing:
        return 0
    sem = asyncio.Semaphore(max(1, get_settings().claude_interpret_concurrency))

    async def _one(title: str, spec: str | None) -> bool:
        async with sem:
            note = await describe_indicator(title, specimen=spec)
        if not note:
            return False
        try:
            async with get_session() as session:
                await notecache.store_many(session, {note_cache_key(spec, title): note})
                await session.commit()
        except Exception:
            return False
        return True

    results = await asyncio.gather(
        *(_one(title, spec) for title, spec in missing), return_exceptions=True
    )
    return sum(1 for r in results if r is True)


# Keep a strong reference to in-flight background warms so the loop does not GC them mid-run.
_BACKGROUND: set[asyncio.Task[int]] = set()


def warm_user_notes_in_background(user_id: int) -> None:
    """Fire-and-forget warm: schedule :func:`warm_user_notes` on the running loop without awaiting,
    so a caller (lab confirm, startup) is never blocked. A reference is kept until done so the task
    is not garbage-collected mid-flight."""
    with contextlib.suppress(RuntimeError):  # no running loop (e.g. a sync CLI) — just skip
        task = asyncio.get_running_loop().create_task(warm_user_notes(user_id))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
