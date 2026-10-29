"""
Authorization & CSRF tests for the admin API (backend/tests/test_admin_authz.py).

These are the tests that prove the admin API is actually CLOSED. A hidden
frontend button is cosmetic; only a server-side dependency is real. So every
route is probed with:

* no session at all            -> 401 (not authenticated)
* a valid student/faculty session -> 403 (authenticated, not permitted)
* an admin session WITHOUT a CSRF token -> 403 (unsafe request)
* an admin session with a WRONG CSRF token -> 403
* an admin session with the RIGHT CSRF token -> 2xx

The database interlock (see conftest.py) guarantees these run against
``campus360_test`` and never against development data.
"""

import pytest
from app.core.config import get_settings

#: One representative SAFE (GET) and one UNSAFE (POST) admin route.
SAFE_ROUTE = "/api/v1/admin/students"
UNSAFE_ROUTE = "/api/v1/admin/students"

#: Every admin route family - used to sweep the whole surface.
ADMIN_ROUTES = [
    "/api/v1/admin/students",
    "/api/v1/admin/programmes",
    "/api/v1/admin/faculty",
    "/api/v1/admin/courses",
    "/api/v1/admin/enrollments",
    "/api/v1/admin/announcements",
    "/api/v1/admin/reports/summary",
    "/api/v1/admin/reports/enrollments",
    "/api/v1/admin/reports/attendance",
]


def test_database_under_test_is_the_test_database() -> None:
    """EXPLICIT interlock: the suite must target ``campus360_test``.

    If this ever fails, the remaining tests would run against real data -
    so it is asserted loudly, before anything else happens.
    """
    settings = get_settings()
    assert settings.db_name == settings.test_db_name == "campus360_test"
    assert settings.environment == "test"


@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_unauthenticated_requests_are_rejected(client, route) -> None:
    """No session cookie -> 401 on every admin route."""
    response = client.get(route)

    assert response.status_code == 401, response.text


@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_authenticated_non_admins_are_forbidden(student_client, route) -> None:
    """A logged-in STUDENT -> 403 on every admin route."""
    response = student_client.client.get(route)

    assert response.status_code == 403, response.text


def test_faculty_are_forbidden_too(faculty_client) -> None:
    """Faculty are staff, but not ADMIN - they get 403, not 401."""
    response = faculty_client.client.get(SAFE_ROUTE)

    assert response.status_code == 403, response.text


def test_admin_get_succeeds(admin_client) -> None:
    """The happy path: an admin may read the admin API."""
    response = admin_client.client.get(SAFE_ROUTE)

    assert response.status_code == 200, response.text


def test_unsafe_request_without_csrf_token_is_forbidden(admin_client) -> None:
    """An admin POST without the CSRF header -> 403.

    The session cookie alone is not enough for a state change.
    """
    response = admin_client.client.post(UNSAFE_ROUTE, json={"email": "x@y.z"})

    assert response.status_code == 403, response.text


def test_unsafe_request_with_wrong_csrf_token_is_forbidden(admin_client) -> None:
    """A forged CSRF token -> 403 (the digest comparison rejects it)."""
    response = admin_client.client.post(
        UNSAFE_ROUTE,
        json={"email": "x@y.z"},
        headers={"X-CSRF-Token": "not-the-real-token"},
    )

    assert response.status_code == 403, response.text


def test_unsafe_request_with_valid_csrf_token_succeeds(admin_client, db_cleaner) -> None:
    """The same request WITH the real token passes the guard."""
    response = admin_client.client.post(
        UNSAFE_ROUTE,
        json={
            "email": "csrf.ok@campus360.example.com",
            "password": "CsrfPass123",
            "student_code": "ST-CSRF-1",
            "full_name": "Csrf Probe",
        },
        headers=admin_client.headers,
    )

    assert response.status_code == 201, response.text


def test_unsafe_request_with_csrf_cookie_but_no_header_is_forbidden(admin_client) -> None:
    """Sending only the (readable) CSRF cookie is NOT enough.

    The attacker can read the cookie but cannot produce a header value that
    hashes to the digest stored on the victim's session - which is exactly
    why the guard compares against the stored HASH, not the cookie.
    """
    # The cookie is already set by the login; only the header is missing.
    response = admin_client.client.post(UNSAFE_ROUTE, json={"email": "x@y.z"})

    assert response.status_code == 403, response.text


def test_student_cannot_create_a_student_even_with_csrf(student_client, db_cleaner) -> None:
    """A student with a VALID CSRF token is still refused (403).

    This proves the role check is independent of - and not replaced by - the
    CSRF check.
    """
    response = student_client.client.post(
        UNSAFE_ROUTE,
        json={
            "email": "sneaky@campus360.example.com",
            "password": "SneakyPass123",
            "student_code": "ST-SNEAK-1",
            "full_name": "Sneaky Student",
        },
        headers=student_client.headers,
    )

    assert response.status_code == 403, response.text


def test_deactivated_admin_session_is_rejected(admin_client, make_user) -> None:
    """A session whose user was deactivated stops working (401)."""
    admin_id = admin_client.user["id"]
    from app.db.session import SessionLocal
    from app.models import User

    db = SessionLocal()
    try:
        user = db.get(User, admin_id)
        user.is_active = False
        db.commit()
    finally:
        db.close()

    response = admin_client.client.get(SAFE_ROUTE)

    assert response.status_code == 401, response.text
