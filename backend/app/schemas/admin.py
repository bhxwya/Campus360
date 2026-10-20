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

from app.models.enums import (
    AnnouncementAudience,
    AnnouncementCategory,
    AnnouncementStatus,
    EnrollmentStatus,
    UserRole,
)

#: Shared pagination block for every list endpoint.
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


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
    current_semester: int | None = Field(default=None, ge=1, le=12)
    phone: str | None = Field(default=None, max_length=20)


class StudentUpdate(BaseModel):
    """Body of ``PATCH /api/v1/admin/students/{id}``.

    Every field is optional; only the fields the client actually sent are
    applied (``exclude_unset``). ``None`` therefore means "not supplied"
    rather than "clear this value" - clearing a value is out of scope.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    degree_program: str | None = Field(default=None, max_length=120)
    current_semester: int | None = Field(default=None, ge=1, le=12)
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
    designation: str | None
    #: Courses this faculty leads (the "Courses Led" column of the UI).
    assigned_courses: int
    created_at: datetime
    updated_at: datetime


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
