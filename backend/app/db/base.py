"""
Shared declarative base and reusable column mixins (db/base.py).

Every ORM model in backend/app/models inherits ``Base`` and declares its
``__tablename__``. Because alembic's autogenerate compares ``Base.metadata``
against the live database, alembic/env.py imports this module (and the models)
before producing a migration - otherwise tables would be invisible to it.

``naming_convention`` deserves a special mention: without it, PostgreSQL
auto-generates random constraint names (e.g. ``ck_ab123cd``) that change on
every recreation, which makes migrations noisy and constraints impossible to
refer to by name (in tests, in ``ALTER ... DROP CONSTRAINT``, etc.). The
convention below gives every constraint a deterministic, greppable name like
``ck_mark_score_not_negative``.
"""

from datetime import datetime

from sqlalchemy import DateTime as sa_datetime
from sqlalchemy import MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Deterministic constraint names: <type>_<table>_<column(s)>
#: https://docs.sqlalchemy.org/en/20/core/metadata.html#naming-convention
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base class for all Campus360 ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` to any model that uses it.

    * ``created_at``: filled by the DATABASE clock (``func.now()``) at INSERT.
      Server-side time avoids "wrong clock on the app server" bugs.
    * ``updated_at``: refreshed by SQLAlchemy (``onupdate``) on every UPDATE.

    Both are timezone-aware (``TIMESTAMP WITH TIME ZONE``): storing naive
    timestamps is a classic source of bugs across daylight-saving changes.
    """

    created_at: Mapped[datetime] = mapped_column(
        sa_datetime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa_datetime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
