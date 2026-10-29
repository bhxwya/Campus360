"""
Admin request/response schemas (backend/app/schemas/admin.py).

Stage 4 shapes for the administrative API. Two rules govern this module:

1. NOTHING here declares ``password_hash``. The response models are explicit
   "safe projections" - the hash simply is not among their fields, so it can
   never leak through a response by accident (same contract as
   ``schemas/auth.py``).

2. Field lengths mirror the underlying COLUMNS (``String(32)`` for
   ``student_code``, ``String(200)`` for a notice title, ...). Oversized input
   therefore fails with a clean 422 at the edge instead of a database error
   halfway through a transaction.
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.core.programmes import SEMESTER_POLICY
from app.models.enums import (
    AnnouncementAudience,
    AnnouncementCategory,
    AnnouncementStatus,
    EnrollmentStatus,
    UserRole,
)


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------
class StudentCreate(BaseModel):
    """Body of ``POST /api/v1/admin/students``.

    Creating a student is a TWO-TABLE write (a login account plus its
    profile); the service performs both in one transaction so a failure
    never leaves an orphan account behind.
    """

    email: EmailStr
    #: Minimum length mirrors the provisioning CLI (8 characters).
    password: str = Field(min_length=8, max_length=128)
    student_code: str = Field(min_length=1, max_length=32)
    full_name: str = Field(min_length=1, max_length=120)
    degree_program: str | None = Field(default=None, max_length=120)
    current_semester: int | None = Field(
        default=None,
        ge=SEMESTER_POLICY.minimum,
        le=SEMESTER_POLICY.maximum,
    )
    phone: str | None = Field(default=None, max_length=20)


class StudentUpdate(BaseModel):
    """Body of ``PATCH /api/v1/admin/students/{id}``.

    Every field is optional; only the fields the client actually sent are
    applied (``exclude_unset``). ``None`` therefore means "not supplied"
    rather than "clear this value" - clearing a value is out of scope.

    ``student_code`` is the database's unique roll number. It is editable so
    the admin directory can correct a typo without deleting and recreating the
    student; a duplicate is rejected by the UNIQUE constraint (409).

    ``email`` is the LINKED LOGIN address: the student profile has no email of
    its own, so a change here updates ``users.email`` / ``users.email_normalized``
    on the same account (the student id and all academic rows are untouched).
    Duplicates are rejected case-insensitively (409).
    """

    email: EmailStr | None = Field(default=None, max_length=255)
    student_code: str | None = Field(default=None, min_length=1, max_length=32)
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    degree_program: str | None = Field(default=None, max_length=120)
    current_semester: int | None = Field(
        default=None,
        ge=SEMESTER_POLICY.minimum,
        le=SEMESTER_POLICY.maximum,
    )
    phone: str | None = Field(default=None, max_length=20)
    is_active: bool | None = None


class StudentResponse(BaseModel):
    """A student as the admin UI sees it. Never contains the password hash."""

    id: int
    user_id: int
    email: str
    role: UserRole
    is_active: bool
    student_code: str
    full_name: str
    degree_program: str | None
    #: Stable catalogue code for ``degree_program`` when it resolves to a known
    #: programme (``None`` for a legacy/uncatalogued value). This lets clients
    #: select/programme-filter without comparing display text.
    programme_code: str | None = None
    current_semester: int | None
    phone: str | None
    #: Computed at read time from ``attendance_records`` - never stored.
    attendance_percentage: float | None
    enrolled_courses: int
    created_at: datetime
    updated_at: datetime


class StudentListResponse(BaseModel):
    """Paginated student list."""

    items: list[StudentResponse]
    total: int
    limit: int
    offset: int


class ProgrammeResponse(BaseModel):
    """One academic programme (branch/degree), NOT an individual course."""

    code: str
    name: str
    group: str


class FacultyListResponse(BaseModel):
    """Paginated faculty list."""

    items: list[FacultyResponse]
    total: int
    limit: int
    offset: int


class FacultyCreate(BaseModel):
    """Body of ``POST /api/v1/admin/faculty``."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    faculty_code: str = Field(min_length=1, max_length=32)
    full_name: str = Field(min_length=1, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    designation: str | None = Field(default=None, max_length=120)


class FacultyUpdate(BaseModel):
    """Body of ``PATCH /api/v1/admin/faculty/{id}``."""

    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    designation: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None


class FacultyResponse(BaseModel):
    """A faculty member as the admin UI sees it."""

    id: int
    user_id: int
    email: str
    role: UserRole
    is_active: bool
    faculty_code: str
    full_name: str
    department: str | None
    #: Stable catalogue code for ``department`` when it resolves to a known
    #: department (``None`` for a legacy/uncatalogued value).
    department_code: str | None = None
    designation: str | None
    #: Courses this faculty leads (the "Courses Led" column of the UI).
    assigned_courses: int
    created_at: datetime
    updated_at: datetime


class DepartmentResponse(BaseModel):
    """One academic department in the institutional catalogue."""

    code: str
    name: str


# ---------------------------------------------------------------------------
# Courses & enrollments
# ---------------------------------------------------------------------------
class CourseCreate(BaseModel):
    """Body of ``POST /api/v1/admin/courses``.

    ``credits`` mirrors the database CHECK ``credits > 0``: the schema
    rejects a non-positive value with 422 before the database has to.
    """

    code: str = Field(min_length=1, max_length=16)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    credits: int = Field(ge=1)
    department: str | None = Field(default=None, max_length=120)
    lead_faculty_id: int | None = None
    is_active: bool = True


class CourseUpdate(BaseModel):
    """Body of ``PATCH /api/v1/admin/courses/{id}``."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    credits: int | None = Field(default=None, ge=1)
    department: str | None = Field(default=None, max_length=120)
    lead_faculty_id: int | None = None
    is_active: bool | None = None


class CourseResponse(BaseModel):
    """A course as the admin UI sees it."""

    id: int
    code: str
    title: str
    description: str | None
    credits: int
    department: str | None
    is_active: bool
    lead_faculty_id: int | None
    lead_faculty_name: str | None
    enrolled_students: int
    created_at: datetime
    updated_at: datetime


class CourseListResponse(BaseModel):
    """Paginated course list."""

    items: list[CourseResponse]
    total: int
    limit: int
    offset: int


class EnrollmentCreate(BaseModel):
    """Body of ``POST /api/v1/admin/enrollments``."""

    #: The STUDENT PROFILE id (not the user id) - the FK the database uses.
    student_id: int
    course_id: int
    status: EnrollmentStatus = EnrollmentStatus.ACTIVE


class EnrollmentResponse(BaseModel):
    """One enrollment row."""

    id: int
    student_id: int
    student_code: str
    student_name: str
    course_id: int
    course_code: str
    course_title: str
    status: EnrollmentStatus
    created_at: datetime


# ---------------------------------------------------------------------------
# Announcements / notices
# ---------------------------------------------------------------------------
class AnnouncementCreate(BaseModel):
    """Body of ``POST /api/v1/admin/announcements``.

    ``created_by`` is NOT part of this schema: the author is taken from the
    authenticated session, never from the request body. A client cannot
    forge authorship by sending a different id.
    """

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    category: AnnouncementCategory
    audience: AnnouncementAudience = AnnouncementAudience.ALL
    status: AnnouncementStatus = AnnouncementStatus.ACTIVE
    #: Optional explicit publication instant. When omitted and the status is
    #: ACTIVE, the service stamps the current time (see services/admin.py).
    published_at: datetime | None = None


class AnnouncementUpdate(BaseModel):
    """Body of ``PATCH /api/v1/admin/announcements/{id}``."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1)
    category: AnnouncementCategory | None = None
    audience: AnnouncementAudience | None = None


class AnnouncementResponse(BaseModel):
    """A notice as the admin UI sees it."""

    id: int
    title: str
    body: str
    category: AnnouncementCategory
    audience: AnnouncementAudience
    status: AnnouncementStatus
    created_by: int | None
    author_email: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AnnouncementListResponse(BaseModel):
    """Paginated notice list."""

    items: list[AnnouncementResponse]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
class ReportSummary(BaseModel):
    """Institution-wide counters for the admin dashboard and reports page.

    Every number here is COMPUTED at read time with SQL aggregates - nothing
    is stored, so a report can never drift out of sync with the rows it
    describes.

    ``average_cgpa`` and ``faculty_publication_count`` are ``None`` on
    purpose: the current schema has no grade scale and no publications
    table, so there is no honest source for them. Inventing a number would
    be worse than admitting the gap - the frontend can hide those tiles
    until a later stage adds the data.
    """

    total_students: int
    active_students: int
    total_faculty: int
    active_faculty: int
    total_courses: int
    active_courses: int
    active_sessions: int
    total_enrollments: int
    #: Percentage of attendance records that were PRESENT or LATE.
    institution_attendance_rate: float | None
    #: Percentage of graded students at or above the pass mark.
    pass_percentage: float | None
    #: Always None - no grade scale exists in the schema.
    average_cgpa: float | None = None
    #: Always None - there is no publications table.
    faculty_publication_count: int | None = None
    #: Explains the two ``None`` fields above, so a client can show a reason.
    notes: list[str] = Field(default_factory=list)


class EnrollmentReportRow(BaseModel):
    """One row of the enrolment report: students per course."""

    course_id: int
    course_code: str
    course_title: str
    department: str | None
    enrolled_students: int


class EnrollmentReport(BaseModel):
    """Enrolment counts per course (and per department)."""

    total_enrollments: int
    courses: list[EnrollmentReportRow]
    by_department: dict[str, int]


class AttendanceReportRow(BaseModel):
    """One row of the attendance report: attendance rate per course."""

    course_id: int
    course_code: str
    course_title: str
    total_records: int
    present_count: int
    late_count: int
    absent_count: int
    attendance_percentage: float | None


class AttendanceReport(BaseModel):
    """Attendance rates per course, computed from ``attendance_records``."""

    total_records: int
    institution_attendance_rate: float | None
    courses: list[AttendanceReportRow]


# ---------------------------------------------------------------------------
# Bulk student import
# ---------------------------------------------------------------------------
class ImportRowPreview(BaseModel):
    """Per-row validation result for the import preview step."""

    row_number: int
    email: str
    student_code: str
    full_name: str
    degree_program: str | None = None
    current_semester: int | None = None
    phone: str | None = None
    status: str  # "valid" | "failed"
    errors: list[str] = Field(default_factory=list)


class ImportUploadRequest(BaseModel):
    """Body of ``POST /admin/students/import``.

    ``csv_content`` is the raw UTF-8 CSV text (the frontend reads the uploaded
    file with FileReader and sends it as a string, keeping the same JSON API
    client and CSRF token handling used everywhere else).
    """

    csv_content: str


class ImportPreviewResponse(BaseModel):
    """Result of validating an import CSV (no writes have occurred)."""

    total_rows: int
    valid_rows: int
    invalid_rows: int
    rows: list[ImportRowPreview]
    file_error: str | None = None


class ImportRowConfirm(BaseModel):
    """One validated row submitted for confirmation."""

    row_number: int | None = None
    email: str = Field(min_length=1, max_length=255)
    student_code: str = Field(min_length=1, max_length=32)
    full_name: str = Field(min_length=1, max_length=120)
    degree_program: str | None = Field(default=None, max_length=120)
    current_semester: int | None = None
    phone: str | None = Field(default=None, max_length=20)


class ImportConfirmRequest(BaseModel):
    """Body of ``POST /admin/students/import/confirm``."""

    rows: list[ImportRowConfirm] = Field(default_factory=list, max_length=500)


class ImportRowResult(BaseModel):
    """Per-row outcome of confirming an import."""

    row_number: int
    email: str
    student_code: str
    full_name: str
    status: str  # "created" | "skipped" | "failed"
    errors: list[str] = Field(default_factory=list)
    user_id: int | None = None
    profile_id: int | None = None
    email_sent: bool = False
    email_error: str | None = None


class ImportConfirmResponse(BaseModel):
    """Result of confirming an import (writes have occurred)."""

    created: int
    skipped: int
    failed: int
    email_sent: int
    email_failed: int
    rows: list[ImportRowResult]


# Bulk faculty import
class FacultyImportRowPreview(BaseModel):
    row_number: int
    email: str
    faculty_code: str
    full_name: str
    department: str | None = None
    designation: str | None = None
    status: str
    errors: list[str] = Field(default_factory=list)


class FacultyImportUploadRequest(BaseModel):
    csv_content: str


class FacultyImportPreviewResponse(BaseModel):
    total_rows: int
    valid_rows: int
    invalid_rows: int
    rows: list[FacultyImportRowPreview]
    file_error: str | None = None


class FacultyImportRowConfirm(BaseModel):
    row_number: int | None = None
    email: str = Field(min_length=1, max_length=255)
    faculty_code: str = Field(min_length=1, max_length=32)
    full_name: str = Field(min_length=1, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    designation: str | None = Field(default=None, max_length=120)


class FacultyImportConfirmRequest(BaseModel):
    rows: list[FacultyImportRowConfirm] = Field(default_factory=list, max_length=500)


class FacultyImportRowResult(BaseModel):
    row_number: int
    email: str
    faculty_code: str
    full_name: str
    status: str
    errors: list[str] = Field(default_factory=list)
    user_id: int | None = None
    profile_id: int | None = None
    email_sent: bool = False
    email_error: str | None = None


class FacultyImportConfirmResponse(BaseModel):
    created: int
    skipped: int
    failed: int
    email_sent: int
    email_failed: int
    rows: list[FacultyImportRowResult]
