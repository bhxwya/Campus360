"""
Database engine and session handling (db/session.py)
=====================================================

This is the only place that creates the SQLAlchemy engine and the
sessionmaker factory. Everything else obtains a Session through ``get_db``.

Key concepts for readers new to SQLAlchemy:

* **Engine** - a pool of database connections shared by the whole process.
  It is created once at import time (bottom of this file).
* **Session** - a short-lived "unit of work" wrapper around ONE connection.
  FastAPI gives each request its own Session via the ``get_db`` dependency.
* **Transactions** - with autocommit disabled, SQLAlchemy opens a transaction
  lazily on the first query and keeps it open until commit()/rollback()/close().
  Our convention (documented on get_db): SELECT-only requests just close;
  write requests call commit() explicitly once, after all related writes
  succeeded, so multi-row operations are atomic (all-or-nothing).
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

# pool_pre_ping=True: run a cheap "SELECT 1" before reusing a pooled connection.
# This transparently discards connections that PostgreSQL closed while idle
# (e.g. server restart), instead of failing the next request with an error.
#
# get_settings().database_url is a SQLAlchemy URL *object* (not a string) - see
# config.py for why: str(URL) masks the password, so a URL leaking into a log
# line is harmless, while create_engine() still receives the real credentials.
engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    future=True,
)

# autoflush=False  -> never silently flush pending changes mid-query (surprising
#                    SQL is a common source of bugs; we flush explicitly).
# autocommit=False -> the SQLAlchemy 2.x explicit-transaction style.
# expire_on_commit=False -> ORM objects stay readable after commit() without
#                    triggering a lazy refresh (which could fail if the session
#                    is already closing at the end of the request).
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session]:
    """FastAPI dependency: provide one Session per request, always closed.

    The try/finally guarantees close() even when a route raises. Closing
    WITHOUT a prior commit() rolls back any open transaction - that is our
    error path: a failed write leaves the database untouched.

    This dependency deliberately never commits: keeping "who commits" explicit
    makes transaction boundaries easy to find when reading the code.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
