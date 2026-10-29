"""
Regression tests for defects found during the 2026-10 codebase audit
(backend/tests/test_audit_regressions.py).

Each test reproduces one confirmed defect and pins the corrected behaviour:

* activation links must point at the page that actually serves the form;
* the import CONFIRM step must re-validate every row server-side (a client can
  send anything to that endpoint, not only rows the preview accepted);
* import rows must use the same email rules as login, otherwise an imported
  account can never sign in;
* the import upload enforces the configured size limit;
* faculty can only record marks/attendance for students ENROLLED in the course;
* the "open sessions" counter ignores expired sessions;
* a duplicate faculty email/code reports which field collided;
* the resend-activation endpoint revokes the previous link;
* repeated failed logins for one account are throttled.
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.api import auth as auth_api
from app.core.config import get_settings
from app.core.email import EmailSendResult
from app.core.security import hash_token
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    ActivationToken,
    StudentProfile,
    TimetableEntry,
    User,
    UserRole,
    UserSession,
)
from app.services import activation as activation_service
from fastapi.testclient import TestClient


def _faculty_profile_id(faculty_user_id: int) -> int:
    db = SessionLocal()
    try:
        user = db.get(User, faculty_user_id)
        assert user is not None and user.faculty_profile is not None
        return user.faculty_profile.id
    finally:
        db.close()


def _csv(rows: list[str]) -> str:
    return "email,student_code,full_name,degree_program,current_semester,phone\n" + "\n".join(rows)


# ---------------------------------------------------------------------------
# Activation link
# ---------------------------------------------------------------------------
def test_activation_link_points_at_the_served_activation_page(monkeypatch) -> None:
    """The emailed link must open pages/activate.html, not a 404 route."""
    captured: dict[str, str] = {}

    class _Sender:
        def send_activation_email(self, *, to_email, activation_url, student_name):
            captured["url"] = activation_url
            return EmailSendResult(sent=True, provider="test")

    monkeypatch.setattr(activation_service, "build_email_sender", lambda: _Sender())
    user = User(email="link@campus360.example.com")
    activation_service.queue_activation_email(
        None, user=user, raw_token="abc123", student_name="Link Test"
    )

    url = captured["url"]
    base = get_settings().activation_link_base_url.rstrip("/")
    assert url == f"{base}/pages/activate.html?token=abc123"

    path = url[len(base) :].split("?")[0]
    with TestClient(app) as client:
        assert client.get(path).status_code == 200


def test_activation_link_url_encodes_the_token(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class _Sender:
        def send_activation_email(self, *, to_email, activation_url, student_name):
            captured["url"] = activation_url
            return EmailSendResult(sent=True, provider="test")

    monkeypatch.setattr(activation_service, "build_email_sender", lambda: _Sender())
    activation_service.queue_activation_email(
        None, user=User(email="x@campus360.example.com"), raw_token="a+b/c=", student_name="X"
    )
    assert captured["url"].endswith("?token=a%2Bb%2Fc%3D")


# ---------------------------------------------------------------------------
# Import validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "email",
    ["a@b", "two words@campus360.example.com", "a@@campus360.example.com", "x@reserved.test"],
)
def test_import_preview_rejects_emails_that_could_never_log_in(
    admin_client, db_cleaner, email
) -> None:
    response = admin_client.client.post(
        "/api/v1/admin/students/import",
        json={"csv_content": _csv([f"{email},IMP-1,Ada Lovelace,,,"])},
        headers=admin_client.headers,
    )
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["status"] == "failed"
    assert any("email" in error.lower() for error in row["errors"])


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"email": "not-an-email"}, "email"),
        ({"current_semester": 99}, "semester"),
        ({"degree_program": "Underwater Basket Weaving"}, "programme"),
        ({"student_code": "   "}, "roll number"),
    ],
)
def test_import_confirm_revalidates_rows_server_side(
    admin_client, db_cleaner, override, fragment
) -> None:
    """Rows sent straight to /confirm are validated like the preview does."""
    row = {
        "row_number": 2,
        "email": "confirm-bypass@campus360.example.com",
        "student_code": "IMP-BYPASS-1",
        "full_name": "Bypass Attempt",
        "degree_program": None,
        "current_semester": None,
        "phone": None,
    }
    row.update(override)
    response = admin_client.client.post(
        "/api/v1/admin/students/import/confirm",
        json={"rows": [row]},
        headers=admin_client.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 0
    assert body["failed"] == 1
    result = body["rows"][0]
    assert result["status"] == "failed"
    assert any(fragment in error.lower() for error in result["errors"]), result["errors"]
    # No database internals may leak into the per-row errors.
    assert not any("psycopg" in error or "SQL" in error for error in result["errors"])

    db = SessionLocal()
    try:
        assert (
            db.query(User)
            .filter(User.email_normalized == str(row["email"]).strip().lower())
            .one_or_none()
            is None
        )
    finally:
        db.close()


def test_faculty_import_confirm_revalidates_rows_server_side(admin_client, db_cleaner) -> None:
    response = admin_client.client.post(
        "/api/v1/admin/faculty/import/confirm",
        json={
            "rows": [
                {
                    "row_number": 2,
                    "email": "bad email@campus360.example.com",
                    "faculty_code": "FAC-BYPASS-1",
                    "full_name": "Bypass Attempt",
                    "department": "Not A Department",
                }
            ]
        },
        headers=admin_client.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 0 and body["failed"] == 1
    errors = " ".join(body["rows"][0]["errors"]).lower()
    assert "email" in errors and "department" in errors


def test_import_upload_enforces_the_configured_size_limit(admin_client, db_cleaner) -> None:
    limit = get_settings().import_max_file_mb * 1024 * 1024
    oversized = _csv(["a@campus360.example.com,IMP-1,Ada,,,"]) + ("x" * limit)
    for path in ("/api/v1/admin/students/import", "/api/v1/admin/faculty/import"):
        response = admin_client.client.post(
            path, json={"csv_content": oversized}, headers=admin_client.headers
        )
        assert response.status_code == 413, response.text


# ---------------------------------------------------------------------------
# Faculty: enrollment is required for marks and attendance
# ---------------------------------------------------------------------------
def _course_with_unenrolled_student(admin_client, faculty_client) -> tuple[dict, dict]:
    course = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "AUD-101",
            "title": "Audit Course",
            "credits": 3,
            "lead_faculty_id": _faculty_profile_id(faculty_client.user["id"]),
        },
        headers=admin_client.headers,
    )
    assert course.status_code == 201, course.text
    student = admin_client.client.post(
        "/api/v1/admin/students",
        json={
            "email": "unenrolled@campus360.example.com",
            "password": "StudentPass123",
            "student_code": "AUD-ST-1",
            "full_name": "Not Enrolled",
        },
        headers=admin_client.headers,
    )
    assert student.status_code == 201, student.text
    return course.json(), student.json()


def test_faculty_cannot_record_a_mark_for_an_unenrolled_student(
    admin_client, faculty_client, db_cleaner
) -> None:
    course, student = _course_with_unenrolled_student(admin_client, faculty_client)
    response = faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student["id"],
            "course_id": course["id"],
            "component": "internal",
            "score": 10,
            "max_score": 20,
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text
    assert "not enrolled" in response.json()["detail"].lower()


def test_faculty_cannot_mark_attendance_for_an_unenrolled_student(
    admin_client, faculty_client, db_cleaner
) -> None:
    course, student = _course_with_unenrolled_student(admin_client, faculty_client)
    db = SessionLocal()
    try:
        entry = TimetableEntry(
            course_id=course["id"], day_of_week="Monday", start_time="09:00", end_time="10:00"
        )
        db.add(entry)
        db.commit()
        entry_id = entry.id
    finally:
        db.close()

    response = faculty_client.client.post(
        "/api/v1/faculty/attendance/batch",
        json={
            "timetable_entry_id": entry_id,
            "attendance_date": "2026-10-19",
            "records": [{"student_id": student["id"], "status": "present"}],
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text
    assert "not enrolled" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Reports: open sessions
# ---------------------------------------------------------------------------
def test_open_session_count_ignores_expired_sessions(admin_client, db_cleaner) -> None:
    before = admin_client.client.get("/api/v1/admin/reports/summary").json()["active_sessions"]
    db = SessionLocal()
    try:
        db.add(
            UserSession(
                user_id=admin_client.user["id"],
                token_hash=hash_token("expired-audit-token"),
                csrf_token_hash=hash_token("expired-audit-csrf"),
                expires_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        db.commit()
    finally:
        db.close()
    after = admin_client.client.get("/api/v1/admin/reports/summary").json()["active_sessions"]
    assert after == before


# ---------------------------------------------------------------------------
# Faculty create: field-specific duplicate errors
# ---------------------------------------------------------------------------
def test_duplicate_faculty_code_reports_the_field(admin_client, db_cleaner) -> None:
    payload = {
        "email": "dup-fac-1@campus360.example.com",
        "password": "FacultyPass123",
        "faculty_code": "FAC-DUP-1",
        "full_name": "First Person",
    }
    first = admin_client.client.post(
        "/api/v1/admin/faculty", json=payload, headers=admin_client.headers
    )
    assert first.status_code == 201, first.text
    payload["email"] = "dup-fac-2@campus360.example.com"
    second = admin_client.client.post(
        "/api/v1/admin/faculty", json=payload, headers=admin_client.headers
    )
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert set(detail["field_errors"]) == {"faculty_code"}


# ---------------------------------------------------------------------------
# Resend activation
# ---------------------------------------------------------------------------
def test_resend_activation_revokes_the_previous_link(admin_client, db_cleaner) -> None:
    preview = admin_client.client.post(
        "/api/v1/admin/students/import/confirm",
        json={
            "rows": [
                {
                    "row_number": 2,
                    "email": "resend@campus360.example.com",
                    "student_code": "RESEND-1",
                    "full_name": "Resend Me",
                }
            ]
        },
        headers=admin_client.headers,
    )
    assert preview.status_code == 200 and preview.json()["created"] == 1, preview.text
    profile_id = preview.json()["rows"][0]["profile_id"]

    response = admin_client.client.post(
        f"/api/v1/admin/students/{profile_id}/resend-activation",
        headers=admin_client.headers,
    )
    assert response.status_code == 200, response.text

    db = SessionLocal()
    try:
        profile = db.get(StudentProfile, profile_id)
        tokens = db.query(ActivationToken).filter(ActivationToken.user_id == profile.user_id).all()
        assert len(tokens) == 2
        assert sum(1 for token in tokens if token.consumed_at is None) == 1
    finally:
        db.close()


def test_resend_activation_for_unknown_student_is_404(admin_client, db_cleaner) -> None:
    response = admin_client.client.post(
        "/api/v1/admin/students/999999999/resend-activation", headers=admin_client.headers
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Login throttling
# ---------------------------------------------------------------------------
def test_repeated_failed_logins_are_throttled(make_user) -> None:
    auth_api.reset_login_throttle()
    make_user("throttle@campus360.example.com", "RightPassword123", UserRole.STUDENT)
    try:
        with TestClient(app) as client:
            for _ in range(auth_api.LOGIN_MAX_FAILURES):
                response = client.post(
                    "/api/v1/auth/login",
                    json={"email": "throttle@campus360.example.com", "password": "wrong-password"},
                )
                assert response.status_code == 401
            blocked = client.post(
                "/api/v1/auth/login",
                json={"email": "throttle@campus360.example.com", "password": "RightPassword123"},
            )
            assert blocked.status_code == 429
            # Another account from the same client is not locked out.
            other = client.post(
                "/api/v1/auth/login",
                json={"email": "someone-else@campus360.example.com", "password": "whatever-1"},
            )
            assert other.status_code == 401
    finally:
        auth_api.reset_login_throttle()


def test_successful_login_clears_the_failure_counter(make_user) -> None:
    auth_api.reset_login_throttle()
    make_user("throttle2@campus360.example.com", "RightPassword123", UserRole.STUDENT)
    try:
        with TestClient(app) as client:
            for _ in range(auth_api.LOGIN_MAX_FAILURES - 1):
                client.post(
                    "/api/v1/auth/login",
                    json={"email": "throttle2@campus360.example.com", "password": "wrong-pass"},
                )
            ok = client.post(
                "/api/v1/auth/login",
                json={"email": "throttle2@campus360.example.com", "password": "RightPassword123"},
            )
            assert ok.status_code == 200
            again = client.post(
                "/api/v1/auth/login",
                json={"email": "throttle2@campus360.example.com", "password": "wrong-pass"},
            )
            assert again.status_code == 401
    finally:
        auth_api.reset_login_throttle()


def test_activation_attempts_are_throttled_per_client() -> None:
    with TestClient(app) as client:
        for _ in range(auth_api.ACTIVATION_MAX_FAILURES):
            response = client.post(
                "/api/v1/auth/activate",
                json={"token": "not-a-real-token", "password": "LongEnough123"},
            )
            assert response.status_code == 400
        blocked = client.post(
            "/api/v1/auth/activate",
            json={"token": "not-a-real-token", "password": "LongEnough123"},
        )
        assert blocked.status_code == 429


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------
def test_import_with_an_oversized_csv_field_is_a_file_error_not_a_500(
    admin_client, db_cleaner
) -> None:
    content = _csv([f"a@campus360.example.com,IMP-1,{'x' * 200_000},,,"])
    response = admin_client.client.post(
        "/api/v1/admin/students/import",
        json={"csv_content": content},
        headers=admin_client.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["file_error"]


def test_attendance_batch_rejects_the_same_student_twice(faculty_client) -> None:
    response = faculty_client.client.post(
        "/api/v1/faculty/attendance/batch",
        json={
            "timetable_entry_id": 1,
            "attendance_date": "2026-10-19",
            "records": [
                {"student_id": 7, "status": "present"},
                {"student_id": 7, "status": "absent"},
            ],
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 422


def test_student_directory_query_count_does_not_grow_with_page_size(
    admin_client, db_cleaner
) -> None:
    """Listing 1 or 6 students issues the same number of SQL statements."""
    from app.db.session import engine
    from sqlalchemy import event

    for index in range(6):
        created = admin_client.client.post(
            "/api/v1/admin/students",
            json={
                "email": f"n-plus-one-{index}@campus360.example.com",
                "password": "StudentPass123",
                "student_code": f"NPO-{index}",
                "full_name": f"Query Count {index}",
            },
            headers=admin_client.headers,
        )
        assert created.status_code == 201, created.text

    def count_queries(limit: int) -> int:
        statements: list[str] = []

        def _record(conn, cursor, statement, *args):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        try:
            response = admin_client.client.get(
                f"/api/v1/admin/students?search=query count&limit={limit}"
            )
            assert response.status_code == 200
            assert len(response.json()["items"]) == limit
        finally:
            event.remove(engine, "before_cursor_execute", _record)
        return len(statements)

    assert count_queries(1) == count_queries(6)
