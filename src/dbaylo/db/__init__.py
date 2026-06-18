"""L2 data layer — SQLAlchemy 2.0 models and session plumbing.

Stage 1 ships the schema and a clean Alembic init migration. Sessions are
synchronous for now; the models are session-agnostic, so moving to async
sessions in Stage 2 (when check-ins and labs persist) requires no model changes.
"""

from dbaylo.db.base import Base, engine, get_session, session_factory

__all__ = ["Base", "engine", "get_session", "session_factory"]
