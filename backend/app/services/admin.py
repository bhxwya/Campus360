"""
Administrative business logic (backend/app/services/admin.py).

Stage 4's core: every admin operation lives here, so the router in
``api/admin.py`` stays thin (translate HTTP <-> service call, map errors to
status codes) and the logic can be reused by scripts and background jobs.

TWO RULES THAT SHAPE THIS MODULE
--------------------------------

1. ATOMICITY. Creating a student or a faculty member is a TWO-TABLE write:
   a login account (``users``) plus its role profile (``student_profiles`` /
   ``faculty_profiles``). Both must appear together or NEITHER may: a login
   with no profile is an account nobody can use, and a profile with no login
   is unreachable. Every such write therefore runs inside ONE transaction and
   rolls back completely on ``IntegrityError``.

2. NO DELETION, DEACTIVATION INSTEAD. Academic history (enrollments,
   attendance, marks) references students, courses and notices. Deleting a row
   would either fail on a RESTRICT foreign key or silently destroy history,
   so "removing" something means flipping ``is_active`` (accounts, courses) or
   setting ``status`` to ARCHIVED (notices).

Error contract: services raise ``AdminError`` with an HTTP status code; the
router converts it into a response. The service layer stays free of
``HTTPException`` so it can be called from a CLI or a task runner.
"""

import re
from datetime import UTC, datetime

from fastapi import status
from sqlalchemy import Numeric, case, cast, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.departments import (
    canonical_name as canonical_department_name,
)
from app.core.departments import (
    find_department,
)
from app.core.programmes import (
    canonical_name,
    find_programme,
    match_values,
)
from app.core.programmes import (
    programme_code as resolve_programme_code,
)
from app.core.security import hash_password
from app.models.announcement import Announcement
from app.models.attendance import AttendanceRecord
from app.models.course import Course, Enrollment
from app.models.enums import (
    AnnouncementAudience,
    AnnouncementCategory,
    AnnouncementStatus,
    AttendanceStatus,
    EnrollmentStatus,
    UserRole,
)
from app.models.mark import Mark
from app.models.session import UserSession
from app.models.timetable import TimetableEntry
from app.models.user import FacultyProfile, StudentProfile, User
from app.schemas.admin import (
    AnnouncementCreate,
    AnnouncementResponse,
    AnnouncementUpdate,
    AttendanceReport,
    AttendanceReportRow,
    CourseCreate,
    CourseResponse,
    CourseUpdate,
    EnrollmentCreate,
    EnrollmentReport,
    EnrollmentReportRow,
    EnrollmentResponse,
    FacultyCreate,
    FacultyResponse,
    FacultyUpdate,
    ReportSummary,
    StudentCreate,
    StudentResponse,
    StudentUpdate,
)

#: The pass mark used by the academic reports, in PERCENT.
#: Documented here (rather than hard-coded inside a query) because it is an
#: INSTITUTIONAL POLICY, not an implementation detail - a future stage may
#: move it into configuration.
PASS_MARK_PERCENT = 40.0

#: Attendance statuses that count as "the student attended the session".
#: LATE counts as attended: the student WAS in the class, and the admin UI
#: shows late as a separate badge rather than an absence.
ATTENDED_STATUSES = (AttendanceStatus.PRESENT, AttendanceStatus.LATE)

#: Field-specific duplicate messages for the student create/edit form. The
#: keys used in ``AdminError.field_errors`` are the request field names, so the
#: browser can highlight the exact input instead of parsing a generic sentence.
EMAIL_DUPLICATE_MESSAGE = "This email address is already registered."
ROLL_NUMBER_DUPLICATE_MESSAGE = "This roll number is already assigned to another student."
FACULTY_CODE_DUPLICATE_MESSAGE = "This faculty code is already assigned to another faculty member."


class AdminError(Exception):
    """Raised when an admin operation cannot proceed.

    Carries the HTTP status the router should answer with, mirroring the
    ``AuthError`` pattern in ``services/auth.py``. ``field_errors`` is optional
    and only populated when the client can map the failure to a specific form
    control (currently duplicate student email/roll number).
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        *,
        field_errors: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.field_errors = dict(field_errors or {})


def _not_found(what: str) -> AdminError:
    """404 for a missing row."""
    return AdminError(f"{what} not found", status.HTTP_404_NOT_FOUND)


def _conflict(message: str, *, field_errors: dict[str, str] | None = None) -> AdminError:
    """409 for a duplicate or otherwise conflicting write."""
    return AdminError(
        message,
        status.HTTP_409_CONFLICT,
        field_errors=field_errors,
    )


def _conflict_fields(field_errors: dict[str, str]) -> AdminError:
    """409 whose response identifies each duplicated form field independently."""
    return _conflict(" ".join(field_errors.values()), field_errors=field_errors)


def _normalised_email(email: str) -> str:
    """Lowercase form of an email, matching the ``email_normalized`` column.

    The database has a CHECK enforcing ``email_normalized = lower(email)`` and
    a UNIQUE index on it, so normalising here means a duplicate email is
    rejected by the DATABASE rather than by a racy application check.
    """
    return email.strip().lower()


def normalize_person_name(value: str | None) -> str | None:
    """Normalize a person's name for storage.

    Trims, collapses repeated internal whitespace, and applies consistent
    title-casing to ordinary (all-lowercase or first-letter) names. Names
    that already carry internal capitalization (``McDonald``, ``O'Neil``,
    hyphenated or initialled forms) are preserved verbatim so a blind
    ``.title()`` cannot mangle them.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return text
    already_formatted = any(
        len(token) > 1 and any(ch.isupper() for ch in token[1:]) for token in text.split(" ")
    )
    if already_formatted:
        return text
    return text.title()


