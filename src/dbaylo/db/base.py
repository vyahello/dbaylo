"""Declarative base, engine, and session factory.

Synchronous SQLAlchemy 2.0 for Stage 1. The ORM mappings are identical for sync
and async, so this is the only place that changes when Stage 2 moves the runtime
to async sessions.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from dbaylo.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


engine = create_engine(get_settings().database_url, future=True)
session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a session, committing on success and rolling back on error."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
