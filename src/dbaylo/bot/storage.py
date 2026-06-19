"""A persistent FSM storage backed by SQLite (Stage 6).

aiogram's default ``MemoryStorage`` loses all FSM state on restart, so an in-progress
lab confirmation or symptom interview is lost on every deploy. This stores each
``StorageKey``'s state + data in a dedicated SQLite file (the domain DB and Alembic stay
untouched), so dialogs survive a restart. No new dependency — it uses ``aiosqlite``, which
is already in the stack.

Single-process bot: one lazily-opened connection guarded by an ``asyncio.Lock``. The data
dict is stored as JSON (every value the handlers put in FSM state is JSON-serialisable —
primitives, lists, and the report dict).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fsm (
    key   TEXT PRIMARY KEY,
    state TEXT,
    data  TEXT NOT NULL DEFAULT '{}'
)
"""


def _key(key: StorageKey) -> str:
    return ":".join(
        str(part)
        for part in (
            key.bot_id,
            key.chat_id,
            key.user_id,
            key.thread_id,
            key.business_connection_id,
            key.destiny,
        )
    )


class SQLiteStorage(BaseStorage):
    """FSM storage persisted to a SQLite file (survives restarts)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(self._path)
            await self._db.execute(_SCHEMA)
            await self._db.commit()
        return self._db

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        async with self._lock:
            db = await self._conn()
            # Upsert the state, preserving any existing data row.
            await db.execute(
                "INSERT INTO fsm (key, state) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET state = excluded.state",
                (_key(key), value),
            )
            await db.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        async with self._lock:
            db = await self._conn()
            async with db.execute("SELECT state FROM fsm WHERE key = ?", (_key(key),)) as cur:
                row = await cur.fetchone()
        return row[0] if row is not None else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(data), ensure_ascii=False)
        async with self._lock:
            db = await self._conn()
            await db.execute(
                "INSERT INTO fsm (key, data) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                (_key(key), payload),
            )
            await db.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self._lock:
            db = await self._conn()
            async with db.execute("SELECT data FROM fsm WHERE key = ?", (_key(key),)) as cur:
                row = await cur.fetchone()
        if row is None or not row[0]:
            return {}
        loaded: dict[str, Any] = json.loads(row[0])
        return loaded

    async def close(self) -> None:
        async with self._lock:
            if self._db is not None:
                await self._db.close()
                self._db = None
