"""
Authentication endpoints (api/auth.py).

Mounted under the API version prefix, so the three endpoints are:

* ``POST /api/v1/auth/login``  - exchange email+password for session cookies.
* ``POST /api/v1/auth/logout`` - revoke the current session and clear cookies.
* ``GET  /api/v1/auth/me``     - who am I? The frontend's "current user" call.

Design notes:
  * Login never says WHICH of email/password was wrong, and treats a disabled
    account exactly like bad credentials (no account enumeration).
  * Logout requires the synchronizer CSRF token, because it is an unsafe,
    cookie-authenticated state change - the exact shape CSRF attacks target.
  * ``/me`` is a SAFE GET, so it needs no CSRF token (it changes nothing).
  * Responses only ever use the safe ``UserResponse`` projection - the
    password hash and token hashes can never appear in a body.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentSession, require_csrf
from app.db.session import get_db
from app.schemas.auth import LoginRequest, UserResponse
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/login",
    response_model=UserResponse,
    summary="Log in and start a session",
)
def login(
    credentials: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> UserResponse:
    """Validate credentials and set the session + CSRF cookies.

    On success the response body identifies the user (id, email, role) and the
    ``Set-Cookie`` headers carry the opaque session token (HttpOnly) and the
    CSRF token (readable). On ANY failure - unknown email, wrong password, or
    a disabled account - the response is the same generic 401.
    """
    user = auth_service.authenticate(db, credentials.email, credentials.password)
    if user is None:
        # One shared message for every failure mode (anti-enumeration).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=auth_service.INVALID_CREDENTIALS,
        )

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
    db: Annotated[Session, Depends(get_db)],
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
