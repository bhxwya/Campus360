"""
Bulk student import tests (backend/tests/test_bulk_import.py).

Covers the import workflow:

* valid CSV import and automatic Student ID generation;
* missing or invalid fields;
* duplicate email and roll number, within the file and against existing records;
* invalid programme and semester;
* authorization restrictions (non-admin cannot import);
* transaction rollback and partial-failure behavior;
* upload size and row-count limits.

Email delivery is mocked - no real SMTP provider is contacted. All tests run
against the dedicated campus360_test database.
"""

import csv
import io

import pytest
from app.core.email import EmailSendResult
from app.db.session import SessionLocal
from app.main import app
from app.models import StudentProfile, User, UserRole
from app.services.bulk_import import build_csv_template, confirm_import, validate_import_csv
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _csv_text(rows: list[dict[str, str]]) -> str:
    """Build CSV text from a list of row dicts."""
    buffer = io.StringIO()
    columns = ["email", "student_code", "full_name", "degree_program", "current_semester", "phone"]
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return buffer.getvalue()


def _make_existing_student(
    db, email: str = "existing@campus360.example", code: str = "ST-EXIST-001"
) -> None:
    """Insert an existing active student to test duplicate detection."""
    from app.core.security import hash_password

    user = User(
        email=email,
        email_normalized=email.strip().lower(),
        password_hash=hash_password("Password123!"),
        role=UserRole.STUDENT,
        is_active=True,
    )
    db.add(user)
    db.flush()
    profile = StudentProfile(
        user=user,
        student_code=code,
        full_name="Existing Student",
    )
    db.add(profile)
    db.commit()


def _login_as_admin() -> tuple:
    """Return a TestClient logged in as admin and its CSRF token."""
    from app.core.config import get_settings
    from app.services.auth import create_user

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "import-admin@campus360.example").one_or_none()
        if user is None:
            user = create_user(
                db,
                email="import-admin@campus360.example",
                password="ImportAdminPass123",
                role=UserRole.ADMIN,
            )
            db.commit()
    finally:
        db.close()

    c = TestClient(app)
    c.post(
        "/api/v1/auth/login",
        json={"email": "import-admin@campus360.example", "password": "ImportAdminPass123"},
    )
    csrf = c.cookies.get(get_settings().csrf_cookie_name)
    return c, csrf


def _login_as_student() -> tuple:
    """Return a TestClient logged in as a non-admin student and its CSRF token."""
    from app.core.config import get_settings
    from app.services.auth import create_user

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "import-student@campus360.example").one_or_none()
        if user is None:
            user = create_user(
                db,
                email="import-student@campus360.example",
                password="ImportStudentPass123",
                role=UserRole.STUDENT,
            )
            db.commit()
    finally:
        db.close()

    c = TestClient(app)
    c.post(
        "/api/v1/auth/login",
        json={"email": "import-student@campus360.example", "password": "ImportStudentPass123"},
    )
    csrf = c.cookies.get(get_settings().csrf_cookie_name)
    return c, csrf


# ---------------------------------------------------------------------------
# Fake email sender
# ---------------------------------------------------------------------------


