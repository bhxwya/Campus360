"""
Admin API tests for students (backend/tests/test_admin_students.py).

Covers the student lifecycle: create (including the ATOMIC account+profile
write), list with filters, read, partial update, and deactivate.

Every test asserts the two safety properties that matter most:
  * a response never contains a password or a password hash, and
  * a failed creation leaves NO orphan user behind.
"""

import pytest
from app.core.config import PROJECT_ROOT
from app.core.programmes import SEMESTER_POLICY
from app.db.session import SessionLocal
from app.main import app
from app.models import StudentProfile, User
from app.services import admin as admin_service
from fastapi.testclient import TestClient


def _student_payload(**overrides) -> dict:
    """A valid student body, with per-test overrides applied."""
    payload = {
        "email": "ada@campus360.example.com",
        "password": "StudentPass123",
        "student_code": "ST-2026-0001",
        "full_name": "Ada Lovelace",
        "degree_program": "B.Tech CSE",
        "current_semester": 3,
    }
    payload.update(overrides)
    return payload


def _create(admin_client, **overrides) -> dict:
    """POST a student and return the parsed body (asserting 201)."""
    response = admin_client.client.post(
        "/api/v1/admin/students",
        json=_student_payload(**overrides),
        headers=admin_client.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_student_returns_201_and_safe_body(admin_client, db_cleaner) -> None:
    """Creating a student returns the safe projection - never the hash."""
    body = _create(admin_client)

    assert body["email"] == "ada@campus360.example.com"
    assert body["student_code"] == "ST-2026-0001"
    assert body["full_name"] == "Ada Lovelace"
    assert body["degree_program"] == "B.Tech Computer Science and Engineering (CSE)"
    assert body["programme_code"] == "BTECH-CSE"
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
    """A second student with the same email -> 409 with only the email field."""
    _create(admin_client)

    response = admin_client.client.post(
        "/api/v1/admin/students",
        json={
            "email": "ADA@CAMPUS360.EXAMPLE.COM",  # case-insensitive duplicate
            "password": "OtherPass123",
            "student_code": "ST-2026-9999",
            "full_name": "Impostor",
        },
        headers=admin_client.headers,
    )

    assert response.status_code == 409, response.text
    field_errors = response.json()["detail"]["field_errors"]
    assert field_errors == {
        "email": "This email address is already registered.",
    }
    assert "student_code" not in field_errors


def test_duplicate_student_code_returns_409(admin_client, db_cleaner) -> None:
    """A second student with the same roll number -> only that field errors."""
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
    field_errors = response.json()["detail"]["field_errors"]
    assert field_errors == {
        "student_code": "This roll number is already assigned to another student.",
    }
    assert "email" not in field_errors


def test_both_duplicate_fields_are_reported_independently(admin_client, db_cleaner) -> None:
    """Both duplicates are returned together; neither message hides the other."""
    _create(admin_client)

    response = admin_client.client.post(
        "/api/v1/admin/students",
        json={
            "email": "ada@campus360.example.com",
            "password": "OtherPass123",
            "student_code": "ST-2026-0001",
            "full_name": "Impostor",
        },
        headers=admin_client.headers,
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["field_errors"] == {
        "email": "This email address is already registered.",
        "student_code": "This roll number is already assigned to another student.",
    }
    assert "email" in detail["message"].lower()
    assert "roll number" in detail["message"].lower()


def test_create_recovers_from_a_concurrent_unique_conflict(
    admin_client, db_cleaner, monkeypatch
) -> None:
    """A writer that wins AFTER the pre-check is still mapped to its field.

    The monkeypatch skips the first pre-check only, so the INSERT genuinely
    raises the database's unique violation. The post-rollback lookup is the
    production recovery path for a concurrent create.
    """
    _create(admin_client)
    original = admin_service._student_field_conflicts
    calls = {"count": 0}

    def miss_first_precheck(db, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {}
        return original(db, **kwargs)

    monkeypatch.setattr(admin_service, "_student_field_conflicts", miss_first_precheck)

    response = admin_client.client.post(
        "/api/v1/admin/students",
        json={
            "email": "ada@campus360.example.com",
            "password": "OtherPass123",
            "student_code": "ST-2026-0001",
            "full_name": "Racing Impostor",
        },
        headers=admin_client.headers,
    )

    assert response.status_code == 409, response.text
    assert calls["count"] == 2
    assert response.json()["detail"]["field_errors"] == {
        "email": "This email address is already registered.",
        "student_code": "This roll number is already assigned to another student.",
    }


def test_editing_a_students_own_identity_values_is_not_a_conflict(admin_client, db_cleaner) -> None:
    """Submitting the student's own email/code (even case-varied) must succeed."""
    created = _create(admin_client)

    response = admin_client.client.patch(
        f"/api/v1/admin/students/{created['id']}",
        json={
            "email": "ADA@CAMPUS360.EXAMPLE.COM",
            "student_code": created["student_code"],
        },
        headers=admin_client.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == created["id"]
    assert body["user_id"] == created["user_id"]
    assert body["email"] == "ADA@campus360.example.com"
    assert body["student_code"] == created["student_code"]


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


def test_student_details_data_uses_existing_student_and_enrollment_endpoints(
    admin_client, db_cleaner
) -> None:
    """The read-only Details view is backed by existing, authorized endpoints.

    This pins the exact data the dialog will display, including the separate
    Student ID (profile id), phone, programme, semester, account state, and
    enrolled course rows. No new API endpoint is introduced.
    """
    created = _create(admin_client, phone="+91-9876500000")
    course = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "DET-101",
            "title": "Details Verification",
            "credits": 3,
            "department": "Computer Science",
        },
        headers=admin_client.headers,
    )
    assert course.status_code == 201, course.text
    course_body = course.json()
    enrollment = admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": created["id"], "course_id": course_body["id"]},
        headers=admin_client.headers,
    )
    assert enrollment.status_code == 201, enrollment.text

    details = admin_client.client.get(f"/api/v1/admin/students/{created['id']}")
    assert details.status_code == 200, details.text
    body = details.json()
    assert body["id"] == created["id"]  # stable Student ID
    assert body["student_code"] == "ST-2026-0001"  # separate roll number
    assert body["full_name"] == "Ada Lovelace"
    assert body["email"] == "ada@campus360.example.com"
    assert body["phone"] == "+91-9876500000"
    assert body["degree_program"] == "B.Tech Computer Science and Engineering (CSE)"
    assert body["current_semester"] == 3
    assert body["is_active"] is True

    enrolled = admin_client.client.get(
        "/api/v1/admin/enrollments",
        params={"student_id": created["id"], "limit": 100},
    )
    assert enrolled.status_code == 200, enrolled.text
    rows = enrolled.json()
    assert len(rows) == 1
    assert rows[0]["student_id"] == created["id"]
    assert rows[0]["course_code"] == "DET-101"
    assert rows[0]["course_title"] == "Details Verification"
    assert rows[0]["status"] == "active"


def test_student_details_optional_fields_are_null_not_invented(admin_client, db_cleaner) -> None:
    """Missing optional values stay null for the details view's empty state."""
    created = _create(
        admin_client,
        email="sparse.details@campus360.example.com",
        student_code="ST-SPARSE-1",
        full_name="Sparse Details",
        degree_program=None,
        current_semester=None,
        phone=None,
    )

    response = admin_client.client.get(f"/api/v1/admin/students/{created['id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == created["id"]
    assert body["phone"] is None
    assert body["degree_program"] is None
    assert body["current_semester"] is None


def test_student_details_endpoints_reject_non_admins(
    admin_client, student_client, db_cleaner
) -> None:
    """A student session cannot read another student's admin details."""
    created = _create(admin_client)

    details = student_client.client.get(f"/api/v1/admin/students/{created['id']}")
    enrollments = student_client.client.get(
        "/api/v1/admin/enrollments",
        params={"student_id": created["id"]},
    )

    assert details.status_code == 403, details.text
    assert enrollments.status_code == 403, enrollments.text


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
    assert body["degree_program"] == "B.Tech Computer Science and Engineering (CSE)"


def test_patch_student_changes_the_roll_number(admin_client, db_cleaner) -> None:
    """Regression: the admin UI edits this field, so PATCH must accept it.

    The roll number is ``student_code`` in the database. Changing it must
    update the SAME row: id, account and related records stay intact.
    """
    created = _create(admin_client)

    response = admin_client.client.patch(
        f"/api/v1/admin/students/{created['id']}",
        json={"student_code": "  ST-2026-0002  "},
        headers=admin_client.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["student_code"] == "ST-2026-0002"  # trimmed, like create
    assert body["id"] == created["id"]
    assert body["user_id"] == created["user_id"]
    assert body["email"] == created["email"]
    assert body["full_name"] == "Ada Lovelace"

    # The directory re-reads the record, so a fresh GET must show the new code.
    reread = admin_client.client.get(
        f"/api/v1/admin/students/{created['id']}", headers=admin_client.headers
    )
    assert reread.status_code == 200, reread.text
    assert reread.json()["student_code"] == "ST-2026-0002"


def test_patch_student_rejects_a_duplicate_roll_number(admin_client, db_cleaner) -> None:
    """Uniqueness is a database rule: a duplicate PATCH is a 409, and the
    rejected value is rolled back."""
    _create(admin_client, student_code="ST-DUP-1")
    second = _create(
        admin_client,
        email="b@campus360.example.com",
        student_code="ST-DUP-2",
        full_name="Bob",
    )

    response = admin_client.client.patch(
        f"/api/v1/admin/students/{second['id']}",
        json={"student_code": "ST-DUP-1"},
        headers=admin_client.headers,
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["field_errors"] == {
        "student_code": "This roll number is already assigned to another student.",
    }

    reread = admin_client.client.get(
        f"/api/v1/admin/students/{second['id']}", headers=admin_client.headers
    )
    assert reread.status_code == 200, reread.text
    assert reread.json()["student_code"] == "ST-DUP-2"


def test_patch_reports_both_duplicate_fields(admin_client, db_cleaner) -> None:
    """A PATCH that duplicates two fields marks both; neither hides the other."""
    first = _create(admin_client, student_code="ST-BOTH-1")
    second = _create(
        admin_client,
        email="both@campus360.example.com",
        student_code="ST-BOTH-2",
        full_name="Both Fields",
    )

    response = admin_client.client.patch(
        f"/api/v1/admin/students/{second['id']}",
        json={"email": first["email"], "student_code": first["student_code"]},
        headers=admin_client.headers,
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["field_errors"] == {
        "email": "This email address is already registered.",
        "student_code": "This roll number is already assigned to another student.",
    }


def test_patch_recovers_from_a_concurrent_unique_conflict(
    admin_client, db_cleaner, monkeypatch
) -> None:
    """A concurrent writer after the PATCH pre-check is mapped after rollback."""
    first = _create(admin_client, student_code="ST-RACE-1")
    second = _create(
        admin_client,
        email="race@campus360.example.com",
        student_code="ST-RACE-2",
        full_name="Racing Update",
    )
    original = admin_service._student_field_conflicts
    calls = {"count": 0}

    def miss_first_precheck(db, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {}
        return original(db, **kwargs)

    monkeypatch.setattr(admin_service, "_student_field_conflicts", miss_first_precheck)

    response = admin_client.client.patch(
        f"/api/v1/admin/students/{second['id']}",
        json={"email": first["email"], "student_code": first["student_code"]},
        headers=admin_client.headers,
    )

    assert response.status_code == 409, response.text
    assert calls["count"] == 2
    assert response.json()["detail"]["field_errors"] == {
        "email": "This email address is already registered.",
        "student_code": "This roll number is already assigned to another student.",
    }

    reread = admin_client.client.get(
        f"/api/v1/admin/students/{second['id']}", headers=admin_client.headers
    )
    assert reread.status_code == 200, reread.text
    assert reread.json()["email"] == "race@campus360.example.com"
    assert reread.json()["student_code"] == "ST-RACE-2"


@pytest.mark.parametrize("blank", ["", "   "])
def test_patch_student_rejects_a_blank_roll_number(admin_client, db_cleaner, blank: str) -> None:
    """The column is NOT NULL: an empty or whitespace-only code is a 422."""
    created = _create(admin_client)

    response = admin_client.client.patch(
        f"/api/v1/admin/students/{created['id']}",
        json={"student_code": blank},
        headers=admin_client.headers,
    )

    assert response.status_code == 422, response.text


def test_patch_student_changes_the_login_email(admin_client, db_cleaner) -> None:
    """The email IS the login identifier, so the linked account must change.

    The profile has no email column of its own. A PATCH therefore updates
    ``users.email`` / ``users.email_normalized`` on the SAME account: the
    student id, user id, roll number and academic rows are untouched, the old
    address stops working, and the new one logs in.
    """
    created = _create(admin_client)

    response = admin_client.client.patch(
        f"/api/v1/admin/students/{created['id']}",
        json={"email": "ada.new@campus360.example.com"},
        headers=admin_client.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == "ada.new@campus360.example.com"
    # Same profile and same login account - no delete/recreate.
    assert body["id"] == created["id"]
    assert body["user_id"] == created["user_id"]
    assert body["student_code"] == created["student_code"]

    db = SessionLocal()
    try:
        user = db.get(User, created["user_id"])
        assert user.email == "ada.new@campus360.example.com"
        assert user.email_normalized == "ada.new@campus360.example.com"
        assert db.get(StudentProfile, created["id"]) is not None
    finally:
        db.close()

    with TestClient(app) as login_client:
        # The OLD login address is gone...
        old = login_client.post(
            "/api/v1/auth/login",
            json={"email": created["email"], "password": "StudentPass123"},
        )
        assert old.status_code == 401, old.text

        # ...and the NEW one authenticates the same account (case-insensitive).
        new = login_client.post(
            "/api/v1/auth/login",
            json={"email": "Ada.New@Campus360.Example.Com", "password": "StudentPass123"},
        )
        assert new.status_code == 200, new.text
        assert new.json()["id"] == created["user_id"]
        assert new.json()["email"] == "ada.new@campus360.example.com"


def test_patch_student_rejects_an_email_used_by_another_account(admin_client, db_cleaner) -> None:
    """Uniqueness is case-insensitive, and a rejected change keeps the old email."""
    _create(admin_client)  # ada@campus360.example.com
    second = _create(
        admin_client,
        email="b@campus360.example.com",
        student_code="ST-EMAIL-2",
        full_name="Bob",
    )

    response = admin_client.client.patch(
        f"/api/v1/admin/students/{second['id']}",
        json={"email": "ADA@CAMPUS360.EXAMPLE.COM"},
        headers=admin_client.headers,
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["field_errors"] == {
        "email": "This email address is already registered.",
    }

    # Rolled back: the second student still owns the original address.
    reread = admin_client.client.get(
        f"/api/v1/admin/students/{second['id']}", headers=admin_client.headers
    )
    assert reread.status_code == 200, reread.text
    assert reread.json()["email"] == "b@campus360.example.com"


@pytest.mark.parametrize("bad_email", [None, "", "not-an-email", "a@", "@b.com"])
def test_patch_student_rejects_an_invalid_email(
    admin_client, db_cleaner, bad_email: str | None
) -> None:
    """Format (and NOT NULL) validation happens at the edge: 422, not a 500."""
    created = _create(admin_client)

    response = admin_client.client.patch(
        f"/api/v1/admin/students/{created['id']}",
        json={"email": bad_email},
        headers=admin_client.headers,
    )

    assert response.status_code == 422, response.text


def test_patch_student_changing_email_revokes_that_students_sessions(
    admin_client, db_cleaner
) -> None:
    """A session issued under the old login must not outlive the email change.

    Sessions are server-side rows keyed by user id, so this revocation is
    immediate: the cookie still exists in the browser but maps to nothing.
    """
    created = _create(admin_client)

    with TestClient(app) as student_http:
        login = student_http.post(
            "/api/v1/auth/login",
            json={"email": created["email"], "password": "StudentPass123"},
        )
        assert login.status_code == 200, login.text
        assert student_http.get("/api/v1/auth/me").status_code == 200

        response = admin_client.client.patch(
            f"/api/v1/admin/students/{created['id']}",
            json={"email": "moved@campus360.example.com"},
            headers=admin_client.headers,
        )
        assert response.status_code == 200, response.text

        # The pre-change session is dead...
        assert student_http.get("/api/v1/auth/me").status_code == 401

        # ...and the student simply signs in again with the new address.
        relogin = student_http.post(
            "/api/v1/auth/login",
            json={"email": "moved@campus360.example.com", "password": "StudentPass123"},
        )
        assert relogin.status_code == 200, relogin.text
        me = student_http.get("/api/v1/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["email"] == "moved@campus360.example.com"


def test_patch_unknown_student_returns_404(admin_client, db_cleaner) -> None:
    """Patching a missing student is a 404."""
    response = admin_client.client.patch(
        "/api/v1/admin/students/99999999",
        json={"phone": "123"},
        headers=admin_client.headers,
    )

    assert response.status_code == 404, response.text


def test_student_profile_id_is_stable_across_edits_and_status_changes(
    admin_client, db_cleaner
) -> None:
    """The existing profile id is the permanent Student ID, not the roll number.

    Name/email/roll/programme/semester edits and deactivate/reactivate keep the
    same profile row, and therefore the same internal identifier.
    """
    created = _create(admin_client)
    original_id = created["id"]
    original_user_id = created["user_id"]

    edited = admin_client.client.patch(
        f"/api/v1/admin/students/{original_id}",
        json={
            "email": "stable.student@campus360.example.com",
            "student_code": "ST-STABLE-9999",
            "full_name": "Stable Student",
            "degree_program": "BTECH-DS",
            "current_semester": 6,
            "phone": "+91-9000011111",
        },
        headers=admin_client.headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["id"] == original_id
    assert edited.json()["user_id"] == original_user_id
    assert edited.json()["student_code"] == "ST-STABLE-9999"

    deactivated = admin_client.client.delete(
        f"/api/v1/admin/students/{original_id}", headers=admin_client.headers
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["id"] == original_id
    assert deactivated.json()["is_active"] is False

    reactivated = admin_client.client.patch(
        f"/api/v1/admin/students/{original_id}",
        json={"is_active": True},
        headers=admin_client.headers,
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["id"] == original_id
    assert reactivated.json()["user_id"] == original_user_id
    assert reactivated.json()["is_active"] is True

    db = SessionLocal()
    try:
        # The profile row still exists under the original primary key.
        assert db.get(StudentProfile, original_id) is not None
    finally:
        db.close()


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


# ---------------------------------------------------------------------------
# Programme catalogue / canonicalization
# ---------------------------------------------------------------------------
CSE_NAME = "B.Tech Computer Science and Engineering (CSE)"


def test_programme_catalogue_is_exposed_to_admins(admin_client) -> None:
    """The dropdown's source of truth is the API, grouped for the UI."""
    response = admin_client.client.get("/api/v1/admin/programmes", headers=admin_client.headers)

    assert response.status_code == 200, response.text
    catalogue = response.json()
    assert len(catalogue) == 18
    by_code = {item["code"]: item for item in catalogue}
    assert by_code["BTECH-CSE"]["name"] == CSE_NAME
    assert by_code["BTECH-CSE"]["group"] == "Engineering"
    assert by_code["BCOM-HONS"]["name"] == "B.Com (Honours)"
    assert {item["group"] for item in catalogue} == {
        "Engineering",
        "Commerce and Management",
        "Computer Applications",
        "Science and Humanities",
    }


def test_create_student_resolves_a_programme_alias(admin_client, db_cleaner) -> None:
    """``cse`` is accepted but the canonical display name is stored."""
    body = _create(admin_client, degree_program="cse")

    assert body["degree_program"] == CSE_NAME
    assert body["programme_code"] == "BTECH-CSE"


def test_create_student_rejects_an_unknown_programme(admin_client, db_cleaner) -> None:
    """A free-text value outside the catalogue cannot be newly assigned."""
    response = admin_client.client.post(
        "/api/v1/admin/students",
        json={
            "email": "unknown-programme@campus360.example.com",
            "password": "StudentPass123",
            "student_code": "ST-BAD-PROG",
            "full_name": "Bad Programme",
            "degree_program": "B.Tech Underwater Basket Weaving",
        },
        headers=admin_client.headers,
    )

    assert response.status_code == 422, response.text
    assert "unknown programme" in response.json()["detail"].lower()


def test_patch_student_accepts_the_stable_programme_code(admin_client, db_cleaner) -> None:
    """The edit form submits a code; the backend stores the canonical name."""
    created = _create(admin_client)

    response = admin_client.client.patch(
        f"/api/v1/admin/students/{created['id']}",
        json={"degree_program": "BTECH-DS"},
        headers=admin_client.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["degree_program"] == "B.Tech Data Science (DS)"
    assert body["programme_code"] == "BTECH-DS"


def test_patch_student_keeps_an_unchanged_legacy_programme(admin_client, db_cleaner) -> None:
    """Legacy free text is grandfathered ONLY when echoed back unchanged.

    The suite writes an uncatalogued value directly (simulating pre-existing
    data) and proves an unrelated edit can still be saved without silently
    rewriting or dropping it.
    """
    created = _create(admin_client)
    db = SessionLocal()
    try:
        profile = db.get(StudentProfile, created["id"])
        assert profile is not None
        profile.degree_program = "B.Tech Cybernetics"
        db.commit()
    finally:
        db.close()

    response = admin_client.client.patch(
        f"/api/v1/admin/students/{created['id']}",
        json={"degree_program": "B.Tech Cybernetics", "phone": "+91-9000000000"},
        headers=admin_client.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["degree_program"] == "B.Tech Cybernetics"
    assert body["programme_code"] is None
    assert body["phone"] == "+91-9000000000"

    # The very same student cannot be switched to a DIFFERENT unknown value.
    rejected = admin_client.client.patch(
        f"/api/v1/admin/students/{created['id']}",
        json={"degree_program": "B.Tech Something Else"},
        headers=admin_client.headers,
    )
    assert rejected.status_code == 422, rejected.text


def test_list_students_filters_legacy_aliases_by_canonical_programme(
    admin_client, db_cleaner
) -> None:
    """Filtering by the canonical programme/code finds a legacy stored alias."""
    created = _create(admin_client)
    db = SessionLocal()
    try:
        profile = db.get(StudentProfile, created["id"])
        assert profile is not None
        profile.degree_program = "B.Tech CSE"
        db.commit()
    finally:
        db.close()

    for selector in (CSE_NAME, "BTECH-CSE", "CSE"):
        response = admin_client.client.get(
            "/api/v1/admin/students", params={"department": selector}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1, selector
        assert body["items"][0]["student_code"] == created["student_code"]


# ---------------------------------------------------------------------------
# Semester policy
# ---------------------------------------------------------------------------
def test_semester_policy_is_one_through_fourteen() -> None:
    """The current programme policy is explicit and easy to change later."""
    assert SEMESTER_POLICY.minimum == 1
    assert SEMESTER_POLICY.maximum == 14
    assert SEMESTER_POLICY.options == tuple(range(1, 15))


@pytest.mark.parametrize("semester", [1, 10, 14])
def test_create_accepts_semester_boundaries(admin_client, db_cleaner, semester: int) -> None:
    """Semester 1, 10 and 14 are inside the current 1-14 policy."""
    body = _create(
        admin_client,
        email=f"semester{semester}@campus360.example.com",
        student_code=f"SEM-{semester:02d}",
        current_semester=semester,
    )

    assert body["current_semester"] == semester


@pytest.mark.parametrize("semester", [0, 15, -1])
def test_create_rejects_out_of_range_semester(admin_client, db_cleaner, semester: int) -> None:
    """Values below 1 or above 14 fail at the API edge with 422."""
    response = admin_client.client.post(
        "/api/v1/admin/students",
        json=_student_payload(
            email=f"rejected{semester}@campus360.example.com",
            student_code=f"SEM-REJECT-{semester}",
            current_semester=semester,
        ),
        headers=admin_client.headers,
    )

    assert response.status_code == 422, response.text
    assert "current_semester" in response.text


@pytest.mark.parametrize("semester", [3.5, "three"])
def test_create_rejects_non_integer_semester(admin_client, db_cleaner, semester: object) -> None:
    """A fractional or textual semester is not an integer."""
    response = admin_client.client.post(
        "/api/v1/admin/students",
        json=_student_payload(
            email="noninteger@campus360.example.com",
            student_code="SEM-NONINT",
            current_semester=semester,
        ),
        headers=admin_client.headers,
    )

    assert response.status_code == 422, response.text
    assert "current_semester" in response.text


def test_update_accepts_and_rejects_semester_bounds(admin_client, db_cleaner) -> None:
    """PATCH uses the same policy as create and never stores rejected values."""
    created = _create(admin_client)

    for semester in (1, 14):
        response = admin_client.client.patch(
            f"/api/v1/admin/students/{created['id']}",
            json={"current_semester": semester},
            headers=admin_client.headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["current_semester"] == semester

    for semester in (0, 15, -1, 3.5):
        response = admin_client.client.patch(
            f"/api/v1/admin/students/{created['id']}",
            json={"current_semester": semester},
            headers=admin_client.headers,
        )
        assert response.status_code == 422, response.text

    reread = admin_client.client.get(f"/api/v1/admin/students/{created['id']}")
    assert reread.status_code == 200, reread.text
    assert reread.json()["current_semester"] == 14


def test_frontend_maps_structured_duplicate_errors_to_fields() -> None:
    """The browser keeps the API's field map instead of parsing a message.

    There is no JavaScript test runner, so this is a source-level contract
    check for the error transport and the per-field rendering hooks.
    """
    api_source = (PROJECT_ROOT / "js" / "api.js").read_text(encoding="utf-8")
    portal_source = (PROJECT_ROOT / "js" / "portal.js").read_text(encoding="utf-8")

    assert "detail.field_errors || detail.errors" in api_source
    assert "error.fieldErrors = parsed.fieldErrors" in api_source
    assert "validationFieldErrors(detail)" in api_source
    assert "const fieldErrors = reason?.fieldErrors || {};" in portal_source
    assert 'data-field-error="${name}"' in portal_source
    assert 'control.setAttribute("aria-invalid", "true")' in portal_source
    assert 'form.addEventListener("input", clearErrorOnEdit)' in portal_source
    assert "submitButton.disabled = true;" in portal_source


def test_frontend_student_details_view_is_read_only_and_uses_the_profile_id() -> None:
    """Pin the read-only Details action, display format, and existing endpoints."""
    portal_source = (PROJECT_ROOT / "js" / "portal.js").read_text(encoding="utf-8")
    html_source = (PROJECT_ROOT / "pages" / "admin" / "students.html").read_text(encoding="utf-8")
    css_source = (PROJECT_ROOT / "css" / "dashboard.css").read_text(encoding="utf-8")

    start = portal_source.index("async function showStudentDetails")
    end = portal_source.index("async function adminList", start)
    details_block = portal_source[start:end]

    assert 'data-action="details"' in portal_source
    assert "View Details" in portal_source
    assert "function formatStudentId" in portal_source
    assert '`STU-${String(id).padStart(7, "0")}`' in portal_source
    assert "api.get(`/admin/students/${item.id}`)" in details_block
    assert "api.get(`/admin/enrollments?student_id=${item.id}&limit=100`)" in details_block
    for label in (
        "Student ID",
        "Roll Number",
        "Full name",
        "Institutional email",
        "Phone number",
        "Degree programme",
        "Current semester",
        "Account status",
        "Enrolled courses",
    ):
        assert label in details_block
    for editable_control in ("<input", "<select", "<textarea"):
        assert editable_control not in details_block

    assert "<th>Roll Number</th>" in html_source
    assert 'class="data-table student-directory-table"' in html_source
    assert '"Roll Number"' in portal_source
    assert "overflow-x: auto" in css_source
    assert ".student-directory-table" in css_source
    assert "min-width: 860px" in css_source
    assert "font-weight: 600" in css_source


def test_frontend_create_and_edit_share_semester_options() -> None:
    """The single student form definition must match the backend policy.

    There is no JavaScript test runner in this project, so this is a
    source-level contract check: both the create and edit dialogs render the
    same ``adminForms.students`` entry, and its options come from the named
    SEMESTER_MIN/MAX constants.
    """
    source = (PROJECT_ROOT / "js" / "portal.js").read_text(encoding="utf-8")

    assert f"const SEMESTER_MIN = {SEMESTER_POLICY.minimum};" in source
    assert f"const SEMESTER_MAX = {SEMESTER_POLICY.maximum};" in source
    assert "const SEMESTER_OPTIONS = Array.from(" in source
    assert "{ length: SEMESTER_MAX - SEMESTER_MIN + 1 }" in source
    assert '["current_semester", "Current semester", "semester-select"]' in source
    assert 'type === "semester-select"' in source
    assert "const fields = adminForms[resource].filter" in source
    assert "function renderSemesterSelect" in source
    assert "SEMESTER_OPTIONS.map" in source
    assert "renderSemesterSelect(name, value)" in source
    assert "String(value) === String(semester)" in source


# ---------------------------------------------------------------------------
# Student Directory ordering
# ---------------------------------------------------------------------------
def test_directory_sorts_numeric_roll_numbers_ascending(admin_client, db_cleaner) -> None:
    """Numeric roll numbers compare numerically, not lexicographically."""
    _create(
        admin_client,
        email="numeric-two@campus360.example.com",
        student_code="2026002",
        full_name="Numeric Two",
    )
    _create(
        admin_client,
        email="numeric-one@campus360.example.com",
        student_code="2026001",
        full_name="Numeric One",
    )

    response = admin_client.client.get(
        "/api/v1/admin/students", params={"search": "202600", "limit": 100}
    )
    assert response.status_code == 200, response.text
    codes = [item["student_code"] for item in response.json()["items"]]
    assert codes == ["2026001", "2026002"]


def test_directory_order_survives_pagination_and_export(admin_client, db_cleaner) -> None:
    """Page concatenation (the CSV export path) keeps the same order."""
    for code in ("2026004", "2026002", "2026001", "2026003"):
        _create(
            admin_client,
            email=f"page-{code}@campus360.example.com",
            student_code=code,
            full_name=f"Page Student {code}",
        )

    pages = []
    for offset in (0, 2):
        response = admin_client.client.get(
            "/api/v1/admin/students",
            params={"search": "202600", "limit": 2, "offset": offset},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 4
        pages.append([item["student_code"] for item in body["items"]])

    assert pages == [["2026001", "2026002"], ["2026003", "2026004"]]
    # The CSV export calls this same endpoint page by page and appends items.
    assert [code for page in pages for code in page] == [
        "2026001",
        "2026002",
        "2026003",
        "2026004",
    ]


def test_directory_order_is_preserved_by_programme_filter(admin_client, db_cleaner) -> None:
    """Filtering narrows the same deterministic ordering."""
    for code, programme in (
        ("2026202", "B.Tech CSE"),
        ("2026201", "B.Tech CSE"),
        ("2026200", "B.Tech ECE"),
    ):
        _create(
            admin_client,
            email=f"filter-{code}@campus360.example.com",
            student_code=code,
            full_name=f"Filter Student {code}",
            degree_program=programme,
        )

    response = admin_client.client.get(
        "/api/v1/admin/students",
        params={"search": "202620", "department": "BTECH-CSE", "limit": 100},
    )
    assert response.status_code == 200, response.text
    codes = [item["student_code"] for item in response.json()["items"]]
    assert codes == ["2026201", "2026202"]


def test_directory_defines_mixed_roll_number_order(admin_client, db_cleaner) -> None:
    """Pin the documented behavior for zeros, letters and case differences.

    Numeric-only codes sort numerically first, with the text form breaking
    numeric ties (``0001`` before ``1``). Alphanumeric codes follow in
    case-insensitive lexicographic order (``B-10`` before ``b-2``); codes that
    differ only by case fall back to the stable profile id.
    """
    for index, code in enumerate(("b-2", "0002", "A-1", "1", "B-10", "0001", "2", "a-1")):
        _create(
            admin_client,
            email=f"ordering-{index}@campus360.example.com",
            student_code=code,
            full_name=f"Ordering Case {index}",
        )

    response = admin_client.client.get(
        "/api/v1/admin/students", params={"search": "Ordering Case", "limit": 100}
    )
    assert response.status_code == 200, response.text
    codes = [item["student_code"] for item in response.json()["items"]]
    assert codes == ["0001", "1", "0002", "2", "A-1", "a-1", "B-10", "b-2"]
