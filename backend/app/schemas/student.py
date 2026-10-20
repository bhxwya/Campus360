"""
Student-facing request/response schemas (backend/app/schemas/student.py).

Stage 7A read-only shapes. Rules:
1. No password_hash, no session data, no other student's data.
2. Grade computation reuses compute_grade() from schemas/faculty.py.
3. Zero attendance sessions -> attendance_percent = None (never divide by zero).
4. Incomplete marks -> overall stats = None, status = "Incomplete".
"""

from datetime import date, datetime, time

from pydantic import BaseModel

from app.models.enums import (
    AnnouncementAudience,
    AnnouncementCategory,
    AttendanceStatus,
    DayOfWeek,
    EnrollmentStatus,
    MarkComponent,
    SubmissionStatus,
)

# --- Profile ------------------------------------------------------------------


class StudentProfileResponse(BaseModel):
    """Safe projection of the student's profile and account metadata."""

    id: int
    student_code: str
    full_name: str
    degree_program: str | None
    current_semester: int | None
    phone: str | None
    email: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Courses / Enrollments ----------------------------------------------------


class MyEnrollmentResponse(BaseModel):
    """An enrolled course with catalogue and lead instructor metadata."""

    enrollment_id: int
    status: EnrollmentStatus
    course_id: int
    course_code: str
    course_title: str
    credits: int
    department: str | None
    lead_faculty_name: str | None

    model_config = {"from_attributes": True}


# --- Timetable ----------------------------------------------------------------


class MyTimetableEntryResponse(BaseModel):
    """A recurring weekly class slot for an actively enrolled course."""

    id: int
    course_id: int
    course_code: str
    course_title: str
    day_of_week: DayOfWeek
    start_time: time
    end_time: time
    room: str | None
    faculty_name: str | None

    model_config = {"from_attributes": True}


# --- Attendance ---------------------------------------------------------------


class CourseAttendanceSummary(BaseModel):
    """Per-course attendance counts, percentage and status."""

    course_id: int
    course_code: str
    course_title: str
    faculty_name: str | None
    total_sessions: int
    present_count: int
    late_count: int
    absent_count: int
    attendance_percent: float | None
    status: str


class AttendanceSummaryResponse(BaseModel):
    """Overall attendance rate and per-course breakdown."""

    overall_percent: float | None
    courses: list[CourseAttendanceSummary]


class MyAttendanceRecordResponse(BaseModel):
    """One individual attendance session record."""

    id: int
    course_id: int
    course_code: str
    timetable_entry_id: int
    day_of_week: DayOfWeek
    attendance_date: date
    status: AttendanceStatus

    model_config = {"from_attributes": True}


# --- Assignments --------------------------------------------------------------


class StudentAssignmentResponse(BaseModel):
    """An assignment with course details and the student's own submission."""

    id: int
    course_id: int
    course_code: str
    course_title: str
    title: str
    description: str | None
    due_at: datetime
    weightage_percent: float | None
    max_score: float
    is_active: bool
    submission_id: int | None
    submission_status: SubmissionStatus | None
    submitted_at: datetime | None
    content: str | None
    score: float | None
    feedback: str | None
    graded_at: datetime | None

    model_config = {"from_attributes": True}


# --- Marks --------------------------------------------------------------------


class StudentMarkResponse(BaseModel):
    """A single scored assessment component for a course."""

    id: int
    course_id: int
    course_code: str
    course_title: str
    component: MarkComponent
    score: float
    max_score: float
    percentage: float
    grade: str
    grade_points: float
    recorded_at: datetime

    model_config = {"from_attributes": True}


class CourseMarkSummary(BaseModel):
    """Course-level mark aggregation with grade calculations."""

    course_id: int
    course_code: str
    course_title: str
    credits: int
    components: list[StudentMarkResponse]
    overall_percentage: float | None
    overall_grade: str | None
    overall_grade_points: float | None
    status: str


class MarksSummaryResponse(BaseModel):
    """All course mark summaries for the student."""

    courses: list[CourseMarkSummary]


# --- Announcements ------------------------------------------------------------


class StudentAnnouncementResponse(BaseModel):
    """A broadcast notice visible to students."""

    id: int
    title: str
    body: str
    category: AnnouncementCategory
    audience: AnnouncementAudience
    published_at: datetime

    model_config = {"from_attributes": True}


# --- Dashboard ----------------------------------------------------------------


class DashboardResponse(BaseModel):
    """Composite student dashboard summary."""

    enrolled_courses_count: int
    total_credits: int
    overall_attendance_percent: float | None
    upcoming_assignments: list[StudentAssignmentResponse]
    recent_announcements: list[StudentAnnouncementResponse]
