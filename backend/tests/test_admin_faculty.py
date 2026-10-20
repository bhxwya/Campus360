"""
Admin API tests for faculty (backend/tests/test_admin_faculty.py).

Mirrors the student suite: create (atomic account+profile), list/filter,
read, partial update, deactivate - plus the faculty-specific behaviour that
a course can be led by a faculty member and that the count of led courses
is reported.
"""

import pytest
from app.db.session import SessionLocal
from app.models import FacultyProfile, User


def _create(admin_client, **overrides) -> dict:
    """POST a faculty member and return the parsed body (asserting 201)."""
    payload = {
        "email": "einstein@campus360.example.com",
        "password": "FacultyPass123",
        "faculty_code": "FAC-2026-0001",
        "full_name": "Albert Einstein",
        "department": "Physics",
        "designation": "Professor",
    }
    payload.update(overrides)
    response = admin_client.client.post(
        "/api/v1/admin/faculty", json=payload, headers=admin_client.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_faculty_returns_201_and_safe_body(admin_client, db_cleaner) -> None:
    """Creating a faculty member returns the safe projection."""
    body = _create(admin_client)

    assert body["email"] == "einstein@campus360.example.com"
    assert body["faculty_code"] == "FAC-2026-0001"
    assert body["full_name"] == "Albert Einstein"
    assert body["department"] == "Physics"
    assert body["designation"] == "Professor"
    assert body["role"] == "faculty"
    assert body["is_active"] is True
    assert body["assigned_courses"] == 0
    assert "password" not in body
    assert "password_hash" not in body


def test_create_faculty_also_creates_the_login_account(admin_client, db_cleaner) -> None:
    """The faculty profile is bound to a real, usable account."""
    body = _create(admin_client)

    db = SessionLocal()
    try:
        user = db.get(User, body["user_id"])
        assert db.get(FacultyProfile, body["id"]) is not None
        assert user.role.value == "faculty"
        assert user.password_hash != "FacultyPass123"
        assert user.password_hash.startswith("$argon2")
    finally:
        db.close()


def test_created_faculty_can_log_in(admin_client, db_cleaner) -> None:
    """End-to-end: the account the admin created actually works."""
    _create(admin_client)

    response = admin_client.client.post(
        "/api/v1/auth/login",
        json={"email": "einstein@campus360.example.com", "password": "FacultyPass123"},
    )

    assert response.status_code == 200, response.text


def test_duplicate_email_returns_409(admin_client, db_cleaner) -> None:
    """A second faculty member with the same email -> 409."""
    _create(admin_client)

    response = admin_client.client.post(
        "/api/v1/admin/faculty",
        json={
            "email": "einstein@campus360.example.com",
            "password": "OtherPass123",
            "faculty_code": "FAC-9999",
            "full_name": "Impostor",
        },
        headers=admin_client.headers,
    )

    assert response.status_code == 409, response.text


def test_duplicate_faculty_code_returns_409(admin_client, db_cleaner) -> None:
    """A second faculty member with the same code -> 409."""
    _create(admin_client)

    response = admin_client.client.post(
        "/api/v1/admin/faculty",
        json={
            "email": "other@campus360.example.com",
            "password": "OtherPass123",
            "faculty_code": "FAC-2026-0001",
            "full_name": "Impostor",
        },
        headers=admin_client.headers,
    )

    assert response.status_code == 409, response.text


def test_failed_creation_leaves_no_orphan_user(admin_client, db_cleaner) -> None:
    """A rolled-back creation must not leave a login account behind."""
    _create(admin_client)

    response = admin_client.client.post(
        "/api/v1/admin/faculty",
        json={
            "email": "orphan@campus360.example.com",
            "password": "OrphanPass123",
            "faculty_code": "FAC-2026-0001",  # duplicate -> the write fails
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
    """Bad input is rejected at the edge with 422."""
    response = admin_client.client.post(
        "/api/v1/admin/faculty",
        json={"email": "nope", "password": "x", "faculty_code": "", "full_name": ""},
        headers=admin_client.headers,
    )

    assert response.status_code == 422, response.text


def test_list_faculty_is_paginated(admin_client, db_cleaner) -> None:
    """The list endpoint reports the total AND returns only a page."""
    _create(admin_client, email="a@campus360.example.com", faculty_code="FAC-A", full_name="Ann")
    _create(admin_client, email="b@campus360.example.com", faculty_code="FAC-B", full_name="Bob")

    response = admin_client.client.get("/api/v1/admin/faculty")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 2
    assert body["limit"] == 20
    assert body["offset"] == 0


def test_list_faculty_supports_search(admin_client, db_cleaner) -> None:
    """The search box filters by name, code or email."""
    _create(
        admin_client,
        email="curie@campus360.example.com",
        faculty_code="FAC-CURIE",
        full_name="Marie Curie",
    )

    response = admin_client.client.get("/api/v1/admin/faculty", params={"search": "curie"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["faculty_code"] == "FAC-CURIE"


def test_list_faculty_filters_by_department(admin_client, db_cleaner) -> None:
    """``department`` filters on the faculty's own department column."""
    _create(
        admin_client,
        email="phys@campus360.example.com",
        faculty_code="FAC-PHYS",
        full_name="Physicist",
        department="Physics",
    )
    _create(
        admin_client,
        email="cs@campus360.example.com",
        faculty_code="FAC-CS",
        full_name="Computer Scientist",
        department="Computer Science",
    )

    response = admin_client.client.get("/api/v1/admin/faculty", params={"department": "Physics"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["faculty_code"] == "FAC-PHYS"


def test_get_faculty_by_id(admin_client, db_cleaner) -> None:
    """One faculty member can be fetched by profile id."""
    created = _create(admin_client)

    response = admin_client.client.get(f"/api/v1/admin/faculty/{created['id']}")

    assert response.status_code == 200, response.text
    assert response.json()["faculty_code"] == "FAC-2026-0001"


def test_get_unknown_faculty_returns_404(admin_client, db_cleaner) -> None:
    """A missing faculty member is a clean 404."""
    response = admin_client.client.get("/api/v1/admin/faculty/99999999")

    assert response.status_code == 404, response.text


def test_patch_faculty_updates_only_supplied_fields(admin_client, db_cleaner) -> None:
    """A PATCH is partial: unsent fields keep their value."""
    created = _create(admin_client)

    response = admin_client.client.patch(
        f"/api/v1/admin/faculty/{created['id']}",
        json={"designation": "Associate Professor"},
        headers=admin_client.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["designation"] == "Associate Professor"
    assert body["full_name"] == "Albert Einstein"
    assert body["department"] == "Physics"


def test_patch_unknown_faculty_returns_404(admin_client, db_cleaner) -> None:
    """Patching a missing faculty member is a 404."""
    response = admin_client.client.patch(
        "/api/v1/admin/faculty/99999999",
        json={"designation": "X"},
        headers=admin_client.headers,
    )

    assert response.status_code == 404, response.text


def test_deactivate_faculty_disables_the_login_and_keeps_the_row(admin_client, db_cleaner) -> None:
    """Deactivation flips the flag; the row survives."""
    created = _create(admin_client)

    response = admin_client.client.delete(
        f"/api/v1/admin/faculty/{created['id']}", headers=admin_client.headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False

    db = SessionLocal()
    try:
        assert db.get(FacultyProfile, created["id"]) is not None
        assert db.get(User, created["user_id"]).is_active is False
    finally:
        db.close()

    login = admin_client.client.post(
        "/api/v1/auth/login",
        json={"email": "einstein@campus360.example.com", "password": "FacultyPass123"},
    )
    assert login.status_code == 401, login.text


def test_assigned_courses_count_is_reported(admin_client, db_cleaner) -> None:
    """A faculty member who leads a course reports it in the list."""
    faculty = _create(admin_client)
    course = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "PH-101",
            "title": "Classical Mechanics",
            "credits": 4,
            "department": "Physics",
            "lead_faculty_id": faculty["id"],
        },
        headers=admin_client.headers,
    )
    assert course.status_code == 201, course.text

    response = admin_client.client.get("/api/v1/admin/faculty")
    assert response.status_code == 200, response.text
    entry = next(item for item in response.json()["items"] if item["id"] == faculty["id"])
    assert entry["assigned_courses"] == 1


@pytest.mark.parametrize("limit", [0, 101])
def test_out_of_range_limit_returns_422(admin_client, db_cleaner, limit) -> None:
    """The page size is bounded."""
    response = admin_client.client.get("/api/v1/admin/faculty", params={"limit": limit})

    assert response.status_code == 422, response.text
