"""Account activation token (models/activation_token.py).

A single-use, time-bound token bound to one ``User``. Used by the bulk-import
workflow so students can choose their own permanent password.

Only the SHA-256 digest of the token is persisted; the raw token is delivered
to the student by email and never written here, to any log, or to any CSV
export.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ActivationToken(Base):
    """One pending account activation, keyed by a hashed opaque token."""

    __tablename__ = "activation_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: SHA-256 hex digest of the raw activation token. UNIQUE because two
    #: activations can never share a token.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user = relationship("User")
