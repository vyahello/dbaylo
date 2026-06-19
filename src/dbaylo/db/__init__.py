"""L2 data layer — SQLAlchemy 2.0 models and the async session plumbing.

Stage 2 runs on async sessions (aiosqlite). Alembic stays synchronous and builds
its own engine in ``migrations/env.py``.
"""

from dbaylo.db.base import Base, async_engine, async_session_factory, get_session

__all__ = ["Base", "async_engine", "async_session_factory", "get_session"]
