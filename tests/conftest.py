"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from dbaylo.db.base import Base


@pytest.fixture
def session() -> Iterator[Session]:
    """A fresh in-memory SQLite session with the full schema created."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        yield s
    Base.metadata.drop_all(engine)
