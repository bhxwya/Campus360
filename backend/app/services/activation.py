"""Secure account activation (services/activation.py).

Students imported through the bulk-import workflow start with an inactive
account (``users.is_active = False``) and an uncrackable placeholder password
hash. They become usable only after clicking a single-use, time-bound
activation link and choosing their own permanent password.

All token material is treated like a password hash: the raw token is never
logged, never stored, and never returned by any API. Only its SHA-256 digest
lives in the ``activation_tokens`` table.

The activation flow:

1. Admin imports students -> accounts created inactive + activation token row
   inserted -> activation email queued (raw token placed only in the link).
2. Student opens the link (``/pages/activate.html?token=...``, a static page
   served by FastAPI) -> presented with a password form.
3. Student submits (POST /auth/activate {token, password}) -> service
   validates the token hash, expiry, single-use state and account association
   inside ONE transaction, hashes the chosen password with Argon2id, flips
   ``is_active`` to True, stores the real hash, marks the token consumed,
   revokes any existing sessions for that account, and commits.
4. Any failure at step 3 leaves the account inactive and the token
   unconsumed; the student can retry with the same link until it expires.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.email import EmailSendResult, build_email_sender
from app.core.security import hash_password, hash_token
from app.models.activation_token import ActivationToken
from app.models.enums import UserRole
from app.models.session import UserSession
from app.models.user import User

#: Minimum / maximum password length, mirroring StudentCreate so an activated
#: account is subject to the same policy a directly-created account would be.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


class ActivationError(Exception):
    """Raised when activation cannot proceed.

    Carries the HTTP status the router should answer with. Every public
    failure uses a generic message so the endpoint cannot be used to
    enumerate whether an arbitrary email has an account.
    """

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


#: One message for EVERY activation failure - anti-enumeration.
INVALID_TOKEN_MESSAGE = "Invalid or expired activation link."


def _placeholder_password_hash() -> str:
    """Return an Argon2id hash of a cryptographically random, unknown secret.

    The secret is generated once per call and discarded, so nobody (including
    Campus360 administrators) knows the corresponding plaintext. The account
    can therefore not be logged into until activation replaces this hash with
    the student's chosen password.
    """
    unknown_secret = secrets.token_urlsafe(48)
    return hash_password(unknown_secret)


def create_inactive_user_and_activation_token(
    db: Session,
    *,
    email: str,
    email_normalized: str,
    role: UserRole = UserRole.STUDENT,
    ttl_seconds: int | None = None,
) -> tuple[User, str]:
    """Create an inactive ``User`` plus a single-use activation token.

    Returns ``(user, raw_token)``. The caller is responsible for committing;
    this helper only adds the rows to the session. The raw token is returned
    so the caller (import service) can place it in an activation email; it is
    never persisted or logged here.

    The user is created with ``is_active=False`` and a placeholder hash that
    cannot verify against any known password, so the account cannot be used
    until activation.
    """
    settings = get_settings()
    if ttl_seconds is None:
        ttl_seconds = settings.activation_ttl_seconds

    user = User(
        email=email,
        email_normalized=email_normalized,
        password_hash=_placeholder_password_hash(),
        role=role,
        is_active=False,
    )
    db.add(user)
    db.flush()  # assign user.id without committing

    raw_token = secrets.token_urlsafe(32)  # 256-bit random
    token_row = ActivationToken(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )
    db.add(token_row)
    db.flush()

    return user, raw_token


def revoke_existing_activation_tokens(db: Session, user_id: int) -> None:
    """Mark every unconsumed token for ``user_id`` as consumed (revoked).

    Used when resending an activation email so the old link can no longer be
    used, even if it has not yet expired.
    """
    now = datetime.now(UTC)
    db.query(ActivationToken).filter(
        ActivationToken.user_id == user_id,
        ActivationToken.consumed_at.is_(None),
    ).update({ActivationToken.consumed_at: now}, synchronize_session=False)


def resend_activation_token(
    db: Session,
    *,
    user_id: int,
) -> tuple[User, str]:
    """Revoke existing tokens and create a new activation token for a user.

    Used when resending an activation email. The user must exist and be
    inactive. All existing unconsumed tokens are revoked before the new one
    is created, so only the newest link works.
    """
    user = db.get(User, user_id)
    if user is None or user.is_active:
        raise ActivationError(
            "Account not found or already active",
            status.HTTP_404_NOT_FOUND,
        )

    # Revoke previous tokens so only the newest link works.
    revoke_existing_activation_tokens(db, user_id)

    settings = get_settings()
    raw_token = secrets.token_urlsafe(32)  # 256-bit random
    token_row = ActivationToken(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.activation_ttl_seconds),
    )
    db.add(token_row)
    db.commit()
    db.refresh(user)

    return user, raw_token


def activate_account(
    db: Session,
    *,
    raw_token: str,
    new_password: str,
) -> User:
    """Atomically consume a token and activate the account.

    Order of enforcement matters for security:

    1. Look up the token by hash (never by raw value) - row must exist,
       be unconsumed, and not be expired.
    2. Load the linked user - must exist and be inactive.
    3. Validate the new password against project conventions.
    4. Hash the password with Argon2id, flip ``is_active`` to True,
       store the real hash, mark the token consumed, revoke any existing
       sessions for this account.
    5. Commit in ONE transaction. Any failure rolls back everything: the
       account stays inactive and the token stays unconsumed.

    Concurrency safety: the token row is locked with ``FOR UPDATE`` so two
    simultaneous activation requests for the same token cannot both succeed.
    The second request sees ``consumed_at`` set and receives a generic error.
    """
    # Validate password shape before touching the database - fail fast on
    # client error, never leave a half-written token state.
    if not new_password or len(new_password) < PASSWORD_MIN_LENGTH:
        raise ActivationError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if len(new_password) > PASSWORD_MAX_LENGTH:
        raise ActivationError(
            f"Password must be at most {PASSWORD_MAX_LENGTH} characters.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    # Lock the token row for update: prevents a race where two requests
    # validate the same unconsumed token at the same time.
    token_row = (
        db.query(ActivationToken)
        .filter(ActivationToken.token_hash == hash_token(raw_token))
        .filter(ActivationToken.consumed_at.is_(None))
        .with_for_update()
        .one_or_none()
    )
    if token_row is None:
        # Generic message regardless of why (missing, consumed, or expired)
        # so attackers cannot distinguish valid tokens from invalid ones.
        raise ActivationError(INVALID_TOKEN_MESSAGE, status.HTTP_400_BAD_REQUEST)

    if token_row.expires_at <= datetime.now(UTC):
        raise ActivationError(INVALID_TOKEN_MESSAGE, status.HTTP_400_BAD_REQUEST)

    user = db.get(User, token_row.user_id)
    if user is None or user.is_active:
        raise ActivationError(INVALID_TOKEN_MESSAGE, status.HTTP_400_BAD_REQUEST)

    # All checks passed - mutate inside the same transaction.
    user.password_hash = hash_password(new_password)
    user.is_active = True
    token_row.consumed_at = datetime.now(UTC)

    # Revoke any existing sessions for this account so a cookie issued under
    # the inactive state cannot outlive activation.
    db.query(UserSession).filter(UserSession.user_id == user.id).delete(synchronize_session=False)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(user)
    return user


#: Where the activation form is served. FastAPI only exposes the static
#: ``pages/`` tree (see app/main.py), so the link must target the page itself.
ACTIVATION_PAGE_PATH = "/pages/activate.html"


def build_activation_url(raw_token: str) -> str:
    """The link placed in the activation email (token URL-encoded)."""
    base = get_settings().activation_link_base_url.rstrip("/")
    return f"{base}{ACTIVATION_PAGE_PATH}?token={quote(raw_token, safe='')}"


def queue_activation_email(
    db: Session,
    *,
    user: User,
    raw_token: str,
    student_name: str,
) -> EmailSendResult:
    """Queue the activation email and return the send result.

    The email provider is chosen from settings (``log`` by default). The raw
    token is used only to build the link; it is never logged by the sender.
    Delivery failure does not roll back the account/token creation - the
    record stays "pending" and can be retried.
    """
    activation_url = build_activation_url(raw_token)
    sender = build_email_sender()
    return sender.send_activation_email(
        to_email=user.email,
        activation_url=activation_url,
        student_name=student_name,
    )
