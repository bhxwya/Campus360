"""
Alembic migration environment (alembic/env.py)
===============================================

Alembic runs OUTSIDE the FastAPI process: `uv run alembic ...` starts a fresh
Python interpreter and executes this file. Two things therefore happen here:

1. `backend/` is added to sys.path so `app.*` modules can be imported.
2. The database URL is taken from application settings (which read the
   git-ignored .env). The URL is passed directly to SQLAlchemy - it is never
   written into alembic.ini, so credentials cannot end up in a committed file.

Modes:
* online  (default)  - connects and applies/reverses migrations.
* offline  (--sql)   - prints the migration SQL without connecting.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool

from alembic import context

# alembic/env.py -> parents[0] = alembic/, parents[1] = repository root.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

# Imported after sys.path setup (they are not installed as a package).
# Importing the models package registers every table on Base.metadata.
# Without this, `alembic revision --autogenerate` would see an EMPTY schema
# and propose DROPping every existing table - a classic Alembic footgun.
from app import models  # noqa: F401,E402
from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402

# This is the Alembic Config object, giving access to values from alembic.ini.
config = context.config

# Set up loggers as configured in alembic.ini ([loggers]/[handlers] sections).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

#: Connection URL for the CURRENT environment (.env / process environment).
#: It is a SQLAlchemy URL *object*: str() of it masks the password, so it is
#: safe to pass around. Tests/tooling that set CAMPUS360_* variables redirect
#: migrations too - same priority rules as everywhere else.
DATABASE_URL = get_settings().database_url

#: Autogenerate compares live database schema against this metadata.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: emit SQL, no database connection.

    Used as: `uv run alembic upgrade head --sql` - handy for reviewing exactly
    what a migration will execute before applying it anywhere.
    """
    context.configure(
        # Alembic wants a string here. Offline mode generates SQL without ever
        # connecting, and the URL itself is never printed - only the SQL is -
        # so rendering the full (unmasked) URL is safe and keeps the dialect
        # information intact.
        url=DATABASE_URL.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Also diff column types/CHECKs in autogenerate, not just tables.
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode: connect and apply them.

    The engine is created with NullPool: migration tooling needs a single
    short-lived connection, not the application's reusable pool. Each migration
    runs inside a transaction (context.begin_transaction), so a failing
    migration rolls back rather than leaving a half-applied schema behind.
    """
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
