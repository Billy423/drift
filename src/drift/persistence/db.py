"""The database connection: the declarative base, the engine and the session factory."""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://agent:agent@localhost:5432/agent"
)


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base — all ORM models inherit from this."""


def get_engine():
    """A new engine bound to `DATABASE_URL`.

    Pool sizing is explicit rather than SQLAlchemy's default. Dispatch is serial and the frame
    and its cell run in separate processes, so one steady connection per process is enough; the
    rest absorbs short-lived extra sessions and caps a runaway well under Postgres's own limit.
    """
    return create_engine(DATABASE_URL, future=True, pool_size=5, max_overflow=5)


SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
