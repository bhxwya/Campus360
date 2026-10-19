"""
Authentication & authorization dependencies (api/deps.py).

Reusable FastAPI "guards" that every protected route composes. They are the
SERVER-SIDE enforcement point: hiding a frontend button is cosmetic, but a
route depending on ``require_admin`` cannot be reached by a student no matter
what the client sends.

The layered model:

  request cookie -> get_current_session -> get_current_user -> role gate
   (opaque token)    (finds+validates the      (loads the User)   (require_roles)
                      UserSession row)

Identity ALWAYS comes from the server-side session lookup. A client-supplied
``user_id`` or ``role`` in a JSON body or query string is never trusted - the
cookie only proves "this browser holds this token", and the token maps to
exactly one row naming exactly one user.

CSRF uses a SYNCHRONIZER-TOKEN design: login issues a random CSRF token, we
store only its SHA-256 hash on the session row, and give the raw value to the
browser in a readable (non-HttpOnly) cookie. Unsafe requests must echo that
value in an ``X-CSRF-Token`` header; we compare the header against the HASH on
the authenticated session. A token is therefore never accepted merely because
it matches a client-controlled cookie - the attacker cannot read the victim's
cookie (SameSite + cross-origin policy) and cannot produce a header value that
hashes to the victim's stored digest.
"""

import hmac
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_token
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.session import UserSession
from app.models.user import User

#: Shared per-request database session, matching the health router's style.
DbSession = Annotated[Session, Depends(get_db)]


def _unauthenticated(detail: str = "Not authenticated") -> HTTPException:
    """401 with a consistent shape. ``WWW-Authenticate`` hints at cookie auth."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Cookie"},
    )


def get_current_session(request: Request, db: DbSession) -> UserSession:
    """Resolve the session cookie to a live ``UserSession`` row, or raise 401.

    The single choke point for authentication. It rejects, in order: a missing
    cookie, an unknown token (deleted/expired/logged-out), an expired session,
    and sessions whose user was deactivated - all with the SAME generic 401 so
    an attacker learns nothing about why access failed.
    """
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise _unauthenticated()

    # Look up by HASH, never by the raw token: the database never sees the
    # value the browser holds, so a dump of this table is useless to an attacker.
    token_hash = hash_token(token)
    session_row = db.query(UserSession).filter(UserSession.token_hash == token_hash).one_or_none()
    if session_row is None:
        raise _unauthenticated()

    # Expiry is checked on every request, so a row that merely lingers past its
    # lifetime (until a cleanup sweep) is still treated as dead.
    if session_row.expires_at <= datetime.now(UTC):
        raise _unauthenticated("Session expired")

    # Deactivating an account (is_active=False) must lock out live sessions
    # immediately - load the user here and check.
    if session_row.user is None or not session_row.user.is_active:
        raise _unauthenticated()

    return session_row


#: Annotated shortcut for the session dependency, defined BEFORE get_current_user
#: so the name resolves at import time.
CurrentSession = Annotated[UserSession, Depends(get_current_session)]


def get_current_user(session_row: CurrentSession) -> User:
    """The authenticated ``User`` for this request. Compose routes on this."""
    return session_row.user


#: Annotated shortcut so routes read cleanly, e.g. ``user: CurrentUser``.
CurrentUser = Annotated[User, Depends(get_current_user)]


def require_csrf(request: Request, session_row: CurrentSession) -> None:
    """Validate the synchronizer CSRF token on UNSAFE (state-changing) requests.

    Apply this to every POST/PUT/PATCH/DELETE that authenticates via cookie.
    Safe methods (GET/HEAD/OPTIONS) do NOT need it - they must never change
    state, and requiring a token on them would break normal navigation.

    Two independent checks:
      1. ORIGIN/REFERER - if the browser sent an Origin, it must match this
         server or a configured frontend origin. Cheap additional defense
         (SameSite already blocks most cross-site form posts).
      2. SYNCHRONIZER TOKEN - the ``X-CSRF-Token`` header must hash to the
         digest stored on the authenticated session. We never compare against
         the readable cookie alone, so a value the attacker set themselves
         cannot pass.
    """
    settings = get_settings()

    # --- Defense 1: origin check (when the browser supplies one) ------------
    origin = request.headers.get("origin")
    if origin is not None:
        allowed = set(settings.cors_origins)
        # Same-origin requests carry the server's own origin too.
        allowed.update(
            {
                str(request.base_url).rstrip("/"),
                f"http://{request.url.netloc}",
                f"https://{request.url.netloc}",
            }
        )
        if origin.rstrip("/") not in {o.rstrip("/") for o in allowed}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed",
            )

    # --- Defense 2: synchronizer token --------------------------------------
    header_token = request.headers.get("x-csrf-token")
    if not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )

    # Compare DIGESTS with a constant-time primitive (hmac.compare_digest).
    # A plain == here could leak, byte-by-byte, how close a guess is.
    presented = hash_token(header_token)
    if not hmac.compare_digest(presented, session_row.csrf_token_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )


def require_roles(*roles: UserRole):
    """Factory producing a dependency that allows only the given roles.

    Usage:  user: Annotated[User, Depends(require_roles(UserRole.ADMIN))]

    Returns the ``User`` so a route can both gate access and use the caller.
    Raises 403 (authenticated but forbidden) - distinct from 401 so clients can
    tell "log in" apart from "you lack permission".
    """

    def dependency(session_row: CurrentSession) -> User:
        user = session_row.user
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return dependency


#: Convenience single-role guards used across Stage 4+ routes.
require_admin = require_roles(UserRole.ADMIN)
require_faculty = require_roles(UserRole.FACULTY)
require_student = require_roles(UserRole.STUDENT)
#: Admin OR faculty (any staff role that teaches/manages).
require_staff = require_roles(UserRole.ADMIN, UserRole.FACULTY)
