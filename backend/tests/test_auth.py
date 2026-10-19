"""
Authentication & authorization integration tests (backend/tests/test_auth.py).

Isolation & safety (same model as test_models_integration.py):

* conftest.py forces CAMPUS360_DB_NAME=campus360_test before the app loads, so
  every connection - including Alembic's - targets the TEST database.
* ``migrated_test_db`` ASSERTS that guard before running `alembic upgrade head`.
* ``make_user`` COMMITS real users (login must see them across app sessions),
  remembers their ids, and DELETES them on teardown. Deleting a user cascades
  (ON DELETE CASCADE) to its user_sessions rows, so no session data lingers.

Role/CSRF behaviour is probed through a TEST-INTERNAL FastAPI app that wires
the REAL dependencies (require_admin, require_csrf, ...) to throwaway routes -
so we prove the guards work without shipping debug endpoints in the real API.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from alembic.config import Config
from app.api import auth as auth_router
from app.api.deps import require_admin, require_csrf, require_faculty, require_student
from app.core.config import PROJECT_ROOT, get_settings
from app.core.security import hash_token
from app.db.session import SessionLocal
from app.main import app as real_app
from app.models import User, UserRole
from app.models.session import UserSession
from app.services.auth import AuthError, create_user
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from alembic import command


@pytest.fixture(scope="session")
def migrated_test_db() -> None:
    """Bring the TEST database to head via Alembic (idempotent)."""
    settings = get_settings()
    # Hard safety interlock: never migrate anything but the test database.
    assert settings.db_name == settings.test_db_name, (
        f"refusing to migrate db {settings.db_name!r} - expected {settings.test_db_name!r}"
    )
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "head")


@pytest.fixture()
def make_user(migrated_test_db: None):
    """Factory to create committed users; all are deleted on teardown.

    Returns a callable mirroring the service signature, so tests can drive the
    real login endpoint with a known email/password/role.
    """
    created_ids: list[int] = []

    def _make(email: str, password: str, role: UserRole, is_active: bool = True) -> User:
        db = SessionLocal()
        try:
            user = create_user(db, email=email, password=password, role=role, is_active=is_active)
        finally:
            db.close()
        created_ids.append(user.id)
        return user

    yield _make

    if created_ids:
        db = SessionLocal()
        try:
            # user_sessions.user_id is ON DELETE CASCADE, so sessions go too.
            db.query(User).filter(User.id.in_(created_ids)).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


@pytest.fixture()
def client(migrated_test_db: None) -> TestClient:
    """TestClient bound to the REAL app (login/logout/me live there)."""
    with TestClient(real_app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# A TEST-INTERNAL probe app: real dependencies, throwaway routes.
# This proves require_admin / require_faculty / require_student / require_csrf
# actually gate access, WITHOUT shipping debug endpoints in the real API.
# ---------------------------------------------------------------------------
def _build_probe_app() -> FastAPI:
    probe = FastAPI()
    probe.include_router(auth_router.router, prefix="/api/v1")

    @probe.get("/probe/admin")
    def _admin(user: Annotated[User, Depends(require_admin)]) -> dict[str, str]:
        return {"role": user.role.value}

    @probe.get("/probe/faculty")
    def _faculty(user: Annotated[User, Depends(require_faculty)]) -> dict[str, str]:
        return {"role": user.role.value}

    @probe.get("/probe/student")
    def _student(user: Annotated[User, Depends(require_student)]) -> dict[str, str]:
        return {"role": user.role.value}

    @probe.post("/probe/unsafe")
    def _unsafe(
        user: Annotated[User, Depends(require_admin)],
        _csrf: Annotated[None, Depends(require_csrf)],
    ) -> dict[str, str]:
        # Both guards: admin role AND a valid synchronizer CSRF token.
        return {"role": user.role.value}

    return probe


@pytest.fixture()
def probe_client(migrated_test_db: None) -> TestClient:
    """TestClient bound to the probe app (for role/CSRF access tests)."""
    with TestClient(_build_probe_app()) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Login success & safe responses
# ---------------------------------------------------------------------------
def test_login_success_sets_cookies_and_safe_body(client, make_user) -> None:
    """A valid login returns a safe user body AND sets both cookies."""
    make_user("alice@campus.edu", "password-123", UserRole.STUDENT)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@campus.edu", "password": "password-123"},
    )

    assert resp.status_code == 200
    body = resp.json()
    # Exactly the safe projection - no password_hash, no token.
    assert set(body) == {"id", "email", "role"}
    assert body["email"] == "alice@campus.edu"
    assert body["role"] == "student"

    # Both cookies are set; the session cookie must be HttpOnly + SameSite=Lax.
    joined = " ".join(resp.headers.get_list("set-cookie")).lower()
    assert "campus360_session=" in joined
    assert "campus360_csrf=" in joined
    assert "httponly" in joined
    assert "samesite=lax" in joined


def test_login_response_never_contains_password_hash(client, make_user) -> None:
    """The raw response must not leak the hash, the password, or a token."""
    user = make_user("bob@campus.edu", "hunter2222", UserRole.FACULTY)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "bob@campus.edu", "password": "hunter2222"},
    )

    assert resp.status_code == 200
    text = resp.text.lower()
    assert "password_hash" not in text
    assert "hunter2222" not in text  # the password itself is never echoed back
    assert user.password_hash not in resp.text  # nor the stored Argon2 hash


# ---------------------------------------------------------------------------
# Invalid credentials - one identical error for every failure mode
# ---------------------------------------------------------------------------
def test_login_wrong_password_is_generic_401(client, make_user) -> None:
    make_user("carol@campus.edu", "right-password", UserRole.STUDENT)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "carol@campus.edu", "password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


def test_login_unknown_email_is_generic_401(client) -> None:
    """Unknown email must return the SAME error as a wrong password."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@campus.edu", "password": "whatever"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


