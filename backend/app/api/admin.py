"""
Administrative endpoints (api/admin.py).

Stage 4's admin API, mounted under ``/api/v1/admin``. Every route is guarded
by ``require_admin`` (server-side role check) and every UNSAFE route also by
``require_csrf`` (synchronizer token), so a hidden frontend button is never
the only thing standing between a student and the admin API.

Route map:

  Students     GET/POST /students, GET/PATCH/DELETE /students/{id}
  Faculty      GET/POST /faculty, GET/PATCH/DELETE /faculty/{id}
  Courses      GET/POST /courses, GET/PATCH/DELETE /courses/{id}
  Enrollments  GET/POST /enrollments, PATCH /enrollments/{id}/status
  Notices      GET/POST /announcements, GET/PATCH /announcements/{id},
               POST /announcements/{id}/publish, POST /announcements/{id}/archive
  Reports      GET /reports/summary, /reports/enrollments, /reports/attendance

"DELETE" here means DEACTIVATE, never destroy: academic history (enrolments,
attendance, marks) references these rows, so removing a student or a course
flips ``is_active`` and keeps the data. Notices are ARCHIVED for the same
reason. The project-wide policy is "preserve history".

The router stays thin: it maps HTTP to service calls and turns the service's
``AdminError`` into a response. No business logic lives here.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import DbSession, require_admin, require_csrf
from app.core.departments import DEPARTMENTS
from app.core.programmes import PROGRAMMES
from app.models.enums import (
    AnnouncementAudience,
    AnnouncementCategory,
    AnnouncementStatus,
    EnrollmentStatus,
)
from app.models.user import StudentProfile, User
from app.schemas.admin import (
    AnnouncementCreate,
    AnnouncementListResponse,
    AnnouncementResponse,
    AnnouncementUpdate,
    AttendanceReport,
    CourseCreate,
    CourseListResponse,
    CourseResponse,
    CourseUpdate,
    DepartmentResponse,
    EnrollmentCreate,
    EnrollmentReport,
    EnrollmentResponse,
    FacultyCreate,
    FacultyImportConfirmRequest,
    FacultyImportConfirmResponse,
    FacultyImportPreviewResponse,
    FacultyImportRowPreview,
    FacultyImportRowResult,
    FacultyImportUploadRequest,
    FacultyListResponse,
    FacultyResponse,
    FacultyUpdate,
    ImportConfirmRequest,
    ImportConfirmResponse,
    ImportPreviewResponse,
    ImportRowPreview,
    ImportRowResult,
    ImportUploadRequest,
    ProgrammeResponse,
    ReportSummary,
    StudentCreate,
    StudentListResponse,
    StudentResponse,
    StudentUpdate,
)
from app.services import admin as admin_service
from app.services import bulk_import as student_import
from app.services import faculty_bulk_import as faculty_import
from app.services.activation import queue_activation_email, resend_activation_token
from app.services.import_common import BulkImportError, ensure_upload_size

router = APIRouter(prefix="/admin", tags=["Admin"])

#: The authenticated admin. Using it as a dependency is what enforces the
#: role check on EVERY route in this router.
AdminUser = Annotated[User, Depends(require_admin)]
#: CSRF guard for unsafe (state-changing) requests.
CsrfGuard = Annotated[None, Depends(require_csrf)]

#: Pagination bounds shared by every list endpoint.
LimitQuery = Annotated[int, Query(ge=1, le=100, description="Page size (max 100).")]
OffsetQuery = Annotated[int, Query(ge=0, description="Rows to skip.")]


def _fail(exc: admin_service.AdminError) -> HTTPException:
    """Translate a service error into an HTTP error response.

    Most errors keep the existing string ``detail``. Duplicate student fields
    add a structured ``field_errors`` map so the browser can mark the exact
    inputs instead of matching sensitive text.
    """
    detail: str | dict[str, object] = exc.message
    if exc.field_errors:
        detail = {
            "message": exc.message,
            "field_errors": exc.field_errors,
        }
    return HTTPException(status_code=exc.status_code, detail=detail)


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------
@router.get(
    "/students",
    response_model=StudentListResponse,
    summary="List students",
)
def list_students(
    _admin: AdminUser,
    db: DbSession,
    search: Annotated[str | None, Query(description="Match name, code or email.")] = None,
    department: Annotated[str | None, Query(description="Filter by degree program.")] = None,
    is_active: Annotated[bool | None, Query(description="Filter by account state.")] = None,
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> StudentListResponse:
    """Return one page of students plus the total number of matches.

    A SAFE GET: it changes nothing, so it needs no CSRF token.
    """
    items, total = admin_service.list_students(
        db,
        search=search,
        department=department,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return StudentListResponse(items=items, total=total, limit=limit, offset=offset)


@router.post(
    "/students",
    response_model=StudentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a student",
)
def create_student(
    payload: StudentCreate,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> StudentResponse:
    """Create a student account and its profile in one transaction.

    Unsafe request: requires the CSRF token. A duplicate email or student
    code answers 409, never 500.
    """
    try:
        return admin_service.create_student(db, payload)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.get(
    "/students/{student_id}",
    response_model=StudentResponse,
    summary="Fetch one student",
)
def get_student(student_id: int, _admin: AdminUser, db: DbSession) -> StudentResponse:
    """Fetch a single student by profile id."""
    try:
        return admin_service.get_student(db, student_id)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.patch(
    "/students/{student_id}",
    response_model=StudentResponse,
    summary="Update a student",
)
def update_student(
    student_id: int,
    payload: StudentUpdate,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> StudentResponse:
    """Apply the supplied fields to a student (partial update)."""
    try:
        return admin_service.update_student(db, student_id, payload)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.delete(
    "/students/{student_id}",
    response_model=StudentResponse,
    summary="Deactivate a student",
)
def deactivate_student(
    student_id: int,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> StudentResponse:
    """Deactivate a student (and their login) - the row is preserved."""
    try:
        return admin_service.deactivate_student(db, student_id)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Bulk student import + activation
# ---------------------------------------------------------------------------
@router.post(
    "/students/import",
    response_model=ImportPreviewResponse,
    summary="Validate a student import CSV (preview only, no writes)",
)
def import_students_preview(
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
    payload: ImportUploadRequest,
) -> ImportPreviewResponse:
    """Parse and validate every row in the CSV without writing anything.

    Returns per-row results so the admin can review errors before confirming.
    Duplicates are checked both within the file and against existing records.
    An upload above ``CAMPUS360_IMPORT_MAX_FILE_MB`` is refused with 413.
    """
    try:
        content = ensure_upload_size(payload.csv_content)
    except BulkImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    preview = student_import.validate_import_csv(db, content)
    return ImportPreviewResponse(
        total_rows=preview.total_rows,
        valid_rows=preview.valid_rows,
        invalid_rows=preview.invalid_rows,
        rows=[
            ImportRowPreview(
                row_number=r.row_number,
                email=r.email,
                student_code=r.student_code,
                full_name=r.full_name,
                degree_program=r.degree_program,
                current_semester=r.current_semester,
                phone=r.phone,
                status=r.status,
                errors=r.errors,
            )
            for r in preview.rows
        ],
        file_error=preview.file_error,
    )


@router.post(
    "/students/import/confirm",
    response_model=ImportConfirmResponse,
    summary="Create accounts for validated import rows and queue activation emails",
)
def import_students_confirm(
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
    payload: ImportConfirmRequest,
) -> ImportConfirmResponse:
    """Create inactive accounts + profiles for the confirmed rows and queue an
    activation email for each. Email delivery failures are tracked separately
    and never undo account creation.
    """
    rows = [
        {
            "row_number": r.row_number,
            "email": r.email,
            "student_code": r.student_code,
            "full_name": r.full_name,
            "degree_program": r.degree_program,
            "current_semester": r.current_semester,
            "phone": r.phone,
        }
        for r in payload.rows
    ]
    confirmation = student_import.confirm_import(db, rows, admin_email=_admin.email)
    return ImportConfirmResponse(
        created=confirmation.created,
        skipped=confirmation.skipped,
        failed=confirmation.failed,
        email_sent=confirmation.email_sent,
        email_failed=confirmation.email_failed,
        rows=[
            ImportRowResult(
                row_number=r.row_number,
                email=r.email,
                student_code=r.student_code,
                full_name=r.full_name,
                status=r.status,
                errors=r.errors,
                user_id=r.user_id,
                profile_id=r.profile_id,
                email_sent=r.email_sent,
                email_error=r.email_error,
            )
            for r in confirmation.rows
        ],
    )


# ---------------------------------------------------------------------------
# Bulk faculty import + activation
# ---------------------------------------------------------------------------
@router.post(
    "/faculty/import",
    response_model=FacultyImportPreviewResponse,
    summary="Validate a faculty import CSV (preview only, no writes)",
)
def import_faculty_preview(
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
    payload: FacultyImportUploadRequest,
) -> FacultyImportPreviewResponse:
    """Faculty counterpart of the student import preview (no writes)."""
    try:
        content = ensure_upload_size(payload.csv_content)
    except BulkImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    preview = faculty_import.validate_import_csv(db, content)
    return FacultyImportPreviewResponse(
        total_rows=preview.total_rows,
        valid_rows=preview.valid_rows,
        invalid_rows=preview.invalid_rows,
        rows=[
            FacultyImportRowPreview(
                row_number=r.row_number,
                email=r.email,
                faculty_code=r.faculty_code,
                full_name=r.full_name,
                department=r.department,
                designation=r.designation,
                status=r.status,
                errors=r.errors,
            )
            for r in preview.rows
        ],
        file_error=preview.file_error,
    )


@router.post(
    "/faculty/import/confirm",
    response_model=FacultyImportConfirmResponse,
    summary="Create accounts for validated faculty import rows and queue activation emails",
)
def import_faculty_confirm(
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
    payload: FacultyImportConfirmRequest,
) -> FacultyImportConfirmResponse:
    """Re-validate and create inactive faculty accounts with activation links."""
    rows = [
        {
            "row_number": r.row_number,
            "email": r.email,
            "faculty_code": r.faculty_code,
            "full_name": r.full_name,
            "department": r.department,
            "designation": r.designation,
        }
        for r in payload.rows
    ]
    confirmation = faculty_import.confirm_import(db, rows, admin_email=_admin.email)
    return FacultyImportConfirmResponse(
        created=confirmation.created,
        skipped=confirmation.skipped,
        failed=confirmation.failed,
        email_sent=confirmation.email_sent,
        email_failed=confirmation.email_failed,
        rows=[
            FacultyImportRowResult(
                row_number=r.row_number,
                email=r.email,
                faculty_code=r.faculty_code,
                full_name=r.full_name,
                status=r.status,
                errors=r.errors,
                user_id=r.user_id,
                profile_id=r.profile_id,
                email_sent=r.email_sent,
                email_error=r.email_error,
            )
            for r in confirmation.rows
        ],
    )


@router.post(
    "/students/{student_id}/resend-activation",
    response_model=dict[str, str],
    summary="Revoke old activation tokens and resend the activation email",
)
def resend_student_activation(
    student_id: int,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> dict[str, str]:
    """Revoke any unconsumed activation tokens for the student, create a new
    one, and queue a fresh activation email. No account data changes.
    """
    profile = db.get(StudentProfile, student_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    if profile.user.is_active:
        return {"detail": "Account is already active."}

    user, raw_token = resend_activation_token(db, user_id=profile.user_id)
    send_result = queue_activation_email(
        db,
        user=user,
        raw_token=raw_token,
        student_name=profile.full_name,
    )
    return {
        "detail": "Activation email queued."
        if send_result.sent
        else f"Activation email could not be sent: {send_result.error}",
    }


@router.get(
    "/programmes",
    response_model=list[ProgrammeResponse],
    summary="List the academic programme catalogue",
)
def list_programmes(_admin: AdminUser) -> list[ProgrammeResponse]:
    """Return the canonical programme/branch list (NOT courses).

    The catalogue is configuration, not user data, so it is served from the
    code-level policy list and requires no database round-trip. Students store
    a programme; subjects remain separate ``courses`` rows.
    """
    return [
        ProgrammeResponse(code=programme.code, name=programme.name, group=programme.group)
        for programme in PROGRAMMES
    ]


@router.get(
    "/departments",
    response_model=list[DepartmentResponse],
    summary="List the department catalogue",
)
def list_departments(_admin: AdminUser) -> list[DepartmentResponse]:
    """Return the canonical department list.

    The catalogue is configuration, not user data, so it is served from the
    code-level policy list and requires no database round-trip.
    """
    return [
        DepartmentResponse(code=department.code, name=department.name) for department in DEPARTMENTS
    ]


# ---------------------------------------------------------------------------
# Faculty
# ---------------------------------------------------------------------------
@router.get(
    "/faculty",
    response_model=FacultyListResponse,
    summary="List faculty",
)
def list_faculty(
    _admin: AdminUser,
    db: DbSession,
    search: Annotated[str | None, Query(description="Match name, code or email.")] = None,
    department: Annotated[str | None, Query(description="Filter by department.")] = None,
    designation: Annotated[str | None, Query(description="Filter by designation.")] = None,
    is_active: Annotated[bool | None, Query(description="Filter by account state.")] = None,
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> FacultyListResponse:
    """Return one page of faculty plus the total number of matches."""
    items, total = admin_service.list_faculty(
        db,
        search=search,
        department=department,
        designation=designation,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return FacultyListResponse(items=items, total=total, limit=limit, offset=offset)


@router.post(
    "/faculty",
    response_model=FacultyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a faculty member",
)
def create_faculty(
    payload: FacultyCreate,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> FacultyResponse:
    """Create a faculty account and its profile in one transaction."""
    try:
        return admin_service.create_faculty(db, payload)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.get(
    "/faculty/{faculty_id}",
    response_model=FacultyResponse,
    summary="Fetch one faculty member",
)
def get_faculty(faculty_id: int, _admin: AdminUser, db: DbSession) -> FacultyResponse:
    """Fetch a single faculty member by profile id."""
    try:
        return admin_service.get_faculty(db, faculty_id)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.patch(
    "/faculty/{faculty_id}",
    response_model=FacultyResponse,
    summary="Update a faculty member",
)
def update_faculty(
    faculty_id: int,
    payload: FacultyUpdate,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> FacultyResponse:
    """Apply the supplied fields to a faculty member (partial update)."""
    try:
        return admin_service.update_faculty(db, faculty_id, payload)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.delete(
    "/faculty/{faculty_id}",
    response_model=FacultyResponse,
    summary="Deactivate a faculty member",
)
def deactivate_faculty(
    faculty_id: int,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> FacultyResponse:
    """Deactivate a faculty member (and their login) - the row is preserved."""
    try:
        return admin_service.deactivate_faculty(db, faculty_id)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------
@router.get(
    "/courses",
    response_model=CourseListResponse,
    summary="List courses",
)
def list_courses(
    _admin: AdminUser,
    db: DbSession,
    search: Annotated[str | None, Query(description="Match code or title.")] = None,
    department: Annotated[str | None, Query(description="Filter by department.")] = None,
    is_active: Annotated[bool | None, Query(description="Filter by catalogue state.")] = None,
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> CourseListResponse:
    """Return one page of courses plus the total number of matches."""
    items, total = admin_service.list_courses(
        db,
        search=search,
        department=department,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return CourseListResponse(items=items, total=total, limit=limit, offset=offset)


@router.post(
    "/courses",
    response_model=CourseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a course",
)
def create_course(
    payload: CourseCreate,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> CourseResponse:
    """Create a course in the catalogue."""
    try:
        return admin_service.create_course(db, payload)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.get(
    "/courses/{course_id}",
    response_model=CourseResponse,
    summary="Fetch one course",
)
def get_course(course_id: int, _admin: AdminUser, db: DbSession) -> CourseResponse:
    """Fetch a single course by id."""
    try:
        return admin_service.get_course(db, course_id)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.patch(
    "/courses/{course_id}",
    response_model=CourseResponse,
    summary="Update a course",
)
def update_course(
    course_id: int,
    payload: CourseUpdate,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> CourseResponse:
    """Apply the supplied fields to a course (partial update)."""
    try:
        return admin_service.update_course(db, course_id, payload)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.delete(
    "/courses/{course_id}",
    response_model=CourseResponse,
    summary="Deactivate a course",
)
def deactivate_course(
    course_id: int,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> CourseResponse:
    """Deactivate a course - enrolments, attendance and marks are preserved."""
    try:
        return admin_service.deactivate_course(db, course_id)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Enrollments
# ---------------------------------------------------------------------------
@router.get(
    "/enrollments",
    response_model=list[EnrollmentResponse],
    summary="List enrollments",
)
def list_enrollments(
    _admin: AdminUser,
    db: DbSession,
    student_id: Annotated[int | None, Query(description="Filter by student profile id.")] = None,
    course_id: Annotated[int | None, Query(description="Filter by course id.")] = None,
    status_filter: Annotated[
        EnrollmentStatus | None, Query(alias="status", description="Filter by status.")
    ] = None,
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> list[EnrollmentResponse]:
    """Return one page of enrollments.

    The query parameter is named ``status``; the Python argument is
    ``status_filter`` because ``status`` collides with FastAPI's
    ``status`` module import in this module.
    """
    items, _total = admin_service.list_enrollments(
        db,
        student_id=student_id,
        course_id=course_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return items


@router.post(
    "/enrollments",
    response_model=EnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enroll a student in a course",
)
def create_enrollment(
    payload: EnrollmentCreate,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> EnrollmentResponse:
    """Enrol a student in a course.

    A duplicate enrolment is impossible at the database level; it is
    reported here as a clean 409.
    """
    try:
        return admin_service.create_enrollment(db, payload)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.patch(
    "/enrollments/{enrollment_id}/status",
    response_model=EnrollmentResponse,
    summary="Change an enrollment status",
)
def update_enrollment_status(
    enrollment_id: int,
    new_status: Annotated[EnrollmentStatus, Query(alias="status")],
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> EnrollmentResponse:
    """Change an enrolment's status (e.g. to ``dropped``).

    The row is never deleted - attendance and marks reference it.
    """
    try:
        return admin_service.update_enrollment_status(db, enrollment_id, new_status)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Announcements / notices
# ---------------------------------------------------------------------------
@router.get(
    "/announcements",
    response_model=AnnouncementListResponse,
    summary="List notices",
)
def list_announcements(
    _admin: AdminUser,
    db: DbSession,
    status_filter: Annotated[
        AnnouncementStatus | None, Query(alias="status", description="Active or archived.")
    ] = None,
    category: Annotated[
        AnnouncementCategory | None, Query(description="Filter by category.")
    ] = None,
    audience: Annotated[
        AnnouncementAudience | None, Query(description="Filter by target audience.")
    ] = None,
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> AnnouncementListResponse:
    """Return one page of notices plus the total number of matches."""
    items, total = admin_service.list_announcements(
        db,
        status=status_filter,
        category=category,
        audience=audience,
        limit=limit,
        offset=offset,
    )
    return AnnouncementListResponse(items=items, total=total, limit=limit, offset=offset)


@router.post(
    "/announcements",
    response_model=AnnouncementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Publish a notice",
)
def create_announcement(
    payload: AnnouncementCreate,
    admin_user: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> AnnouncementResponse:
    """Publish (or draft) a notice authored by the AUTHENTICATED admin.

    The author is taken from the session, never from the body, so authorship
    cannot be forged.
    """
    try:
        return admin_service.create_announcement(db, payload, author_id=admin_user.id)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.get(
    "/announcements/{announcement_id}",
    response_model=AnnouncementResponse,
    summary="Fetch one notice",
)
def get_announcement(
    announcement_id: int, _admin: AdminUser, db: DbSession
) -> AnnouncementResponse:
    """Fetch a single notice by id."""
    try:
        return admin_service.get_announcement(db, announcement_id)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.patch(
    "/announcements/{announcement_id}",
    response_model=AnnouncementResponse,
    summary="Edit a notice",
)
def update_announcement(
    announcement_id: int,
    payload: AnnouncementUpdate,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> AnnouncementResponse:
    """Edit a notice's content (partial update)."""
    try:
        return admin_service.update_announcement(db, announcement_id, payload)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.post(
    "/announcements/{announcement_id}/archive",
    response_model=AnnouncementResponse,
    summary="Archive a notice",
)
def archive_announcement(
    announcement_id: int,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> AnnouncementResponse:
    """Archive a notice. Notices are archived, never deleted."""
    try:
        return admin_service.archive_announcement(db, announcement_id)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


@router.post(
    "/announcements/{announcement_id}/publish",
    response_model=AnnouncementResponse,
    summary="Publish a notice",
)
def publish_announcement(
    announcement_id: int,
    _admin: AdminUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> AnnouncementResponse:
    """Publish (un-archive) a notice."""
    try:
        return admin_service.publish_announcement(db, announcement_id)
    except admin_service.AdminError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@router.get(
    "/reports/summary",
    response_model=ReportSummary,
    summary="Institution-wide dashboard counters",
)
def report_summary(_admin: AdminUser, db: DbSession) -> ReportSummary:
    """Return the computed dashboard counters.

    ``average_cgpa`` and ``faculty_publication_count`` are ``null`` - the
    current schema has no grade scale and no publications table. The
    ``notes`` field explains why, so the frontend can hide those tiles
    instead of showing a misleading zero.
    """
    return admin_service.report_summary(db)


@router.get(
    "/reports/enrollments",
    response_model=EnrollmentReport,
    summary="Enrollment report per course and department",
)
def enrollment_report(_admin: AdminUser, db: DbSession) -> EnrollmentReport:
    """Return enrolment counts per course, plus a per-department breakdown."""
    return admin_service.enrollment_report(db)


@router.get(
    "/reports/attendance",
    response_model=AttendanceReport,
    summary="Attendance report per course",
)
def attendance_report(_admin: AdminUser, db: DbSession) -> AttendanceReport:
    """Return attendance rates per course.

    PRESENT and LATE both count as attended.
    """
    return admin_service.attendance_report(db)
