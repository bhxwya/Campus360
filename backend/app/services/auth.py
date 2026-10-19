"""
Authentication business logic (services/auth.py).

Route handlers in api/auth.py stay thin: they translate HTTP to service calls
and map results to status codes. The actual decisions - does the password
match, is the account active, mint a session - live here, so the CLI user
script and future background jobs can reuse exactly the same logic.

LOGIN PRIVACY NOTE (adjustment from review): unknown email, wrong password,
and a DISABLED account all return the SAME failure. We deliberately do not
distinguish "password wrong" from "account disabled", because a distinct
response would confirm to an attacker that an account exists and is disabled.
"""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    dummy_verify,
    generate_csrf_token,
    generate_session_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models.enums import UserRole
from app.models.session import UserSession
from app.models.user import User

logger = logging.getLogger(__name__)

#: One message for EVERY failed login - the anti-enumeration contract.
INVALID_CREDENTIALS = "Invalid email or password"


class AuthError(Exception):
    """Raised by service functions when an operation cannot proceed.

    The router catches this and turns it into an HTTP response, keeping the
    service layer free of FastAPI imports (easier to reuse and test).
    """

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def authenticate(db: Session, email: str, password: str) -> User | None:
    """Return the ``User`` if credentials are valid AND active, else None.

    Email is NORMALIZED (lowercased) to match the DB constraint
    ``email_normalized = lower(email)``, so logging in as ``User@Campus.edu``
    finds the row stored as ``user@campus.edu``.

    Returns None for every failure (unknown email, bad password, disabled) so
    the caller emits one identical error. Timing is equalized: when the email
    is unknown we still run a verify against a dummy hash, so a missing account
    is not measurably faster than a wrong password.
    """
    normalized = email.strip().lower()
    user = db.query(User).filter(User.email_normalized == normalized).one_or_none()

    if user is None:
        dummy_verify()  # equalize response time - do not reveal "no such user"
        return None

    if not verify_password(password, user.password_hash):
        return None

    # Disabled accounts fail identically to wrong passwords (see module note).
    if not user.is_active:
        logger.info("Login attempt on deactivated account id=%s", user.id)
        return None

    return user


def create_session(db: Session, user: User) -> tuple[str, str]:
    """Mint a new session for ``user``; return (session_token, csrf_token).

    Only HASHES are persisted. The raw tokens are returned so the router can
    place them in cookies - they are never stored, logged, or sent in a body.
    A unique-constraint collision (astronomically unlikely with 256-bit
    tokens) is treated as a hard error rather than silently retried, so we
    never loop on a compromised RNG.
    """
    settings = get_settings()
    session_token = generate_session_token()
    csrf_token = generate_csrf_token()

    row = UserSession(
        user_id=user.id,
        token_hash=hash_token(session_token),
        csrf_token_hash=hash_token(csrf_token),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_max_age_seconds),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.error("Session token collision on insert - refusing to retry")
        raise AuthError(INVALID_CREDENTIALS, status.HTTP_500_INTERNAL_SERVER_ERROR) from None

    db.refresh(row)
    return session_token, csrf_token


def revoke_session(db: Session, session_row: UserSession) -> None:
    """Delete the session row - immediate, server-side revocation.

    After this, the browser's cookie still exists but maps to nothing, so the
    very next request is rejected. This is the advantage of server-side
    sessions over self-contained tokens, which cannot be revoked before expiry.
    """
    db.delete(session_row)
    db.commit()


def set_auth_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    """Attach the session and CSRF cookies to a response.

    Flags and their rationale:
      * session cookie: HttpOnly (JS cannot read it -> XSS cannot steal it),
        SameSite=Lax (blocks cross-site POSTs, allows top-level navigation).
      * csrf cookie: NOT HttpOnly, because the frontend must read it to echo
        it back in the X-CSRF-Token header. It is useless without the session.
      * Secure: from settings.cookie_secure - false for local HTTP dev, true
        behind HTTPS in production.
      * Path=/ so every route receives them; Max-Age ties lifetime to the
        configured session window.
    """
    settings = get_settings()
    max_age = settings.session_max_age_seconds

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=max_age,
        path="/",
        # Readable by the frontend on purpose - it must echo this value back.
        httponly=False,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def clear_auth_cookies(response: Response) -> None:
    """Expire both cookies on the response (used by logout)."""
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def create_user(
    db: Session,
    *,
    email: str,
    password: str,
    role: UserRole,
    is_active: bool = True,
) -> User:
    """Create a user with a NORMALIZED email and hashed password.

    This is the single user-creation path, shared by the local CLI script.
    It is NOT exposed over HTTP in Stage 3 - there is no public registration,
    and nothing here lets a request choose a privileged role. Duplicate emails
    surface as AuthError so callers return a clean conflict, not a 500.
    """
    normalized = email.strip().lower()
    user = User(
        email=email.strip(),
        email_normalized=normalized,
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AuthError(
            "An account with that email already exists",
            status.HTTP_409_CONFLICT,
        ) from None
    db.refresh(user)
    return user
