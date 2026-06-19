"""Declarative base + the async session layer.

Stage 2 moves the runtime to **async** SQLAlchemy (aiosqlite). The ORM mappings
are unchanged. Alembic stays synchronous and builds its own engine in
``migrations/env.py``, so nothing here is needed for migrations.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from dbaylo.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _async_url(sync_url: str) -> str:
    """Turn a sync SQLite URL into its aiosqlite (async) form."""
    url = make_url(sync_url)
    if url.drivername == "sqlite":
        url = url.set(drivername="sqlite+aiosqlite")
    return url.render_as_string(hide_password=False)


async_engine = create_async_engine(_async_url(get_settings().database_url), future=True)
async_session_factory = async_sessionmaker(
    bind=async_engine, expire_on_commit=False, class_=AsyncSession
)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async session, committing on success and rolling back on error."""
    session = async_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
