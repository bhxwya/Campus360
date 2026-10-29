"""Bulk faculty import (services/faculty_bulk_import.py).

Same two-step contract as the student import (``services/bulk_import.py``):
``validate_import_csv`` previews without writing, and ``confirm_import``
re-validates every submitted row before creating inactive faculty accounts
with single-use activation links. Shared parsing and validation helpers live
in ``services/import_common.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models.user import FacultyProfile, UserRole
from app.services import admin as admin_service
from app.services.activation import (
    create_inactive_user_and_activation_token,
    queue_activation_email,
)
from app.services.import_common import (
    MAX_CODE_LENGTH,
    MAX_NAME_LENGTH,
    MAX_TEXT_LENGTH,
    check_length,
    creation_failed_message,
    normalised_email,
    parse_csv,
    row_limit_error,
    validate_email,
)

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = (
    "email",
    "faculty_code",
    "full_name",
    "department",
    "designation",
)


@dataclass
class FacultyImportRowResult:
    row_number: int
    email: str
    faculty_code: str
    full_name: str
    department: str | None = None
    designation: str | None = None
    status: str = "valid"
    errors: list[str] = field(default_factory=list)
    user_id: int | None = None
    profile_id: int | None = None
    email_sent: bool = False
    email_error: str | None = None


@dataclass
class FacultyImportPreview:
    total_rows: int
    valid_rows: int
    invalid_rows: int
    rows: list[FacultyImportRowResult] = field(default_factory=list)
    file_error: str | None = None


@dataclass
class FacultyImportConfirmation:
    created: int
    skipped: int
    failed: int
    email_sent: int
    email_failed: int
    rows: list[FacultyImportRowResult] = field(default_factory=list)


@dataclass
class _CheckedRow:
    result: FacultyImportRowResult
    email: str | None
    department: str | None  # canonical department name, ready to store
    errors: list[str]
    conflicts: list[str]


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
    faculty_code = _text(row, "faculty_code")
    full_name = _text(row, "full_name")
    department_raw = _text(row, "department")
    designation = _text(row, "designation")

    errors: list[str] = []

    email, email_error = validate_email(email_raw)
    if email_error:
        errors.append(email_error)
    if not faculty_code:
        errors.append("Faculty code is required.")
    if not full_name:
        errors.append("Full name is required.")
    check_length(faculty_code, MAX_CODE_LENGTH, "Faculty code", errors)
    check_length(full_name, MAX_NAME_LENGTH, "Full name", errors)
    check_length(designation, MAX_TEXT_LENGTH, "Designation", errors)

    department = None
    if department_raw:
        try:
            department = admin_service._department_for_storage(department_raw)
        except admin_service.AdminError as exc:
            errors.append(exc.message)

    if email:
        key = normalised_email(email)
        if key in seen_emails:
            errors.append("Duplicate email within this file.")
        seen_emails.add(key)
    if faculty_code:
        if faculty_code in seen_codes:
            errors.append("Duplicate faculty code within this file.")
        seen_codes.add(faculty_code)

    conflicts = list(
        admin_service._faculty_field_conflicts(
            db,
            email=email,
            faculty_code=faculty_code or None,
        ).values()
    )

    result = FacultyImportRowResult(
        row_number=row_number,
        email=email_raw,
        faculty_code=faculty_code,
        full_name=full_name,
        department=department_raw or None,
        designation=designation or None,
    )
    return _CheckedRow(result, email, department, errors, conflicts)


def validate_import_csv(db: Session, file_content: bytes) -> FacultyImportPreview:
    """Validate every row in the CSV. NO database writes occur."""
    rows, file_error = parse_csv(file_content, REQUIRED_COLUMNS)
    if file_error is not None:
        return FacultyImportPreview(
            total_rows=0, valid_rows=0, invalid_rows=0, file_error=file_error
        )

    limit_error = row_limit_error(len(rows))
    if limit_error is not None:
        return FacultyImportPreview(
            total_rows=len(rows),
            valid_rows=0,
            invalid_rows=len(rows),
            file_error=limit_error,
        )

    preview = FacultyImportPreview(total_rows=len(rows), valid_rows=0, invalid_rows=0)
    seen_emails: set[str] = set()
    seen_codes: set[str] = set()

    for index, row in enumerate(rows, start=2):
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
) -> FacultyImportConfirmation:
    """Re-validate each row, then create inactive faculty accounts.

    Invalid rows are ``failed``, rows that already exist are ``skipped``, and
    each created row commits on its own.
    """
    confirmation = FacultyImportConfirmation(
        created=0, skipped=0, failed=0, email_sent=0, email_failed=0
    )
    seen_emails: set[str] = set()
    seen_codes: set[str] = set()

    for row in rows:
        row_number = int(row.get("row_number") or 0)
        checked = _check_row(row, row_number, seen_emails, seen_codes, db)
        result = checked.result
        result.full_name = admin_service.normalize_person_name(result.full_name) or ""
        result.department = checked.department

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
                role=UserRole.FACULTY,
            )
            profile = FacultyProfile(
                user=user,
                faculty_code=result.faculty_code,
                full_name=result.full_name,
                department=checked.department,
                designation=result.designation,
            )
            db.add(profile)
            db.commit()
            db.refresh(user)
            db.refresh(profile)
        except Exception:
            db.rollback()
            logger.exception("Faculty import row %s could not be created", row_number)
            result.status = "failed"
            result.errors.append(creation_failed_message())
            confirmation.failed += 1
            confirmation.rows.append(result)
            continue

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
