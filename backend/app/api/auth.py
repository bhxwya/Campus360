"""
Authentication endpoints (api/auth.py).

Mounted under the API version prefix, so the three endpoints are:

* ``POST /api/v1/auth/login``  - exchange email+password for session cookies.
* ``POST /api/v1/auth/logout`` - revoke the current session and clear cookies.
* ``GET  /api/v1/auth/me``     - who am I? The frontend's "current user" call.
* ``POST /api/v1/auth/activate`` - set password and activate an imported
  student account (public, protected by a single-use token).

Design notes:
  * Login never says WHICH of email/password was wrong, and treats a disabled
    account exactly like bad credentials (no account enumeration). Repeated
    failures for one email from one client are throttled (429).
  * Logout requires the synchronizer CSRF token, because it is an unsafe,
    cookie-authenticated state change - the exact shape CSRF attacks target.
  * ``/me`` is a SAFE GET, so it needs no CSRF token (it changes nothing).
  * ``/activate`` is UNAUTHENTICATED: the activation token proves identity.
    It is rate-limited by client IP to deter brute-force guessing, and every
    failure returns a generic message (no account enumeration).
  * Responses only ever use the safe ``UserResponse`` projection - the
    password hash and token hashes can never appear in a body.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.deps import CurrentSession, DbSession, require_csrf
from app.core.rate_limit import FailureLimiter
from app.schemas.activation import ActivateRequest, ActivateResponse
from app.schemas.auth import LoginRequest, UserResponse
from app.services import auth as auth_service
from app.services.activation import ActivationError, activate_account

router = APIRouter(prefix="/auth", tags=["Authentication"])

#: Failed-attempt throttles for the two public endpoints (see core/rate_limit.py
#: for the single-process limitation). Login is keyed by client + email so one
#: attacker cannot lock every account; activation is keyed by client.
LOGIN_MAX_FAILURES = 5
ACTIVATION_MAX_FAILURES = 5
_WINDOW_SECONDS = 15 * 60
_login_failures = FailureLimiter(LOGIN_MAX_FAILURES, _WINDOW_SECONDS)
_activation_failures = FailureLimiter(ACTIVATION_MAX_FAILURES, _WINDOW_SECONDS)


def reset_login_throttle() -> None:
    """Forget every recorded failure (tests and operators)."""
    _login_failures.clear()
    _activation_failures.clear()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post(
    "/login",
    response_model=UserResponse,
    summary="Log in and start a session",
)
def login(
    request: Request,
    credentials: LoginRequest,
    response: Response,
    db: DbSession,
) -> UserResponse:
    """Validate credentials and set the session + CSRF cookies.

    On success the response body identifies the user (id, email, role) and the
    ``Set-Cookie`` headers carry the opaque session token (HttpOnly) and the
    CSRF token (readable). On ANY failure - unknown email, wrong password, or
    a disabled account - the response is the same generic 401. After
    ``LOGIN_MAX_FAILURES`` failures for the same email from the same client
    within 15 minutes, further attempts get 429 without checking the password.
    """
    throttle_key = (_client_ip(request), credentials.email.strip().lower())
    if _login_failures.is_blocked(throttle_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-in attempts. Please wait a few minutes and try again.",
        )

    user = auth_service.authenticate(db, credentials.email, credentials.password)
    if user is None:
        _login_failures.record_failure(throttle_key)
        # One shared message for every failure mode (anti-enumeration).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=auth_service.INVALID_CREDENTIALS,
        )

    _login_failures.reset(throttle_key)
    session_token, csrf_token = auth_service.create_session(db, user)
    auth_service.set_auth_cookies(response, session_token, csrf_token)

    # UserResponse is built from the ORM object via from_attributes; only the
    # declared fields (id, email, role) are serialised - never password_hash.
    return UserResponse.model_validate(user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out and revoke the current session",
)
def logout(
    session_row: CurrentSession,
    response: Response,
    db: DbSession,
    _csrf: Annotated[None, Depends(require_csrf)],
) -> Response:
    """Delete the current session row and clear the cookies.

    ``require_csrf`` guards this because logout is an unsafe, cookie-
    authenticated state change. Revocation is immediate and server-side: the
    row is gone, so the next request carrying the same cookie is rejected even
    if the browser has not yet dropped it.
    """
    auth_service.revoke_session(db, session_row)
    auth_service.clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserResponse, summary="Current authenticated user")
def me(session_row: CurrentSession) -> UserResponse:
    """Return the logged-in user's safe profile.

    Identity comes ONLY from the server-side session lookup - a safe GET that
    changes nothing, so no CSRF token is required.
    """
    return UserResponse.model_validate(session_row.user)


@router.post(
    "/activate",
    response_model=ActivateResponse,
    summary="Set password and activate an imported student account",
)
def activate(
    request: Request,
    payload: ActivateRequest,
    db: DbSession,
) -> ActivateResponse:
    """Activate an inactive account by consuming a single-use activation token.

    This endpoint is UNAUTHENTICATED: the token itself proves identity. Every
    failure returns the same generic message so the endpoint cannot be used to
    enumerate whether an arbitrary email has an account (anti-enumeration).

    Security properties:

    * The token is looked up by SHA-256 hash, never by the raw value.
    * The token row is locked with ``FOR UPDATE`` so concurrent activation
      requests for the same token cannot both succeed.
    * The token must be unconsumed and unexpired.
    * The linked account must exist and be inactive.
    * The password is hashed with Argon2id and stored only on success.
    * The token is marked consumed and any existing sessions are revoked,
      all in ONE transaction.
    """
    # --- Rate limiting by client IP (deters brute-force token guessing) ---
    client_ip = _client_ip(request)
    if _activation_failures.is_blocked(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many activation attempts. Please try again later.",
        )

    try:
        user = activate_account(
            db,
            raw_token=payload.token,
            new_password=payload.password,
        )
    except ActivationError as exc:
        _activation_failures.record_failure(client_ip)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return ActivateResponse(
        message="Account activated. You can now sign in with your new password.",
        email=user.email,
    )