def test_login_disabled_account_is_generic_401(client, make_user) -> None:
    """A DISABLED account with the CORRECT password gets the same generic 401.

    Adjustment from review: we must not reveal the account exists-and-is-
    disabled with a distinct 403. It looks identical to bad credentials.
    """
    make_user("dave@campus.edu", "correct-pw", UserRole.STUDENT, is_active=False)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "dave@campus.edu", "password": "correct-pw"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


def test_login_malformed_body_is_422(client) -> None:
    """A missing/invalid field is rejected by Pydantic before any auth logic."""
    resp = client.post("/api/v1/auth/login", json={"email": "not-an-email"})
    assert resp.status_code == 422


def test_login_is_case_insensitive(client, make_user) -> None:
    """Login with a MIXED-CASE email finds the normalized (lowercase) row."""
    make_user("erin@campus.edu", "password-abc", UserRole.STUDENT)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "Erin@Campus.EDU", "password": "password-abc"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "erin@campus.edu"


def test_duplicate_email_creation_is_rejected(make_user) -> None:
    """Two accounts with the same (normalized) email are refused.

    The service layer catches the DB IntegrityError and re-raises it as a
    clean AuthError(409), so callers get a friendly conflict - not a 500.
    """
    make_user("frank@campus.edu", "password-1", UserRole.STUDENT)
    with pytest.raises(AuthError) as excinfo:
        make_user("frank@campus.edu", "password-2", UserRole.STUDENT)
    assert excinfo.value.status_code == 409


# ---------------------------------------------------------------------------
# /me and missing/malformed authentication
# ---------------------------------------------------------------------------
def test_me_without_cookie_is_401(client) -> None:
    """No session cookie -> 401, never a 500 or a default user."""
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_with_garbage_cookie_is_401(client) -> None:
    """A cookie that maps to no session row -> 401 (no crash, no leak)."""
    client.cookies.set("campus360_session", "not-a-real-token")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_returns_logged_in_user(client, make_user) -> None:
    """After login, /me reports the session's user - never a client-supplied id."""
    make_user("grace@campus.edu", "password-xyz", UserRole.FACULTY)
    client.post(
        "/api/v1/auth/login", json={"email": "grace@campus.edu", "password": "password-xyz"}
    )

    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "grace@campus.edu"
    assert body["role"] == "faculty"
    assert set(body) == {"id", "email", "role"}


