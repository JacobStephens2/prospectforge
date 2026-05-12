"""SQLAlchemy engine + session.

One engine per process, scoped sessions per request. Workers create their own
sessions per job. App code goes through `get_db()` for FastAPI dependency
injection.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.postgres_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session, closes it on request end."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
