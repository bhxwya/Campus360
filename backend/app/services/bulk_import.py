"""Bulk student import (services/bulk_import.py).

Admin uploads a CSV of student records. The flow is deliberately split into
two steps so nothing is written until the admin has reviewed a preview:

1. ``validate_import_csv`` - parse + validate every row (format, duplicates
   within the file, duplicates against existing database records). Returns a
   per-row preview with field-specific errors. NO database writes.
2. ``confirm_import`` - RE-VALIDATES each submitted row with the same rules
   (the endpoint accepts any JSON, not only rows the preview approved), then
   atomically creates an inactive User + StudentProfile + activation token per
   valid row, queues the activation email, and returns per-row results
   (created / skipped / failed + email status).

Security and consistency properties:

* The complete file is validated before any write. A single bad row never
  poisons a good row.
* Emails are validated exactly like the login form, so every imported account
  can actually sign in once activated.
* Duplicates are detected both within the CSV and against live database
  records. The database UNIQUE constraints remain authoritative; the
  pre-check only gives a precise field map for ordinary requests.
* Existing records are never silently overwritten. A row whose email or
  roll number already exists is reported as skipped, not updated.
* Account creation, profile creation, and email delivery statuses are tracked
  independently. A failed email does not delete a valid profile or create a
  duplicate account on retry.
* Passwords are never accepted from the admin and never appear in any
  output. Accounts start inactive with an uncrackable placeholder hash.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.programmes import SEMESTER_POLICY, canonical_name
from app.models.user import StudentProfile
from app.services import admin as admin_service
from app.services.activation import (
    create_inactive_user_and_activation_token,
    queue_activation_email,
)
from app.services.import_common import (
    MAX_CODE_LENGTH,
    MAX_NAME_LENGTH,
    MAX_PHONE_LENGTH,
    BulkImportError,
    check_length,
    creation_failed_message,
    normalised_email,
    parse_csv,
    row_limit_error,
    validate_email,
)

__all__ = [
    "REQUIRED_COLUMNS",
    "BulkImportError",
    "ImportConfirmation",
    "ImportPreview",
    "ImportRowResult",
    "build_csv_template",
    "confirm_import",
    "validate_import_csv",
]

logger = logging.getLogger(__name__)

#: Columns the import CSV must contain (in any order). ``password`` is
#: intentionally absent - students choose their own password at activation.
REQUIRED_COLUMNS = (
    "email",
    "student_code",
    "full_name",
    "degree_program",
    "current_semester",
    "phone",
)


@dataclass
class ImportRowResult:
    """Outcome for one CSV row."""

    row_number: int  # 1-based, counting the header as row 1
    email: str
    student_code: str
    full_name: str
    degree_program: str | None = None
    current_semester: int | None = None
    phone: str | None = None
    status: str = "valid"  # "valid" | "failed" (preview); "created" | "skipped" | "failed"
    errors: list[str] = field(default_factory=list)
    user_id: int | None = None
    profile_id: int | None = None
    email_sent: bool = False
    email_error: str | None = None


@dataclass
class ImportPreview:
    """Result of ``validate_import_csv`` - no writes have occurred."""

    total_rows: int
    valid_rows: int
    invalid_rows: int
    rows: list[ImportRowResult] = field(default_factory=list)
    file_error: str | None = None


@dataclass
class ImportConfirmation:
    """Result of ``confirm_import`` - writes have occurred."""

    created: int
    skipped: int
    failed: int
    email_sent: int
    email_failed: int
    rows: list[ImportRowResult] = field(default_factory=list)


@dataclass
class _CheckedRow:
    """A validated row: display values, storage values, and both error kinds."""

    result: ImportRowResult
    email: str | None  # validated address, ready to store
    programme: str | None  # canonical programme name, ready to store
    errors: list[str]  # format / in-file problems -> the row FAILS
    conflicts: list[str]  # already exists in the database -> the row is SKIPPED


def _text(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return "" if value is None else str(value).strip()


def _check_row(
    row: dict[str, Any],
    row_number: int,
    seen_emails: set[str],
    seen_codes: set[str],
    db: Session,
) -> _CheckedRow:
    """Validate one row without writing anything (shared by preview + confirm)."""
    email_raw = _text(row, "email")
    student_code = _text(row, "student_code")
    full_name = _text(row, "full_name")
    degree_program = _text(row, "degree_program")
    current_semester_raw = _text(row, "current_semester")
    phone = _text(row, "phone")

    errors: list[str] = []

    email, email_error = validate_email(email_raw)
    if email_error:
        errors.append(email_error)
    if not student_code:
        errors.append("Roll number is required.")
    if not full_name:
        errors.append("Full name is required.")
    check_length(student_code, MAX_CODE_LENGTH, "Roll number", errors)
    check_length(full_name, MAX_NAME_LENGTH, "Full name", errors)
    check_length(phone, MAX_PHONE_LENGTH, "Phone", errors)

    programme = None
    if degree_program:
        programme = canonical_name(degree_program)
        if programme is None:
            errors.append(
                f"Unknown programme '{degree_program}'. Choose from the programme catalogue."
            )

    semester_value: int | None = None
    if current_semester_raw:
        try:
            semester_value = int(current_semester_raw)
        except ValueError:
            errors.append("Semester must be a whole number.")
        else:
            if not SEMESTER_POLICY.minimum <= semester_value <= SEMESTER_POLICY.maximum:
                errors.append(
                    "Semester must be between "
                    f"{SEMESTER_POLICY.minimum} and {SEMESTER_POLICY.maximum}."
                )

    if email:
        key = normalised_email(email)
        if key in seen_emails:
            errors.append("Duplicate email within this file.")
        seen_emails.add(key)
    if student_code:
        if student_code in seen_codes:
            errors.append("Duplicate roll number within this file.")
        seen_codes.add(student_code)

    conflicts: list[str] = []
    found = admin_service._student_field_conflicts(
        db,
        email=email,
        student_code=student_code or None,
    )
    if "email" in found:
        conflicts.append("This email address is already registered.")
    if "student_code" in found:
        conflicts.append("This roll number is already assigned to another student.")

    result = ImportRowResult(
        row_number=row_number,
        email=email_raw,
        student_code=student_code,
        full_name=full_name,
        degree_program=degree_program or None,
        current_semester=semester_value,
        phone=phone or None,
    )
    return _CheckedRow(result, email, programme, errors, conflicts)


def validate_import_csv(db: Session, file_content: bytes) -> ImportPreview:
    """Validate every row in the CSV. NO database writes occur.

    Use this for the preview step: the admin reviews per-row errors and then
    calls ``confirm_import`` with only the valid rows.
    """
    rows, file_error = parse_csv(file_content, REQUIRED_COLUMNS)
    if file_error is not None:
        return ImportPreview(total_rows=0, valid_rows=0, invalid_rows=0, file_error=file_error)

    limit_error = row_limit_error(len(rows))
    if limit_error is not None:
        return ImportPreview(
            total_rows=len(rows),
            valid_rows=0,
            invalid_rows=len(rows),
            file_error=limit_error,
        )

    preview = ImportPreview(total_rows=len(rows), valid_rows=0, invalid_rows=0)
    seen_emails: set[str] = set()
    seen_codes: set[str] = set()

    for index, row in enumerate(rows, start=2):  # header is row 1
        checked = _check_row(row, index, seen_emails, seen_codes, db)
        result = checked.result
        result.errors = checked.errors + checked.conflicts
        result.status = "failed" if result.errors else "valid"
        preview.rows.append(result)
        if result.status == "valid":
            preview.valid_rows += 1
        else:
            preview.invalid_rows += 1

    return preview


def confirm_import(
    db: Session,
    rows: list[dict[str, Any]],
    admin_email: str,
) -> ImportConfirmation:
    """Create accounts for valid rows and queue activation emails.

    Every row is validated again here - the confirm endpoint is reachable
    without the preview. Invalid rows are reported as ``failed``; rows whose
    email/roll number already exists are ``skipped``. Each created row commits
    in its own transaction so one failure does not roll back the others.
    Email delivery failures are tracked separately and do not undo account
    creation.
    """
    confirmation = ImportConfirmation(created=0, skipped=0, failed=0, email_sent=0, email_failed=0)
    seen_emails: set[str] = set()
    seen_codes: set[str] = set()

    for row in rows:
        row_number = int(row.get("row_number") or 0)
        checked = _check_row(row, row_number, seen_emails, seen_codes, db)
        result = checked.result
        result.full_name = admin_service.normalize_person_name(result.full_name) or ""
        result.degree_program = checked.programme

        if checked.errors:
            result.status = "failed"
            result.errors = checked.errors
            confirmation.failed += 1
            confirmation.rows.append(result)
            continue
        if checked.conflicts:
            result.status = "skipped"
            result.errors = checked.conflicts
            confirmation.skipped += 1
            confirmation.rows.append(result)
            continue

        assert checked.email is not None  # guaranteed: no validation errors
        try:
            user, raw_token = create_inactive_user_and_activation_token(
                db,
                email=checked.email,
                email_normalized=normalised_email(checked.email),
            )
            profile = StudentProfile(
                user=user,
                student_code=result.student_code,
                full_name=result.full_name,
                degree_program=checked.programme,
                current_semester=result.current_semester,
                phone=result.phone,
            )
            db.add(profile)
            db.commit()
            db.refresh(user)
            db.refresh(profile)
        except Exception:
            db.rollback()
            logger.exception("Student import row %s could not be created", row_number)
            result.status = "failed"
            result.errors.append(creation_failed_message())
            confirmation.failed += 1
            confirmation.rows.append(result)
            continue

        # Queue activation email (failure tracked separately, no rollback).
        send_result = queue_activation_email(
            db,
            user=user,
            raw_token=raw_token,
            student_name=result.full_name,
        )
        result.status = "created"
        result.user_id = user.id
        result.profile_id = profile.id
        result.email_sent = send_result.sent
        result.email_error = send_result.error
        if send_result.sent:
            confirmation.email_sent += 1
        else:
            confirmation.email_failed += 1
        confirmation.created += 1
        confirmation.rows.append(result)

    return confirmation


def build_csv_template() -> str:
    """Return the CSV template text admins can download.

    Contains only the fields the import workflow accepts: no password, no
    user id, no activation token.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=REQUIRED_COLUMNS)
    writer.writeheader()
    writer.writerow(
        {
            "email": "ada.lovelace@campus360.example",
            "student_code": "ST-2026-0001",
            "full_name": "Ada Lovelace",
            "degree_program": "B.Tech CSE",
            "current_semester": "3",
            "phone": "",
        }
    )
    return buffer.getvalue()
