"""
Server-side session storage (models/session.py).

``UserSession`` is one active browser login. The design is a SERVER-SIDE
session: the browser only ever holds an opaque random token in an HttpOnly
cookie, and this table maps that token (by hash) back to a user.

Why store only SHA-256 HASHES of the session and CSRF tokens?

* If this table is ever leaked (backup, SQL injection, rogue dump), an
  attacker sees only digests - they cannot replay them as live cookies, the
  same way a leaked password table should not reveal passwords.
* The tokens are 256-bit random (not human-chosen), so unlike passwords a
  fast hash is appropriate; we verify them on every request and a slow KDF
  would add needless latency.

Immediate revocation: ``POST /logout`` DELETES the row, so the very next
request with that cookie finds nothing and is rejected. Expiry is enforced
by comparing ``expires_at`` to the current time on every authenticated
request - an expired row is treated exactly like a missing one.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.models.user import User


class UserSession(Base):
    """One active login session, keyed by a hashed opaque token."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,  # "list this user's sessions" / revoke-all-on-password-change
    )

    #: SHA-256 of the opaque session token in the cookie. UNIQUE because two
    #: sessions can never share a token - a duplicate would be a collision or
    #: a forgery attempt.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    #: SHA-256 of the synchronizer CSRF token. Not unique: it is only ever
    #: compared (via hmac.compare_digest) against the X-CSRF-Token header of
    #: the ALREADY-authenticated session - never used as a lookup key.
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,  # supports "delete expired sessions" cleanup sweeps
    )

    #: Optional liveness signal for a future "your active sessions" page.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Relationships -----------------------------------------------------
    user: Mapped[User] = relationship()
