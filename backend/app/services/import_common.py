"""Shared CSV import helpers (services/import_common.py).

The student and faculty bulk imports follow the same two-step contract
(preview without writes, then confirm). Everything that does not depend on
the kind of account - decoding the upload, enforcing the size and row limits,
parsing the header, and validating an email exactly the way login does - lives
here so the two importers cannot drift apart.
"""

from __future__ import annotations

import csv
import io

from fastapi import status
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.core.config import get_settings

#: The SAME validator ``LoginRequest.email`` uses. An address that passes the
#: import but fails this check could never be used to sign in.
_EMAIL_ADAPTER: TypeAdapter[str] = TypeAdapter(EmailStr)

#: Column lengths mirrored from the models, so an oversized value is reported
#: on its row instead of failing the whole confirm request or the database.
MAX_CODE_LENGTH = 32
MAX_NAME_LENGTH = 120
MAX_EMAIL_LENGTH = 255
MAX_PHONE_LENGTH = 20
MAX_TEXT_LENGTH = 120


class BulkImportError(Exception):
    """The upload cannot be processed at all (for example: it is too large)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def ensure_upload_size(csv_content: str) -> bytes:
    """Encode the uploaded CSV text, refusing anything above the size limit.

    ``CAMPUS360_IMPORT_MAX_FILE_MB`` is the server-side guarantee; the browser
    checks the same limit only to give faster feedback.
    """
    data = csv_content.encode("utf-8")
    limit_mb = get_settings().import_max_file_mb
    if len(data) > limit_mb * 1024 * 1024:
        raise BulkImportError(
            f"The file is larger than the {limit_mb} MB import limit.",
            status.HTTP_413_CONTENT_TOO_LARGE,
        )
    return data


def parse_csv(
    file_content: bytes, required_columns: tuple[str, ...]
) -> tuple[list[dict[str, str]], str | None]:
    """Parse CSV bytes into row dicts, or return ``([], error_message)``.

    Keys and values are stripped and fully-empty rows are dropped. Structural
    problems (encoding, header, malformed quoting) become a file-level error,
    never an unhandled exception.
    """
    try:
        text = file_content.decode("utf-8-sig")  # strip a BOM if present
    except UnicodeDecodeError:
        return [], "The file is not valid UTF-8 text. Save the CSV as UTF-8 and try again."

    reader = csv.DictReader(io.StringIO(text))
    try:
        fieldnames = [name.strip() for name in reader.fieldnames or [] if name]
        if not fieldnames:
            return [], "The CSV has no header row."
        reader.fieldnames = fieldnames

        missing = [column for column in required_columns if column not in fieldnames]
        if missing:
            return [], (
                "Missing required column(s): "
                + ", ".join(missing)
                + ". Download the template from the Import dialog."
            )

        rows = []
        for raw in reader:
            row = {
                key: (value.strip() if isinstance(value, str) else "")
                for key, value in raw.items()
                if key is not None
            }
            if any(row.values()):
                rows.append(row)
    except csv.Error as exc:
        return [], f"The CSV could not be read ({exc}). Check the file format and try again."

    return rows, None


def row_limit_error(row_count: int) -> str | None:
    """File-level message when the CSV has more rows than allowed."""
    limit = get_settings().import_max_rows
    if row_count > limit:
        return f"File has {row_count} rows; maximum allowed is {limit}. Split into smaller files."
    return None


def validate_email(value: str) -> tuple[str | None, str | None]:
    """Return ``(normalised_email, None)`` or ``(None, error_message)``."""
    if not value:
        return None, "Email is required."
    if len(value) > MAX_EMAIL_LENGTH:
        return None, f"Email must be at most {MAX_EMAIL_LENGTH} characters."
    try:
        return _EMAIL_ADAPTER.validate_python(value), None
    except ValidationError:
        return None, "Email is not a valid address."


def check_length(value: str, limit: int, label: str, errors: list[str]) -> None:
    """Append a length error for ``value`` when it exceeds ``limit``."""
    if value and len(value) > limit:
        errors.append(f"{label} must be at most {limit} characters.")


def normalised_email(email: str) -> str:
    """Lowercase form matching the ``users.email_normalized`` column."""
    return email.strip().lower()


def creation_failed_message() -> str:
    """Generic per-row message for an unexpected database failure.

    The real exception is logged by the caller; database internals are never
    sent to the browser.
    """
    return "Could not create the account because of a server error. Try this row again."