def test_me_ignores_client_supplied_role_or_id(client, make_user) -> None:
    """Identity comes only from the server-side session, not the request."""
    make_user("henry@campus.edu", "password-123", UserRole.STUDENT)
    client.post(
        "/api/v1/auth/login", json={"email": "henry@campus.edu", "password": "password-123"}
    )

    # Even if a request tries to claim admin/another id, /me returns the real role.
    resp = client.get("/api/v1/auth/me?role=admin&user_id=9999", headers={"X-Role": "admin"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "student"


# ---------------------------------------------------------------------------
# Logout: CSRF required, revocation is immediate
# ---------------------------------------------------------------------------
def test_logout_without_csrf_token_is_403(client, make_user) -> None:
    """Logout is an unsafe cookie-authenticated action -> CSRF token required."""
    make_user("ivan@campus.edu", "password-123", UserRole.STUDENT)
    client.post("/api/v1/auth/login", json={"email": "ivan@campus.edu", "password": "password-123"})

    assert client.post("/api/v1/auth/logout").status_code == 403  # no X-CSRF-Token


def test_logout_with_wrong_csrf_token_is_403(client, make_user) -> None:
    """A wrong synchronizer token is rejected (not just any non-empty value)."""
    make_user("judy@campus.edu", "password-123", UserRole.STUDENT)
    client.post("/api/v1/auth/login", json={"email": "judy@campus.edu", "password": "password-123"})

    resp = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong-token"})
    assert resp.status_code == 403


def test_logout_revokes_session_immediately(client, make_user) -> None:
    """After a CSRF-valid logout, the same cookie is rejected on the next call."""
    make_user("kate@campus.edu", "password-123", UserRole.STUDENT)
    client.post("/api/v1/auth/login", json={"email": "kate@campus.edu", "password": "password-123"})

    csrf = client.cookies.get("campus360_csrf")
    resp = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 204

    # The session row is gone, so /me with the same cookie is now 401.
    assert client.get("/api/v1/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# Role-based authorization (server-enforced)
# ---------------------------------------------------------------------------
def _login(probe_client, email: str, password: str) -> None:
    resp = probe_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200


def test_admin_reaches_admin_route(probe_client, make_user) -> None:
    make_user("admin@campus.edu", "password-123", UserRole.ADMIN)
    _login(probe_client, "admin@campus.edu", "password-123")
    assert probe_client.get("/probe/admin").status_code == 200


def test_faculty_reaches_faculty_route(probe_client, make_user) -> None:
    make_user("prof@campus.edu", "password-123", UserRole.FACULTY)
    _login(probe_client, "prof@campus.edu", "password-123")
    assert probe_client.get("/probe/faculty").status_code == 200


def test_student_reaches_student_route(probe_client, make_user) -> None:
    make_user("stu@campus.edu", "password-123", UserRole.STUDENT)
    _login(probe_client, "stu@campus.edu", "password-123")
    assert probe_client.get("/probe/student").status_code == 200


def test_student_denied_admin_route(probe_client, make_user) -> None:
    """A logged-in student must NOT reach an admin-only route (403)."""
    make_user("stu2@campus.edu", "password-123", UserRole.STUDENT)
    _login(probe_client, "stu2@campus.edu", "password-123")
    assert probe_client.get("/probe/admin").status_code == 403


def test_faculty_denied_admin_route(probe_client, make_user) -> None:
    """Faculty are privileged but still NOT admin - 403, not 200."""
    make_user("prof2@campus.edu", "password-123", UserRole.FACULTY)
    _login(probe_client, "prof2@campus.edu", "password-123")
    assert probe_client.get("/probe/admin").status_code == 403


def test_admin_denied_student_route(probe_client, make_user) -> None:
    """Role gates are exact, not a ladder - admin is not automatically student."""
    make_user("admin2@campus.edu", "password-123", UserRole.ADMIN)
    _login(probe_client, "admin2@campus.edu", "password-123")
    assert probe_client.get("/probe/student").status_code == 403


def test_unauthenticated_denied_role_route(probe_client) -> None:
    """No login at all -> 401 (not even a 403) on a role-gated route."""
    assert probe_client.get("/probe/admin").status_code == 401


def test_unsafe_route_requires_csrf_even_for_admin(probe_client, make_user) -> None:
    """An admin hitting an unsafe route WITHOUT a valid CSRF token gets 403."""
    make_user("admin3@campus.edu", "password-123", UserRole.ADMIN)
    _login(probe_client, "admin3@campus.edu", "password-123")

    # Right role, valid session, but a bogus X-CSRF-Token -> CSRF rejects it.
    resp = probe_client.post("/probe/unsafe", headers={"X-CSRF-Token": "bogus"})
    assert resp.status_code == 403


def test_expired_session_is_rejected(client, make_user) -> None:
    """A session row past its expires_at is treated as unauthenticated."""
    make_user("lena@campus.edu", "password-123", UserRole.STUDENT)
    client.post("/api/v1/auth/login", json={"email": "lena@campus.edu", "password": "password-123"})

    # Backdate the row's expiry directly, so the next request sees it as expired.
    raw_token = client.cookies.get("campus360_session")
    db = SessionLocal()
    try:
        row = db.query(UserSession).filter(UserSession.token_hash == hash_token(raw_token)).one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=5)
        db.commit()
    finally:
        db.close()

    assert client.get("/api/v1/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# Stage 3.1 regression tests (from the read-only security audit)
# ---------------------------------------------------------------------------
def test_active_session_rejected_after_user_deactivated(client, make_user) -> None:
    """Deactivating an account must invalidate its LIVE session immediately.

    The audit verified this manually; this pins it as a regression test. A
    session minted while the account was active must stop working the instant
    the account is deactivated - not just at the next login attempt.
    """
    user = make_user("deact@campus.edu", "password-123", UserRole.STUDENT)
    client.post(
        "/api/v1/auth/login", json={"email": "deact@campus.edu", "password": "password-123"}
    )
    assert client.get("/api/v1/auth/me").status_code == 200

    # Deactivate behind the scenes while the session cookie is still live.
    db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == user.id).one()
        row.is_active = False
        db.commit()
    finally:
        db.close()

    # The very next authenticated request is rejected, despite a valid cookie.
    assert client.get("/api/v1/auth/me").status_code == 401


def test_client_controlled_csrf_cookie_cannot_satisfy_validation(client, make_user) -> None:
    """A CSRF token taken from a client-set cookie alone must NOT pass.

    The synchronizer token is checked against the HASH stored on the session
    row, not against the readable cookie. So if an attacker overwrites the
    ``campus360_csrf`` cookie, neither echoing that value back nor sending it
    as the X-CSRF-Token header is accepted - the attacker-controlled value does
    not hash to the victim session's stored digest.
    """
    make_user("csrf-cookie@campus.edu", "password-123", UserRole.STUDENT)
    client.post(
        "/api/v1/auth/login",
        json={"email": "csrf-cookie@campus.edu", "password": "password-123"},
    )

    # Attacker replaces the readable CSRF cookie with their own value.
    attacker_value = "attacker-controlled-csrf"
    client.cookies.set("campus360_csrf", attacker_value)

    # No header at all -> rejected (the cookie by itself proves nothing).
    assert client.post("/api/v1/auth/logout").status_code == 403
    # Header copied from the attacker cookie -> still rejected: it does not
    # hash to the digest the server stored for this session.
    resp = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": attacker_value})
    assert resp.status_code == 403


def test_valid_csrf_token_with_untrusted_origin_is_rejected(probe_client, make_user) -> None:
    """A correct synchronizer token is NOT enough if Origin is untrusted.

    Origin validation is an additional CSRF defense layered on top of the
    token. A request carrying the RIGHT token but a foreign Origin header is
    still refused, so a cross-site page cannot replay a stolen token.
    """
    make_user("origin-admin@campus.edu", "password-123", UserRole.ADMIN)
    probe_client.post(
        "/api/v1/auth/login",
        json={"email": "origin-admin@campus.edu", "password": "password-123"},
    )
    valid_csrf = probe_client.cookies.get("campus360_csrf")

    # Correct role, correct session, correct token - but an evil Origin.
    resp = probe_client.post(
        "/probe/unsafe",
        headers={"X-CSRF-Token": valid_csrf, "Origin": "https://evil.example"},
    )
    assert resp.status_code == 403

    # Sanity: with no Origin (non-browser client) the same token succeeds, so
    # the 403 above was caused by the Origin check, not a bad token.
    ok = probe_client.post("/probe/unsafe", headers={"X-CSRF-Token": valid_csrf})
    assert ok.status_code == 200