def _natural_sort_key(value: str | None) -> list[tuple[int, object]]:
    """Natural-sort key for a faculty code (or similar string).

    Splits the value into alternating non-numeric and numeric chunks so that
    ``FAC-TEST-002`` sorts before ``FAC-TEST-010``. A stable secondary key
    (the profile id) is applied by the caller.
    """

    def _convert(chunk: str) -> tuple[int, object]:
        if chunk.isdigit():
            return (0, int(chunk))
        return (1, chunk.casefold())

    return [_convert(chunk) for chunk in re.split(r"(\d+)", value or "")]


def _student_field_conflicts(
    db: Session,
    *,
    email: str | None = None,
    student_code: str | None = None,
    exclude_user_id: int | None = None,
    exclude_profile_id: int | None = None,
) -> dict[str, str]:
    """Return field-specific duplicate errors for a student write.

    The database remains authoritative. This check gives an ordinary request a
    precise field map in one round trip; the commit handlers call the same
    helper after an ``IntegrityError`` so a concurrent request that slipped
    past the pre-check is still reported against the field that actually lost
    the unique race.

    ``exclude_*`` is what makes editing a student's own email or roll number
    safe: the row being edited cannot conflict with itself.
    """
    conflicts: dict[str, str] = {}

    if email is not None:
        email_query = select(User.id).where(User.email_normalized == _normalised_email(email))
        if exclude_user_id is not None:
            email_query = email_query.where(User.id != exclude_user_id)
        if db.scalar(email_query) is not None:
            conflicts["email"] = EMAIL_DUPLICATE_MESSAGE

    if student_code is not None:
        code = student_code.strip()
        if code:
            code_query = select(StudentProfile.id).where(StudentProfile.student_code == code)
            if exclude_profile_id is not None:
                code_query = code_query.where(StudentProfile.id != exclude_profile_id)
            if db.scalar(code_query) is not None:
                conflicts["student_code"] = ROLL_NUMBER_DUPLICATE_MESSAGE

    return conflicts


