"""
Shared declarative base for SQLAlchemy models (db/base.py).

Every ORM model in backend/app/models inherits ``Base`` and declares its
``__tablename__``. Because alembic's autogenerate compares ``Base.metadata``
against the live database, alembic/env.py imports this module (and the models)
before producing a migration - otherwise tables would be invisible to it.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base class for all Campus360 ORM models."""
