"""
Admin API tests for students (backend/tests/test_admin_students.py).

Covers the student lifecycle: create (including the ATOMIC account+profile
write), list with filters, read, partial update, and deactivate.

Every test asserts the two safety properties that matter most:
  * a response never contains a password or a password hash, and
  * a failed creation leaves NO orphan user behind.
"""

import pytest
from app.db.session import SessionLocal
from app.models import StudentProfile, User


def _create(admin_client, **overrides) -> dict:
    """POST a student and return the parsed body (asserting 201)."""
    payload = {
        "email": "ada@campus360.example.com",
        "password": "StudentPass123",
        "student_code": "ST-2026-0001",
        "full_name": "Ada Lovelace",
        "degree_program": "B.Tech CSE",
        "current_semester": 3,
    }
    payload.update(overrides)
    response = admin_client.client.post(
        "/api/v1/admin/students", json=payload, headers=admin_client.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_student_returns_201_and_safe_body(admin_client, db_cleaner) -> None:
    """Creating a student returns the safe projection - never the hash."""
    body = _create(admin_client)

    assert body["email"] == "ada@campus360.example.com"
    assert body["student_code"] == "ST-2026-0001"
    assert body["full_name"] == "Ada Lovelace"
    assert body["degree_program"] == "B.Tech CSE"
    assert body["current_semester"] == 3
    assert body["role"] == "student"
    assert body["is_active"] is True
    assert body["enrolled_courses"] == 0
    # No attendance records yet, so the rate is honestly "unknown" (null).
    assert body["attendance_percentage"] is None
    # The forbidden fields must not appear anywhere in the body.
    assert "password" not in body
    assert "password_hash" not in body


def test_create_student_also_creates_the_login_account(admin_client, db_cleaner) -> None:
    """The profile is bound to a real, usable account."""
    body = _create(admin_client)

    db = SessionLocal()
    try:
        user = db.get(User, body["user_id"])
        profile = db.get(StudentProfile, body["id"])
        assert user is not None and profile is not None
        assert user.role.value == "student"
        assert user.email_normalized == user.email.lower()
        # The stored password must be a HASH, never the plain text.
        assert user.password_hash != "StudentPass123"
        assert user.password_hash.startswith("$argon2")
    finally:
        db.close()


def test_created_student_can_log_in(admin_client, db_cleaner) -> None:
    """End-to-end: the account the admin created actually works."""
    _create(admin_client)

    response = admin_client.client.post(
        "/api/v1/auth/login",
        json={"email": "ada@campus360.example.com", "password": "StudentPass123"},
    )

    assert response.status_code == 200, response.text


def test_duplicate_email_returns_409(admin_client, db_cleaner) -> None:
    """A second student with the same email -> 409, not 500."""
    _create(admin_client)

    response = admin_client.client.post(
        "/api/v1/admin/students",
        json={
            "email": "ada@campus360.example.com",
            "password": "OtherPass123",
            "student_code": "ST-2026-9999",
            "full_name": "Impostor",
        },
        headers=admin_client.headers,
    )

    assert response.status_code == 409, response.text


def test_duplicate_student_code_returns_409(admin_client, db_cleaner) -> None:
    """A second student with the same roll number -> 409."""
    _create(admin_client)

    response = admin_client.client.post(
        "/api/v1/admin/students",
        json={
            "email": "other@campus360.example.com",
            "password": "OtherPass123",
            "student_code": "ST-2026-0001",
            "full_name": "Impostor",
        },
        headers=admin_client.headers,
    )

    assert response.status_code == 409, response.text


def test_failed_creation_leaves_no_orphan_user(admin_client, db_cleaner) -> None:
    """A failed creation must not leave a login account behind.

    The account and the profile are written in ONE transaction: if the
    profile insert fails (duplicate code), the account insert is rolled
    back too. This test pins that guarantee.
    """
    _create(admin_client)  # takes student_code ST-2026-0001

    response = admin_client.client.post(
        "/api/v1/admin/students",
        json={
            "email": "orphan@campus360.example.com",
            "password": "OrphanPass123",
            "student_code": "ST-2026-0001",  # duplicate -> the write fails
            "full_name": "Orphan",
        },
        headers=admin_client.headers,
    )
    assert response.status_code == 409, response.text

    db = SessionLocal()
    try:
        orphan = (
            db.query(User)
            .filter(User.email_normalized == "orphan@campus360.example.com")
            .one_or_none()
        )
        assert orphan is None, "a rolled-back creation left an orphan user behind"
    finally:
        db.close()


def test_invalid_payload_returns_422(admin_client, db_cleaner) -> None:
    """A short password / bad email is rejected at the edge with 422."""
    response = admin_client.client.post(
        "/api/v1/admin/students",
        json={
            "email": "not-an-email",
            "password": "short",
            "student_code": "",
            "full_name": "",
        },
        headers=admin_client.headers,
    )

    assert response.status_code == 422, response.text


def test_list_students_is_paginated(admin_client, db_cleaner) -> None:
    """The list endpoint reports the total AND returns only a page."""
    _create(admin_client, email="a@campus360.example.com", student_code="ST-A", full_name="Ann")
    _create(admin_client, email="b@campus360.example.com", student_code="ST-B", full_name="Bob")

    response = admin_client.client.get("/api/v1/admin/students")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 2
    assert body["limit"] == 20
    assert body["offset"] == 0
    assert len(body["items"]) == body["total"]


def test_list_students_supports_search(admin_client, db_cleaner) -> None:
    """The search box filters by name, code or email."""
    _create(
        admin_client,
        email="grace@campus360.example.com",
        student_code="ST-G",
        full_name="Grace",
    )

    response = admin_client.client.get("/api/v1/admin/students", params={"search": "grace"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["student_code"] == "ST-G"


def test_list_students_filters_by_department(admin_client, db_cleaner) -> None:
    """``department`` filters on the degree program (there is no other column)."""
    _create(
        admin_client,
        email="cse@campus360.example.com",
        student_code="ST-CSE",
        full_name="Cse Student",
        degree_program="B.Tech CSE",
    )
    _create(
        admin_client,
        email="ece@campus360.example.com",
        student_code="ST-ECE",
        full_name="Ece Student",
        degree_program="B.Tech ECE",
    )

    response = admin_client.client.get(
        "/api/v1/admin/students", params={"department": "B.Tech ECE"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["student_code"] == "ST-ECE"


def test_get_student_by_id(admin_client, db_cleaner) -> None:
    """One student can be fetched by profile id."""
    created = _create(admin_client)

    response = admin_client.client.get(f"/api/v1/admin/students/{created['id']}")

    assert response.status_code == 200, response.text
    assert response.json()["student_code"] == "ST-2026-0001"


def test_get_unknown_student_returns_404(admin_client, db_cleaner) -> None:
    """A missing student is a clean 404."""
    response = admin_client.client.get("/api/v1/admin/students/99999999")

    assert response.status_code == 404, response.text


def test_patch_student_updates_only_supplied_fields(admin_client, db_cleaner) -> None:
    """A PATCH is partial: unsent fields keep their value."""
    created = _create(admin_client)

    response = admin_client.client.patch(
        f"/api/v1/admin/students/{created['id']}",
        json={"phone": "+91-9876543210"},
        headers=admin_client.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["phone"] == "+91-9876543210"
    # Untouched by the patch.
    assert body["full_name"] == "Ada Lovelace"
    assert body["degree_program"] == "B.Tech CSE"


def test_patch_unknown_student_returns_404(admin_client, db_cleaner) -> None:
    """Patching a missing student is a 404."""
    response = admin_client.client.patch(
        "/api/v1/admin/students/99999999",
        json={"phone": "123"},
        headers=admin_client.headers,
    )

    assert response.status_code == 404, response.text


def test_deactivate_student_disables_the_login_and_keeps_the_row(admin_client, db_cleaner) -> None:
    """Deactivation flips the flag; the row survives (history is preserved)."""
    created = _create(admin_client)

    response = admin_client.client.delete(
        f"/api/v1/admin/students/{created['id']}", headers=admin_client.headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False

    db = SessionLocal()
    try:
        # The PROFILE is still there...
        assert db.get(StudentProfile, created["id"]) is not None
        # ...and the account is deactivated, so the login no longer works.
        assert db.get(User, created["user_id"]).is_active is False
    finally:
        db.close()

    login = admin_client.client.post(
        "/api/v1/auth/login",
        json={"email": "ada@campus360.example.com", "password": "StudentPass123"},
    )
    assert login.status_code == 401, login.text


def test_deactivated_student_is_excluded_by_the_active_filter(admin_client, db_cleaner) -> None:
    """The ``is_active`` filter separates active from inactive students."""
    created = _create(admin_client)
    admin_client.client.delete(
        f"/api/v1/admin/students/{created['id']}", headers=admin_client.headers
    )

    active = admin_client.client.get("/api/v1/admin/students", params={"is_active": True})
    inactive = admin_client.client.get("/api/v1/admin/students", params={"is_active": False})

    assert active.status_code == 200 and inactive.status_code == 200
    active_codes = {item["student_code"] for item in active.json()["items"]}
    inactive_codes = {item["student_code"] for item in inactive.json()["items"]}
    assert "ST-2026-0001" not in active_codes
    assert "ST-2026-0001" in inactive_codes


@pytest.mark.parametrize("limit", [0, 101])
def test_out_of_range_limit_returns_422(admin_client, db_cleaner, limit) -> None:
    """The page size is bounded, so a client cannot ask for everything."""
    response = admin_client.client.get("/api/v1/admin/students", params={"limit": limit})

    assert response.status_code == 422, response.text
