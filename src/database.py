"""Database engine and session management supporting SQLite (demo) and Supabase PostgreSQL (connected)."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.config import get_config


class Base(DeclarativeBase):
    """Base declarative class for all relational models."""


_engine = None
_SessionLocal = None


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_engine(database_url: str | None = None):
    """Initialize or re-initialize the SQLAlchemy engine and session factory."""
    global _engine, _SessionLocal
    cfg = get_config()
    url = database_url or cfg.effective_database_url
    is_sqlite = url.startswith("sqlite")

    connect_args = {"check_same_thread": False} if is_sqlite else {}
    _engine = create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=not is_sqlite,
        future=True,
    )
    if is_sqlite:
        event.listen(_engine, "connect", _enable_sqlite_foreign_keys)

    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return _engine


def get_engine():
    global _engine
    if _engine is None:
        init_engine()
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        init_engine()
    return _SessionLocal


def create_all_tables() -> None:
    """Create all relational tables if they do not already exist."""
    from src import models  # noqa: F401

    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a database session."""
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