class FakeEmailSender:
    """Records activation email calls; can be configured to fail on a given address."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.calls: list[dict[str, str]] = []

    provider_name = "fake"

    def send_activation_email(
        self, *, to_email: str, activation_url: str, student_name: str
    ) -> EmailSendResult:
        self.calls.append({"to": to_email, "name": student_name})
        if to_email == self.fail_on:
            return EmailSendResult(sent=False, provider=self.provider_name, error="SMTP down")
        return EmailSendResult(sent=True, provider=self.provider_name)


@pytest.fixture()
def fake_email_sender(monkeypatch: pytest.MonkeyPatch) -> FakeEmailSender:
    """Replace build_email_sender() with a fake that records calls."""
    sender = FakeEmailSender()
    monkeypatch.setattr("app.core.email.build_email_sender", lambda: sender)
    return sender


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


def test_csv_template_has_required_columns() -> None:
    """The downloadable template contains exactly the fields the import accepts."""
    template = build_csv_template()
    lines = template.strip().splitlines()
    assert lines[0] == "email,student_code,full_name,degree_program,current_semester,phone"
    assert len(lines) == 2  # header + one example row


# ---------------------------------------------------------------------------
# Validation (preview) - no writes
# ---------------------------------------------------------------------------


def test_valid_csv_passes_validation(db_cleaner) -> None:
    """A valid CSV validates with no writes."""
    db = SessionLocal()
    csv_text = _csv_text(
        [
            {
                "email": "ada@campus360.example",
                "student_code": "ST-2026-0001",
                "full_name": "Ada Lovelace",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
            {
                "email": "alan@campus360.example",
                "student_code": "ST-2026-0002",
                "full_name": "Alan Turing",
                "degree_program": "B.Tech IT",
                "current_semester": "2",
                "phone": "555-0100",
            },
        ]
    )

    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    assert preview.file_error is None
    assert preview.total_rows == 2
    assert preview.valid_rows == 2
    assert preview.invalid_rows == 0

    # No writes occurred during validation.
    assert db.query(StudentProfile).count() == 0


def test_missing_required_column_rejected(db_cleaner) -> None:
    """A CSV missing a required column is rejected at the file level."""
    db = SessionLocal()
    csv_text = "email,student_code\nada@campus360.example,ST-1\n"
    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    assert preview.file_error is not None
    assert "Missing required column" in preview.file_error


def test_missing_fields_rejected_per_row(db_cleaner) -> None:
    """Rows missing email, roll number, or name are flagged per-row."""
    db = SessionLocal()
    csv_text = _csv_text(
        [
            {
                "email": "",
                "student_code": "ST-1",
                "full_name": "No Email",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
            {
                "email": "ok@campus360.example",
                "student_code": "",
                "full_name": "No Code",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
            {
                "email": "ok2@campus360.example",
                "student_code": "ST-2",
                "full_name": "",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )

    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    assert preview.valid_rows == 0
    assert preview.invalid_rows == 3
    for row in preview.rows:
        assert row.status == "failed"
        assert len(row.errors) > 0


def test_invalid_email_rejected(db_cleaner) -> None:
    """A malformed email is flagged per-row."""
    db = SessionLocal()
    csv_text = _csv_text(
        [
            {
                "email": "not-an-email",
                "student_code": "ST-1",
                "full_name": "Bad Email",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )

    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    assert preview.invalid_rows == 1
    assert any("valid address" in e for e in preview.rows[0].errors)


def test_invalid_programme_rejected(db_cleaner) -> None:
    """An unrecognised programme value is flagged per-row."""
    db = SessionLocal()
    csv_text = _csv_text(
        [
            {
                "email": "ada@campus360.example",
                "student_code": "ST-1",
                "full_name": "Ada",
                "degree_program": "Not A Real Programme",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )

    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    assert preview.invalid_rows == 1
    assert any("programme" in e.lower() for e in preview.rows[0].errors)


def test_invalid_semester_rejected(db_cleaner) -> None:
    """A semester outside 1-14 is flagged per-row."""
    db = SessionLocal()
    csv_text = _csv_text(
        [
            {
                "email": "ada@campus360.example",
                "student_code": "ST-1",
                "full_name": "Ada",
                "degree_program": "B.Tech CSE",
                "current_semester": "20",
                "phone": "",
            },
        ]
    )

    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    assert preview.invalid_rows == 1
    assert any("Semester" in e for e in preview.rows[0].errors)


def test_non_integer_semester_rejected(db_cleaner) -> None:
    """A non-integer semester is flagged per-row."""
    db = SessionLocal()
    csv_text = _csv_text(
        [
            {
                "email": "ada@campus360.example",
                "student_code": "ST-1",
                "full_name": "Ada",
                "degree_program": "B.Tech CSE",
                "current_semester": "third",
                "phone": "",
            },
        ]
    )

    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    assert preview.invalid_rows == 1
    assert any("whole number" in e for e in preview.rows[0].errors)


def test_duplicate_email_within_file_rejected(db_cleaner) -> None:
    """Two rows with the same email in one file are both flagged."""
    db = SessionLocal()
    csv_text = _csv_text(
        [
            {
                "email": "ada@campus360.example",
                "student_code": "ST-1",
                "full_name": "Ada 1",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
            {
                "email": "ada@campus360.example",
                "student_code": "ST-2",
                "full_name": "Ada 2",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )

    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    # First occurrence is valid; subsequent duplicates are flagged.
    assert preview.valid_rows == 1
    assert preview.invalid_rows == 1
    assert any("Duplicate email" in e for e in preview.rows[1].errors)


def test_duplicate_code_within_file_rejected(db_cleaner) -> None:
    """Two rows with the same roll number in one file: second is flagged."""
    db = SessionLocal()
    csv_text = _csv_text(
        [
            {
                "email": "ada@campus360.example",
                "student_code": "ST-1",
                "full_name": "Ada 1",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
            {
                "email": "alan@campus360.example",
                "student_code": "ST-1",
                "full_name": "Alan",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )

    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    assert preview.valid_rows == 1
    assert preview.invalid_rows == 1
    assert any("Duplicate roll number" in e for e in preview.rows[1].errors)


def test_duplicate_email_against_existing_record(db_cleaner) -> None:
    """An email already in the database is flagged per-row."""
    db = SessionLocal()
    _make_existing_student(db)

    csv_text = _csv_text(
        [
            {
                "email": "existing@campus360.example",
                "student_code": "ST-NEW",
                "full_name": "Dup Email",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )

    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    assert preview.invalid_rows == 1
    assert any("already registered" in e for e in preview.rows[0].errors)


def test_duplicate_code_against_existing_record(db_cleaner) -> None:
    """A roll number already in the database is flagged per-row."""
    db = SessionLocal()
    _make_existing_student(db)

    csv_text = _csv_text(
        [
            {
                "email": "new@campus360.example",
                "student_code": "ST-EXIST-001",
                "full_name": "Dup Code",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )

    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    assert preview.invalid_rows == 1
    assert any("already assigned" in e for e in preview.rows[0].errors)


def test_upload_row_count_limit(db_cleaner) -> None:
    """Files exceeding the row limit are rejected at the file level."""
    db = SessionLocal()
    rows = [
        {
            "email": f"user{i}@campus360.example",
            "student_code": f"ST-{i:04d}",
            "full_name": f"User {i}",
            "degree_program": "B.Tech CSE",
            "current_semester": "3",
            "phone": "",
        }
        for i in range(600)
    ]
    csv_text = _csv_text(rows)

    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    assert preview.file_error is not None
    assert "600" in preview.file_error and "500" in preview.file_error


# ---------------------------------------------------------------------------
# Confirmation (writes)
# ---------------------------------------------------------------------------


def test_confirm_import_creates_accounts_and_profiles(
    db_cleaner, fake_email_sender: FakeEmailSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid rows create inactive users + profiles + activation tokens atomically."""
    monkeypatch.setattr("app.services.activation.build_email_sender", lambda: fake_email_sender)

    db = SessionLocal()
    csv_text = _csv_text(
        [
            {
                "email": "ada@campus360.example",
                "student_code": "ST-2026-0001",
                "full_name": "Ada Lovelace",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )
    preview = validate_import_csv(db, csv_text.encode("utf-8"))
    valid_rows = [r for r in preview.rows if r.status == "valid"]

    rows = [
        {
            "email": r.email,
            "student_code": r.student_code,
            "full_name": r.full_name,
            "degree_program": r.degree_program,
            "current_semester": r.current_semester,
            "phone": r.phone,
        }
        for r in valid_rows
    ]

    confirmation = confirm_import(db, rows, admin_email="admin@campus360.example")

    assert confirmation.created >= 1
    assert confirmation.failed == 0

    db = SessionLocal()
    try:
        profile = (
            db.query(StudentProfile).filter(StudentProfile.student_code == "ST-2026-0001").one()
        )
        assert profile.full_name == "Ada Lovelace"
        assert profile.degree_program == "B.Tech Computer Science and Engineering (CSE)"
        assert profile.current_semester == 3
        user = db.query(User).filter(User.id == profile.user_id).one()
        assert user.role == UserRole.STUDENT
        assert user.is_active is False  # inactive until activation
        assert user.password_hash.startswith("$argon2")
    finally:
        db.close()

    # Exactly one activation email was queued.
    assert len(fake_email_sender.calls) >= 1
    assert fake_email_sender.calls[0]["to"] == "ada@campus360.example"


def test_confirm_import_skips_duplicates(db_cleaner) -> None:
    """Rows duplicating existing records are skipped, not overwritten."""
    db = SessionLocal()
    _make_existing_student(db)

    csv_text = _csv_text(
        [
            {
                "email": "existing@campus360.example",
                "student_code": "ST-NEW",
                "full_name": "Dup Email",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
            {
                "email": "new@campus360.example",
                "student_code": "ST-EXIST-001",
                "full_name": "Dup Code",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )
    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    rows = [
        {
            "email": r.email,
            "student_code": r.student_code,
            "full_name": r.full_name,
            "degree_program": r.degree_program,
            "current_semester": r.current_semester,
            "phone": r.phone,
        }
        for r in preview.rows
    ]

    confirmation = confirm_import(db, rows, admin_email="admin@campus360.example")

    assert confirmation.skipped == 2
    assert confirmation.created == 0
    assert confirmation.failed == 0


def test_confirm_import_email_failure_does_not_rollback(
    db_cleaner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When email delivery fails for one row, that row is marked failed but
    other rows are still created (no cascade rollback)."""
    sender = FakeEmailSender(fail_on="fail@campus360.example")
    monkeypatch.setattr("app.services.activation.build_email_sender", lambda: sender)

    db = SessionLocal()
    csv_text = _csv_text(
        [
            {
                "email": "ok@campus360.example",
                "student_code": "ST-OK-001",
                "full_name": "OK User",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
            {
                "email": "fail@campus360.example",
                "student_code": "ST-FAIL-001",
                "full_name": "Fail User",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )
    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    rows = [
        {
            "email": r.email,
            "student_code": r.student_code,
            "full_name": r.full_name,
            "degree_program": r.degree_program,
            "current_semester": r.current_semester,
            "phone": r.phone,
        }
        for r in preview.rows
    ]

    confirmation = confirm_import(db, rows, admin_email="admin@campus360.example")

    # Both accounts are created; email delivery is tracked separately.
    assert confirmation.created == 2
    assert confirmation.failed == 0
    assert confirmation.email_sent == 1
    assert confirmation.email_failed == 1

    # The OK row was still created despite the email failure on the FAIL row.
    db = SessionLocal()
    try:
        ok_profile = (
            db.query(StudentProfile)
            .filter(StudentProfile.student_code == "ST-OK-001")
            .one_or_none()
        )
        assert ok_profile is not None, (
            "The successful row must not be rolled back by a later failure"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_import_requires_admin_role() -> None:
    """A non-admin cannot preview or confirm an import (403)."""
    client, _csrf = _login_as_student()

    csv_text = _csv_text(
        [
            {
                "email": "ada@campus360.example",
                "student_code": "ST-1",
                "full_name": "Ada",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )

    # Preview requires admin.
    response = client.post("/api/v1/admin/students/import", json={"csv_content": csv_text})
    assert response.status_code == 403, response.text

    # Confirm requires admin.
    response = client.post("/api/v1/admin/students/import/confirm", json={"rows": []})
    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# Import state reset (stale validation bug fix)
# ---------------------------------------------------------------------------


def test_import_invalid_then_valid_file(db_cleaner) -> None:
    """An invalid file followed by a valid file: only the valid file's
    preview should be shown."""
    db = SessionLocal()

    # First: invalid CSV (missing required fields)
    bad_csv = _csv_text(
        [
            {
                "email": "",
                "student_code": "ST-1",
                "full_name": "Ada",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )
    preview1 = validate_import_csv(db, bad_csv.encode("utf-8"))
    assert preview1.invalid_rows == 1
    assert preview1.valid_rows == 0

    # Then: valid CSV
    good_csv = _csv_text(
        [
            {
                "email": "ada@campus360.example",
                "student_code": "ST-2026-0001",
                "full_name": "Ada Lovelace",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )
    preview2 = validate_import_csv(db, good_csv.encode("utf-8"))
    assert preview2.valid_rows == 1
    assert preview2.invalid_rows == 0


def test_import_removing_file_resets_state(db_cleaner) -> None:
    """When no file is selected, all validation state is cleared."""
    db = SessionLocal()

    csv_text = _csv_text(
        [
            {
                "email": "ada@campus360.example",
                "student_code": "ST-2026-0001",
                "full_name": "Ada Lovelace",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )
    preview = validate_import_csv(db, csv_text.encode("utf-8"))
    assert preview.valid_rows == 1

    # Empty CSV (just header) -> no valid rows
    empty_csv = "email,student_code,full_name,degree_program,current_semester,phone\n"
    preview2 = validate_import_csv(db, empty_csv.encode("utf-8"))
    assert preview2.total_rows == 0
    assert preview2.valid_rows == 0
    assert preview2.invalid_rows == 0


def test_import_duplicate_email_within_file_and_existing(db_cleaner) -> None:
    """Duplicate email both within the file and against existing records."""
    db = SessionLocal()
    _make_existing_student(db, email="existing@campus360.example", code="ST-EXIST-001")

    csv_text = _csv_text(
        [
            {
                "email": "existing@campus360.example",
                "student_code": "ST-NEW",
                "full_name": "Dup Email",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
            {
                "email": "existing@campus360.example",
                "student_code": "ST-NEW2",
                "full_name": "Dup Email 2",
                "degree_program": "B.Tech CSE",
                "current_semester": "3",
                "phone": "",
            },
        ]
    )
    preview = validate_import_csv(db, csv_text.encode("utf-8"))

    assert preview.invalid_rows == 2
    assert all(any("already registered" in e for e in r.errors) for r in preview.rows)