def _programme_for_storage(value: str | None, *, current: str | None = None) -> str | None:
    """Resolve a programme code/name/alias to the canonical display name.

    ``degree_program`` is a free-text column from the original schema. New
    writes are restricted to the catalogue so display text stays consistent;
    ``current`` (the stored value on a PATCH) grandfathers an existing
    uncatalogued legacy value ONLY when the client sends it back unchanged.
    No row is ever rewritten merely by reading it.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    resolved = canonical_name(text)
    if resolved is not None:
        return resolved
    if current is not None and text.casefold() == current.strip().casefold():
        return current
    raise AdminError(
        f"Unknown programme {text!r}. Choose a programme from the admin programme catalogue.",
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


def _faculty_field_conflicts(
    db: Session,
    *,
    email: str | None = None,
    faculty_code: str | None = None,
    exclude_user_id: int | None = None,
    exclude_profile_id: int | None = None,
) -> dict[str, str]:
    """Return field-specific duplicate errors for a faculty write."""
    conflicts: dict[str, str] = {}

    if email is not None:
        email_query = select(User.id).where(User.email_normalized == _normalised_email(email))
        if exclude_user_id is not None:
            email_query = email_query.where(User.id != exclude_user_id)
        if db.scalar(email_query) is not None:
            conflicts["email"] = EMAIL_DUPLICATE_MESSAGE

    if faculty_code is not None:
        code = faculty_code.strip()
        if code:
            code_query = select(FacultyProfile.id).where(FacultyProfile.faculty_code == code)
            if exclude_profile_id is not None:
                code_query = code_query.where(FacultyProfile.id != exclude_profile_id)
            if db.scalar(code_query) is not None:
                conflicts["faculty_code"] = FACULTY_CODE_DUPLICATE_MESSAGE

    return conflicts


def _department_for_storage(value: str | None, *, current: str | None = None) -> str | None:
    """Resolve a department code/name to the canonical display name.

    ``department`` is a free-text column from the original schema. New
    writes are restricted to the catalogue so display text stays consistent;
    ``current`` (the stored value on a PATCH) grandfathers an existing
    uncatalogued legacy value ONLY when the client sends it back unchanged.
    No row is ever rewritten merely by reading it.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    resolved = canonical_department_name(text)
    if resolved is not None:
        return resolved
    if current is not None and text.casefold() == current.strip().casefold():
        return current
    raise AdminError(
        f"Unknown department {text!r}. Choose a department from the admin department catalogue.",
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------
def _attendance_percentages(db: Session, student_ids: list[int]) -> dict[int, float | None]:
    """Attendance rate per student (``None`` when a student has no records).

    Computed from ``attendance_records`` at read time (never stored), exactly
    as the model's docstring requires. PRESENT and LATE both count as
    attended. One grouped query serves a whole page of students.
    """
    rates: dict[int, float | None] = dict.fromkeys(student_ids)
    if not student_ids:
        return rates
    rows = db.execute(
        select(
            AttendanceRecord.student_id,
            func.count(),
            func.count().filter(AttendanceRecord.status.in_(ATTENDED_STATUSES)),
        )
        .where(AttendanceRecord.student_id.in_(student_ids))
        .group_by(AttendanceRecord.student_id)
    ).all()
    for student_id, total, attended in rows:
        rates[student_id] = round(attended * 100.0 / total, 2) if total else None
    return rates


def _active_enrollment_counts(db: Session, column, ids: list[int]) -> dict[int, int]:
    """ACTIVE enrollment counts grouped by ``column`` (student or course id)."""
    counts = dict.fromkeys(ids, 0)
    if not ids:
        return counts
    rows = db.execute(
        select(column, func.count())
        .where(column.in_(ids), Enrollment.status == EnrollmentStatus.ACTIVE)
        .group_by(column)
    ).all()
    counts.update({key: count for key, count in rows})
    return counts


def _student_directory_order():
    """Deterministic roll-number ordering for directory pages and exports.

    Numeric-only codes come first and are compared as NUMERIC, so 2026001
    precedes 2026002. Alphanumeric codes follow in case-insensitive (``C``
    collation) lexicographic order. Numeric ties caused by leading zeros are
    broken by the text form, so ``0001`` precedes ``1``; codes that differ
    only by case have the same normalized key and fall back to the profile id.
    """
    trimmed = func.trim(StudentProfile.student_code)
    numeric = trimmed.op("~")(r"^[0-9]+$")
    return (
        case((numeric, 0), else_=1),
        case((numeric, cast(trimmed, Numeric)), else_=None),
        func.lower(trimmed).collate("C"),
        StudentProfile.id,
    )


def _student_responses(db: Session, profiles: list[StudentProfile]) -> list[StudentResponse]:
    """Build safe student projections for a page with a fixed number of queries."""
    ids = [profile.id for profile in profiles]
    attendance = _attendance_percentages(db, ids)
    enrolled = _active_enrollment_counts(db, Enrollment.student_id, ids)
    return [
        _student_projection(profile, attendance[profile.id], enrolled[profile.id])
        for profile in profiles
    ]


def _student_response(db: Session, profile: StudentProfile) -> StudentResponse:
    """Build the safe student projection (no password hash, ever)."""
    return _student_responses(db, [profile])[0]


def _student_projection(
    profile: StudentProfile, attendance_percentage: float | None, enrolled_courses: int
) -> StudentResponse:
    user = profile.user
    return StudentResponse(
        id=profile.id,
        user_id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        student_code=profile.student_code,
        full_name=profile.full_name,
        degree_program=profile.degree_program,
        programme_code=resolve_programme_code(profile.degree_program),
        current_semester=profile.current_semester,
        phone=profile.phone,
        attendance_percentage=attendance_percentage,
        enrolled_courses=enrolled_courses,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def create_student(db: Session, payload: StudentCreate) -> StudentResponse:
    """Create a student: login account + profile, ATOMICALLY.

    The account and the profile are inserted in ONE transaction. If either
    insert fails (duplicate email, duplicate student code), the transaction
    is rolled back completely - leaving no half-created student behind.

    A duplicate email or student code becomes a 409, not a 500: both are
    ordinary "that already exists" situations, not server faults. The result
    names the duplicated field(s) independently so the form can mark each
    input.
    """
    # Serve a precise field map for ordinary requests. A concurrent writer can
    # still win after this check; the IntegrityError branch below repeats the
    # same lookup after rollback and reports the field that actually conflicted.
    conflicts = _student_field_conflicts(
        db,
        email=payload.email,
        student_code=payload.student_code,
    )
    if conflicts:
        raise _conflict_fields(conflicts)

    user = User(
        email=payload.email.strip(),
        email_normalized=_normalised_email(payload.email),
        password_hash=hash_password(payload.password),
        role=UserRole.STUDENT,
        is_active=True,
    )
    profile = StudentProfile(
        user=user,
        student_code=payload.student_code.strip(),
        full_name=normalize_person_name(payload.full_name),
        degree_program=_programme_for_storage(payload.degree_program),
        current_semester=payload.current_semester,
        phone=payload.phone,
    )
    db.add(user)
    db.add(profile)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        conflicts = _student_field_conflicts(
            db,
            email=payload.email,
            student_code=payload.student_code,
        )
        if conflicts:
            raise _conflict_fields(conflicts) from exc
        # An integrity failure that is NOT a duplicate email/roll number must
        # keep its original 500 behaviour rather than being mislabelled.
        raise
    db.refresh(profile)
    return _student_response(db, profile)


def list_students(
    db: Session,
    *,
    search: str | None = None,
    department: str | None = None,
    is_active: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[StudentResponse], int]:
    """Return one page of students plus the TOTAL number of matches.

    ``department`` filters on ``degree_program`` - the current schema has no
    separate department column on the student profile, so the programme is
    the department-like grouping the UI shows.
    """
    filters = []
    if search:
        pattern = f"%{search.strip().lower()}%"
        filters.append(
            func.lower(StudentProfile.full_name).like(pattern)
            | func.lower(StudentProfile.student_code).like(pattern)
            | func.lower(User.email).like(pattern)
        )
    if department:
        # Accept the canonical name, the stable code or a legacy alias (e.g.
        # "B.Tech CSE") and match every stored spelling of that programme, so
        # filtering does not depend on exact display text.
        programme = find_programme(department)
        if programme is not None:
            spellings = {value.strip().lower() for value in match_values(programme)}
            filters.append(func.lower(StudentProfile.degree_program).in_(spellings))
        else:
            filters.append(func.lower(StudentProfile.degree_program) == department.strip().lower())
    if is_active is not None:
        filters.append(User.is_active == is_active)

    base = select(StudentProfile).join(User, StudentProfile.user_id == User.id).where(*filters)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.options(joinedload(StudentProfile.user))
        .order_by(*_student_directory_order())
        .limit(limit)
        .offset(offset)
    ).all()
    return _student_responses(db, list(rows)), total


def get_student(db: Session, student_id: int) -> StudentResponse:
    """Fetch one student by profile id, or raise a 404."""
    profile = db.get(StudentProfile, student_id)
    if profile is None:
        raise _not_found("Student")
    return _student_response(db, profile)


def update_student(db: Session, student_id: int, payload: StudentUpdate) -> StudentResponse:
    """Apply the fields the client actually sent to a student.

    ``exclude_unset`` is what makes a PATCH partial: a field the client did
    not mention is left untouched, instead of being overwritten with None.
    ``is_active`` lives on the ACCOUNT, not the profile, so deactivating a
    student deactivates the login too - one flag, one meaning.

    ``email`` also lives on the ACCOUNT: it IS the login identifier, so the
    profile and the login can never drift apart - there is only one column to
    update. Changing it revokes that student's live sessions (the same
    re-authenticate-after-an-identity-change rule a password change uses), and
    the old address can no longer log in. The profile row (and therefore its
    id, enrollments, attendance and marks) is never replaced.
    """
    profile = db.get(StudentProfile, student_id)
    if profile is None:
        raise _not_found("Student")

    profile_id = profile.id
    user_id = profile.user_id
    data = payload.model_dump(exclude_unset=True)

    email_supplied = "email" in data
    email = data.pop("email", None)
    normalized_email = ""
    if email_supplied:
        if not email:
            # EmailStr rejects malformed input at the edge; this covers an
            # explicit JSON null, which would violate users.email NOT NULL.
            raise AdminError("Email cannot be empty", status.HTTP_422_UNPROCESSABLE_CONTENT)
        normalized_email = _normalised_email(email)

    student_code_supplied = "student_code" in data
    student_code = None
    if student_code_supplied:
        # The column is NOT NULL + UNIQUE; trim to match create_student and
        # reject whitespace-only input with a validation error, not a 500.
        student_code = (data["student_code"] or "").strip()
        if not student_code:
            raise AdminError("Roll number cannot be empty", status.HTTP_422_UNPROCESSABLE_CONTENT)
        data["student_code"] = student_code

    # Check BOTH fields before mutating anything, excluding this very student.
    # Editing your own email/roll number is therefore never a self-conflict,
    # and a PATCH that duplicates both fields reports both independently.
    conflicts = _student_field_conflicts(
        db,
        email=email if email_supplied else None,
        student_code=student_code,
        exclude_user_id=user_id,
        exclude_profile_id=profile_id,
    )
    if conflicts:
        raise _conflict_fields(conflicts)

    if "degree_program" in data:
        # Codes and aliases are canonicalized; an uncatalogued legacy value is
        # preserved only when sent back unchanged (see _programme_for_storage).
        data["degree_program"] = _programme_for_storage(
            data["degree_program"], current=profile.degree_program
        )

    if "full_name" in data:
        data["full_name"] = normalize_person_name(data["full_name"])

    is_active = data.pop("is_active", None)
    for field, value in data.items():
        setattr(profile, field, value)
    if is_active is not None:
        profile.user.is_active = is_active

    email_changed = email_supplied and normalized_email != profile.user.email_normalized
    if email_supplied:
        profile.user.email = email.strip()
        profile.user.email_normalized = normalized_email
    if email_changed:
        # An address change is an identity change: revoke every server-side
        # session for THIS student, so a cookie issued under the old login
        # cannot outlive it. The acting admin's own session is untouched.
        db.execute(delete(UserSession).where(UserSession.user_id == profile.user_id))

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # A concurrent writer can win after the pre-check. Re-read the unique
        # fields (excluding this student) and report the field(s) that actually
        # conflicted. Other integrity failures keep their original 500 path.
        conflicts = _student_field_conflicts(
            db,
            email=email if email_supplied else None,
            student_code=student_code,
            exclude_user_id=user_id,
            exclude_profile_id=profile_id,
        )
        if conflicts:
            raise _conflict_fields(conflicts) from exc
        raise
    db.refresh(profile)
    return _student_response(db, profile)


def deactivate_student(db: Session, student_id: int) -> StudentResponse:
    """Deactivate (never delete) a student and their login."""
    profile = db.get(StudentProfile, student_id)
    if profile is None:
        raise _not_found("Student")
    profile.user.is_active = False
    db.commit()
    db.refresh(profile)
    return _student_response(db, profile)


# ---------------------------------------------------------------------------
# Faculty
# ---------------------------------------------------------------------------
def _faculty_responses(db: Session, profiles: list[FacultyProfile]) -> list[FacultyResponse]:
    """Build safe faculty projections; one grouped query counts led courses."""
    ids = [profile.id for profile in profiles]
    led: dict[int, int] = dict.fromkeys(ids, 0)
    if ids:
        rows = db.execute(
            select(Course.lead_faculty_id, func.count())
            .where(Course.lead_faculty_id.in_(ids))
            .group_by(Course.lead_faculty_id)
        ).all()
        led.update({faculty_id: count for faculty_id, count in rows})
    return [_faculty_projection(profile, led[profile.id]) for profile in profiles]


def _faculty_response(db: Session, profile: FacultyProfile) -> FacultyResponse:
    """Build the safe faculty projection (no password hash, ever)."""
    return _faculty_responses(db, [profile])[0]


def _faculty_projection(profile: FacultyProfile, assigned_courses: int) -> FacultyResponse:
    user = profile.user
    department = find_department(profile.department)
    return FacultyResponse(
        id=profile.id,
        user_id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        faculty_code=profile.faculty_code,
        full_name=profile.full_name,
        department=profile.department,
        department_code=department.code if department else None,
        designation=profile.designation,
        assigned_courses=assigned_courses,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def create_faculty(db: Session, payload: FacultyCreate) -> FacultyResponse:
    """Create a faculty member: login account + profile, ATOMICALLY.

    Same contract as ``create_student``: one transaction, full rollback on
    ``IntegrityError``, and a 409 whose ``field_errors`` name the duplicated
    field(s) (email and/or faculty code).
    """
    conflicts = _faculty_field_conflicts(db, email=payload.email, faculty_code=payload.faculty_code)
    if conflicts:
        raise _conflict_fields(conflicts)

    user = User(
        email=payload.email.strip(),
        email_normalized=_normalised_email(payload.email),
        password_hash=hash_password(payload.password),
        role=UserRole.FACULTY,
        is_active=True,
    )
    profile = FacultyProfile(
        user=user,
        faculty_code=payload.faculty_code.strip(),
        full_name=normalize_person_name(payload.full_name),
        department=_department_for_storage(payload.department),
        designation=payload.designation,
    )
    db.add(user)
    db.add(profile)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        conflicts = _faculty_field_conflicts(
            db, email=payload.email, faculty_code=payload.faculty_code
        )
        if conflicts:
            raise _conflict_fields(conflicts) from exc
        raise
    db.refresh(profile)
    return _faculty_response(db, profile)


def list_faculty(
    db: Session,
    *,
    search: str | None = None,
    department: str | None = None,
    designation: str | None = None,
    is_active: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[FacultyResponse], int]:
    """Return one page of faculty plus the TOTAL number of matches."""
    filters = []
    if search:
        pattern = f"%{search.strip().lower()}%"
        filters.append(
            func.lower(FacultyProfile.full_name).like(pattern)
            | func.lower(FacultyProfile.faculty_code).like(pattern)
            | func.lower(User.email).like(pattern)
        )
    if department:
        filters.append(func.lower(FacultyProfile.department) == department.strip().lower())
    if designation:
        filters.append(func.lower(FacultyProfile.designation) == designation.strip().lower())
    if is_active is not None:
        filters.append(User.is_active == is_active)

    # Natural code order (FAC-2 before FAC-10) is computed in Python, so only
    # the (id, code) pairs are sorted; full rows are loaded for one page only.
    keys = db.execute(
        select(FacultyProfile.id, FacultyProfile.faculty_code)
        .join(User, FacultyProfile.user_id == User.id)
        .where(*filters)
    ).all()
    keys.sort(key=lambda row: (_natural_sort_key(row.faculty_code), row.id))
    page_ids = [row.id for row in keys[offset : offset + limit]]
    profiles = {
        profile.id: profile
        for profile in db.scalars(
            select(FacultyProfile)
            .options(joinedload(FacultyProfile.user))
            .where(FacultyProfile.id.in_(page_ids))
        )
    }
    return _faculty_responses(db, [profiles[i] for i in page_ids]), len(keys)


def get_faculty(db: Session, faculty_id: int) -> FacultyResponse:
    """Fetch one faculty member by profile id, or raise a 404."""
    profile = db.get(FacultyProfile, faculty_id)
    if profile is None:
        raise _not_found("Faculty member")
    return _faculty_response(db, profile)


def update_faculty(db: Session, faculty_id: int, payload: FacultyUpdate) -> FacultyResponse:
    """Apply the fields the client actually sent to a faculty member."""
    profile = db.get(FacultyProfile, faculty_id)
    if profile is None:
        raise _not_found("Faculty member")

    data = payload.model_dump(exclude_unset=True)
    is_active = data.pop("is_active", None)
    if "department" in data:
        data["department"] = _department_for_storage(data["department"], current=profile.department)
    if "full_name" in data:
        data["full_name"] = normalize_person_name(data["full_name"])
    for field, value in data.items():
        setattr(profile, field, value)
    if is_active is not None:
        profile.user.is_active = is_active

    db.commit()
    db.refresh(profile)
    return _faculty_response(db, profile)


def deactivate_faculty(db: Session, faculty_id: int) -> FacultyResponse:
    """Deactivate (never delete) a faculty member and their login."""
    profile = db.get(FacultyProfile, faculty_id)
    if profile is None:
        raise _not_found("Faculty member")
    profile.user.is_active = False
    db.commit()
    db.refresh(profile)
    return _faculty_response(db, profile)


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------
def _course_responses(db: Session, courses: list[Course]) -> list[CourseResponse]:
    """Build course projections; one grouped query counts active enrollments."""
    enrolled = _active_enrollment_counts(db, Enrollment.course_id, [c.id for c in courses])
    return [_course_projection(course, enrolled[course.id]) for course in courses]


def _course_response(db: Session, course: Course) -> CourseResponse:
    """Build the safe course projection."""
    return _course_responses(db, [course])[0]


def _course_projection(course: Course, enrolled_students: int) -> CourseResponse:
    lead = course.lead_faculty
    return CourseResponse(
        id=course.id,
        code=course.code,
        title=course.title,
        description=course.description,
        credits=course.credits,
        department=course.department,
        is_active=course.is_active,
        lead_faculty_id=course.lead_faculty_id,
        lead_faculty_name=lead.full_name if lead else None,
        enrolled_students=enrolled_students,
        created_at=course.created_at,
        updated_at=course.updated_at,
    )


def _require_faculty(db: Session, faculty_id: int | None) -> None:
    """Validate an optional ``lead_faculty_id`` reference.

    A course pointing at a faculty member who does not exist would fail the
    foreign key at INSERT time with an opaque database error; checking here
    turns it into a clear 404/422 for the admin who typed the wrong id.
    """
    if faculty_id is None:
        return
    if db.get(FacultyProfile, faculty_id) is None:
        raise AdminError(
            f"Faculty member {faculty_id} not found",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )


def create_course(db: Session, payload: CourseCreate) -> CourseResponse:
    """Create a course in the catalogue."""
    _require_faculty(db, payload.lead_faculty_id)
    course = Course(
        code=payload.code.strip(),
        title=payload.title.strip(),
        description=payload.description,
        credits=payload.credits,
        department=payload.department,
        lead_faculty_id=payload.lead_faculty_id,
        is_active=payload.is_active,
    )
    db.add(course)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict(f"A course with code {payload.code!r} already exists") from exc
    db.refresh(course)
    return _course_response(db, course)


def list_courses(
    db: Session,
    *,
    search: str | None = None,
    department: str | None = None,
    is_active: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[CourseResponse], int]:
    """Return one page of courses plus the TOTAL number of matches."""
    filters = []
    if search:
        pattern = f"%{search.strip().lower()}%"
        filters.append(
            func.lower(Course.code).like(pattern) | func.lower(Course.title).like(pattern)
        )
    if department:
        filters.append(func.lower(Course.department) == department.strip().lower())
    if is_active is not None:
        filters.append(Course.is_active == is_active)

    base = select(Course).where(*filters)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.options(joinedload(Course.lead_faculty))
        .order_by(Course.code)
        .limit(limit)
        .offset(offset)
    ).all()
    return _course_responses(db, list(rows)), total


def get_course(db: Session, course_id: int) -> CourseResponse:
    """Fetch one course by id, or raise a 404."""
    course = db.get(Course, course_id)
    if course is None:
        raise _not_found("Course")
    return _course_response(db, course)


def update_course(db: Session, course_id: int, payload: CourseUpdate) -> CourseResponse:
    """Apply the fields the client actually sent to a course."""
    course = db.get(Course, course_id)
    if course is None:
        raise _not_found("Course")

    data = payload.model_dump(exclude_unset=True)
    _require_faculty(db, data.get("lead_faculty_id"))
    for field, value in data.items():
        setattr(course, field, value)

    db.commit()
    db.refresh(course)
    return _course_response(db, course)


def deactivate_course(db: Session, course_id: int) -> CourseResponse:
    """Deactivate (never delete) a course, keeping its academic history."""
    course = db.get(Course, course_id)
    if course is None:
        raise _not_found("Course")
    course.is_active = False
    db.commit()
    db.refresh(course)
    return _course_response(db, course)


# ---------------------------------------------------------------------------
# Enrollments
# ---------------------------------------------------------------------------
def _enrollment_response(db: Session, enrollment: Enrollment) -> EnrollmentResponse:
    """Build the enrollment projection, joined with the student and course."""
    student = enrollment.student
    course = enrollment.course
    return EnrollmentResponse(
        id=enrollment.id,
        student_id=student.id,
        student_code=student.student_code,
        student_name=student.full_name,
        course_id=course.id,
        course_code=course.code,
        course_title=course.title,
        status=enrollment.status,
        created_at=enrollment.created_at,
    )


def create_enrollment(db: Session, payload: EnrollmentCreate) -> EnrollmentResponse:
    """Enrol a student in a course.

    The UNIQUE(student_id, course_id) constraint makes a duplicate
    enrolment impossible at the DATABASE level; this service translates that
    into a clean 409 instead of a 500.
    """
    student = db.get(StudentProfile, payload.student_id)
    if student is None:
        raise AdminError(f"Student {payload.student_id} not found", status.HTTP_404_NOT_FOUND)
    course = db.get(Course, payload.course_id)
    if course is None:
        raise AdminError(f"Course {payload.course_id} not found", status.HTTP_404_NOT_FOUND)
    if not course.is_active:
        raise AdminError(
            f"Course {course.code} is inactive and cannot accept new enrollments",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    # Pre-check for a clear, status-aware message. The UNIQUE constraint still
    # arbitrates a concurrent duplicate below.
    existing = db.scalar(
        select(Enrollment).where(
            Enrollment.student_id == student.id,
            Enrollment.course_id == course.id,
        )
    )
    if existing is not None:
        if existing.status is EnrollmentStatus.DROPPED:
            raise _conflict(
                f"Student {student.student_code} has a dropped enrollment in "
                f"{course.code}; reactivate that enrollment instead"
            )
        raise _conflict(
            f"Student {student.student_code} already has an enrollment in "
            f"{course.code} ({existing.status.value})"
        )

    enrollment = Enrollment(
        student_id=student.id,
        course_id=course.id,
        status=payload.status,
    )
    db.add(enrollment)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict(
            f"Student {student.student_code} is already enrolled in {course.code}"
        ) from exc
    db.refresh(enrollment)
    return _enrollment_response(db, enrollment)


def list_enrollments(
    db: Session,
    *,
    student_id: int | None = None,
    course_id: int | None = None,
    status: EnrollmentStatus | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[EnrollmentResponse], int]:
    """Return one page of enrollments plus the TOTAL number of matches."""
    filters = []
    if student_id is not None:
        filters.append(Enrollment.student_id == student_id)
    if course_id is not None:
        filters.append(Enrollment.course_id == course_id)
    if status is not None:
        filters.append(Enrollment.status == status)

    base = select(Enrollment).where(*filters)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.options(joinedload(Enrollment.student), joinedload(Enrollment.course))
        .order_by(Enrollment.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [_enrollment_response(db, row) for row in rows], total


def update_enrollment_status(
    db: Session, enrollment_id: int, new_status: EnrollmentStatus
) -> EnrollmentResponse:
    """Change an enrolment's status (e.g. ACTIVE -> DROPPED).

    Dropping a course NEVER deletes the row: attendance and marks reference
    it historically, so only the status changes.
    """
    enrollment = db.get(Enrollment, enrollment_id)
    if enrollment is None:
        raise _not_found("Enrollment")
    enrollment.status = new_status
    db.commit()
    db.refresh(enrollment)
    return _enrollment_response(db, enrollment)


# ---------------------------------------------------------------------------
# Announcements / notices
# ---------------------------------------------------------------------------
def _announcement_response(announcement: Announcement) -> AnnouncementResponse:
    """Build the notice projection, including the author's email."""
    author = announcement.author
    return AnnouncementResponse(
        id=announcement.id,
        title=announcement.title,
        body=announcement.body,
        category=announcement.category,
        audience=announcement.audience,
        status=announcement.status,
        created_by=announcement.created_by,
        author_email=author.email if author else None,
        published_at=announcement.published_at,
        created_at=announcement.created_at,
        updated_at=announcement.updated_at,
    )


def create_announcement(
    db: Session, payload: AnnouncementCreate, *, author_id: int
) -> AnnouncementResponse:
    """Publish (or draft) a notice on behalf of the AUTHENTICATED admin.

    ``author_id`` comes from the session, never from the request body: a
    client cannot forge authorship by sending somebody else's id.

    Publishing stamps ``published_at`` when the client did not supply an
    explicit instant, so "recent notices" sorting is meaningful.
    """
    published_at = payload.published_at
    if published_at is None and payload.status == AnnouncementStatus.ACTIVE:
        published_at = datetime.now(UTC)

    announcement = Announcement(
        title=payload.title.strip(),
        body=payload.body.strip(),
        category=payload.category,
        audience=payload.audience,
        status=payload.status,
        created_by=author_id,
        published_at=published_at,
    )
    db.add(announcement)
    db.commit()
    db.refresh(announcement)
    return _announcement_response(announcement)


def list_announcements(
    db: Session,
    *,
    status: AnnouncementStatus | None = None,
    category: AnnouncementCategory | None = None,
    audience: AnnouncementAudience | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[AnnouncementResponse], int]:
    """Return one page of notices plus the TOTAL number of matches."""
    filters = []
    if status is not None:
        filters.append(Announcement.status == status)
    if category is not None:
        filters.append(Announcement.category == category)
    if audience is not None:
        filters.append(Announcement.audience == audience)

    base = select(Announcement).where(*filters)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.order_by(Announcement.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return [_announcement_response(row) for row in rows], total


def get_announcement(db: Session, announcement_id: int) -> AnnouncementResponse:
    """Fetch one notice by id, or raise a 404."""
    announcement = db.get(Announcement, announcement_id)
    if announcement is None:
        raise _not_found("Announcement")
    return _announcement_response(announcement)


def update_announcement(
    db: Session, announcement_id: int, payload: AnnouncementUpdate
) -> AnnouncementResponse:
    """Edit a notice's CONTENT. Publication status changes are separate."""
    announcement = db.get(Announcement, announcement_id)
    if announcement is None:
        raise _not_found("Announcement")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(announcement, field, value)

    db.commit()
    db.refresh(announcement)
    return _announcement_response(announcement)


def archive_announcement(db: Session, announcement_id: int) -> AnnouncementResponse:
    """ARCHIVE a notice instead of deleting it.

    Notices are public information that must survive: archiving hides it
    from the active list while keeping the row (and its author link) intact.
    """
    announcement = db.get(Announcement, announcement_id)
    if announcement is None:
        raise _not_found("Announcement")
    announcement.status = AnnouncementStatus.ARCHIVED
    db.commit()
    db.refresh(announcement)
    return _announcement_response(announcement)


def publish_announcement(db: Session, announcement_id: int) -> AnnouncementResponse:
    """Publish (un-archive) a notice, stamping ``published_at`` if needed."""
    announcement = db.get(Announcement, announcement_id)
    if announcement is None:
        raise _not_found("Announcement")
    announcement.status = AnnouncementStatus.ACTIVE
    if announcement.published_at is None:
        announcement.published_at = datetime.now(UTC)
    db.commit()
    db.refresh(announcement)
    return _announcement_response(announcement)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
#: Explains the two fields the schema cannot honestly populate.
REPORT_NOTES = (
    "average_cgpa is null: the schema has no grade scale or CGPA column yet",
    "faculty_publication_count is null: there is no publications table yet",
)


def _count(db: Session, statement) -> int:
    """Run a COUNT scalar query and return a plain int (never None)."""
    return db.scalar(statement) or 0


def _institution_attendance_rate(db: Session) -> float | None:
    """Institution-wide attendance rate, or None when nothing was recorded."""
    total = _count(db, select(func.count()).select_from(AttendanceRecord))
    if not total:
        return None
    attended = _count(
        db,
        select(func.count())
        .select_from(AttendanceRecord)
        .where(AttendanceRecord.status.in_(ATTENDED_STATUSES)),
    )
    return round(attended * 100.0 / total, 2)


def _pass_percentage(db: Session) -> float | None:
    """Share of graded students at or above the pass mark, or None.

    A student is "graded" once at least one ``marks`` row exists for them. A
    student counts as PASSED when their TOTAL scored percentage (sum of
    scores / sum of max scores) reaches ``PASS_MARK_PERCENT``.

    Returns None when nothing has been graded yet - an empty report is
    honest, a 0% report would be a lie.
    """
    rows = db.execute(
        select(
            Mark.student_id,
            func.sum(Mark.score),
            func.sum(Mark.max_score),
        ).group_by(Mark.student_id)
    ).all()
    if not rows:
        return None

    passed = 0
    for _student_id, total_score, total_max in rows:
        # NUMERIC columns come back as Decimal; convert before comparing with
        # the float pass mark (Decimal * float is a TypeError).
        if not total_max:
            continue
        percentage = float(total_score or 0) * 100.0 / float(total_max)
        if percentage >= PASS_MARK_PERCENT:
            passed += 1
    return round(passed * 100.0 / len(rows), 2)


def report_summary(db: Session) -> ReportSummary:
    """Institution-wide counters for the admin dashboard.

    Everything is COMPUTED from the live rows, so a report can never drift
    out of sync with the data it describes.
    """
    total_students = _count(db, select(func.count()).select_from(StudentProfile))
    active_students = _count(
        db,
        select(func.count())
        .select_from(StudentProfile)
        .join(User, StudentProfile.user_id == User.id)
        .where(User.is_active.is_(True)),
    )
    total_faculty = _count(db, select(func.count()).select_from(FacultyProfile))
    active_faculty = _count(
        db,
        select(func.count())
        .select_from(FacultyProfile)
        .join(User, FacultyProfile.user_id == User.id)
        .where(User.is_active.is_(True)),
    )
    total_courses = _count(db, select(func.count()).select_from(Course))
    active_courses = _count(
        db, select(func.count()).select_from(Course).where(Course.is_active.is_(True))
    )
    # Expired rows linger until they are swept, so only live sessions count.
    active_sessions = _count(
        db,
        select(func.count()).select_from(UserSession).where(UserSession.expires_at > func.now()),
    )
    total_enrollments = _count(
        db,
        select(func.count())
        .select_from(Enrollment)
        .where(Enrollment.status == EnrollmentStatus.ACTIVE),
    )

    return ReportSummary(
        total_students=total_students,
        active_students=active_students,
        total_faculty=total_faculty,
        active_faculty=active_faculty,
        total_courses=total_courses,
        active_courses=active_courses,
        active_sessions=active_sessions,
        total_enrollments=total_enrollments,
        institution_attendance_rate=_institution_attendance_rate(db),
        pass_percentage=_pass_percentage(db),
        average_cgpa=None,
        faculty_publication_count=None,
        notes=list(REPORT_NOTES),
    )


def enrollment_report(db: Session) -> EnrollmentReport:
    """Enrolment counts per course, plus a per-department breakdown.

    Only ACTIVE enrolments are counted: a dropped enrolment is history, not
    a student currently taking the course.
    """
    rows = db.execute(
        select(
            Course.id,
            Course.code,
            Course.title,
            Course.department,
            func.count(Enrollment.id),
        )
        .select_from(Course)
        .outerjoin(
            Enrollment,
            (Enrollment.course_id == Course.id) & (Enrollment.status == EnrollmentStatus.ACTIVE),
        )
        .group_by(Course.id, Course.code, Course.title, Course.department)
        .order_by(Course.code)
    ).all()

    courses = [
        EnrollmentReportRow(
            course_id=course_id,
            course_code=code,
            course_title=title,
            department=department,
            enrolled_students=count,
        )
        for course_id, code, title, department, count in rows
    ]

    by_department: dict[str, int] = {}
    for row in courses:
        key = row.department or "Unassigned"
        by_department[key] = by_department.get(key, 0) + row.enrolled_students

    return EnrollmentReport(
        total_enrollments=sum(row.enrolled_students for row in courses),
        courses=courses,
        by_department=by_department,
    )


def attendance_report(db: Session) -> AttendanceReport:
    """Attendance rates per course, computed from ``attendance_records``.

    PRESENT and LATE both count as attended (see ``ATTENDED_STATUSES``).
    """
    rows = db.execute(
        select(
            Course.id,
            Course.code,
            Course.title,
            func.count(AttendanceRecord.id),
            func.count(AttendanceRecord.id).filter(
                AttendanceRecord.status == AttendanceStatus.PRESENT
            ),
            func.count(AttendanceRecord.id).filter(
                AttendanceRecord.status == AttendanceStatus.LATE
            ),
            func.count(AttendanceRecord.id).filter(
                AttendanceRecord.status == AttendanceStatus.ABSENT
            ),
        )
        .select_from(Course)
        .outerjoin(
            TimetableEntry,
            TimetableEntry.course_id == Course.id,
        )
        .outerjoin(
            AttendanceRecord,
            AttendanceRecord.timetable_entry_id == TimetableEntry.id,
        )
        .group_by(Course.id, Course.code, Course.title)
        .order_by(Course.code)
    ).all()

    courses: list[AttendanceReportRow] = []
    total_records = 0
    total_present = 0
    for course_id, code, title, records, present, late, absent in rows:
        total_records += records
        total_present += present + late
        percentage = round((present + late) * 100.0 / records, 2) if records else None
        courses.append(
            AttendanceReportRow(
                course_id=course_id,
                course_code=code,
                course_title=title,
                total_records=records,
                present_count=present,
                late_count=late,
                absent_count=absent,
                attendance_percentage=percentage,
            )
        )

    institution_rate = round(total_present * 100.0 / total_records, 2) if total_records else None
    return AttendanceReport(
        total_records=total_records,
        institution_attendance_rate=institution_rate,
        courses=courses,
    )
