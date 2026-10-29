"""
Faculty request/response schemas (backend/app/schemas/faculty.py).

Stage 5 shapes for the faculty API. Three rules that mirror the admin schemas:

1. Field lengths AND numeric ranges mirror the underlying COLUMNS - oversized
   input fails with a clean 422 at the edge instead of a database error
   mid-transaction. Assignment ``max_score`` caps at 999.99 (the
   ``NUMERIC(5,2)`` column capacity) and ``weightage_percent`` must be
   strictly positive (the database CHECK is ``> 0``, not ``>= 0``).
2. Marks are percentages (0-100) on input and output. Submission scores are
   different: they are bounded by the ASSIGNMENT's max_score, validated in
   the service layer against the live assignment (so scales above 100 work);
   the schema only rejects negatives, mirroring the database CHECK.
3. Grading may only move a submission to GRADED or RETURNED - both mean "a
   grade exists". The validator below refuses 'submitted', matching the
   submission_grade_state database CHECK.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import (
    AttendanceStatus,
    MarkComponent,
    SubmissionStatus,
)

# --- Shared grade scale (institutional policy) ---------------------------------

PASS_MARK_PERCENT = 40.0

#: (threshold_percent, letter_letter, grade_points) in descending order.
GRADE_BOUNDARIES: list[tuple[float, str, float]] = [
    (90.0, "O", 10.0),
    (80.0, "A+", 9.0),
    (70.0, "A", 8.0),
    (60.0, "B+", 7.0),
    (50.0, "B", 6.0),
    (40.0, "C", 5.0),
    (0.0, "F", 0.0),
]


def compute_grade(percentage: float) -> tuple[str, float]:
    """Return ``(letter_grade, grade_points)`` for a percentage score."""
    for threshold, letter, points in GRADE_BOUNDARIES:
        if percentage >= threshold:
            return letter, points
    return "F", 0.0


# --- Assignments --------------------------------------------------------------


class AssignmentCreate(BaseModel):
    """Body of ``POST /api/v1/faculty/assignments``."""

    course_id: int
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_at: datetime
    weightage_percent: float | None = Field(default=None, gt=0, le=100)
    max_score: float = Field(
        default=100.0,
        gt=0,
        le=999.99,
        description=(
            "Maximum attainable score (default 100; may exceed 100, up to "
            "999.99 - the NUMERIC(5,2) column capacity)"
        ),
    )


class AssignmentUpdate(BaseModel):
    """Body of ``PATCH /api/v1/faculty/assignments/{id}`` (partial update)."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_at: datetime | None = None
    weightage_percent: float | None = Field(default=None, gt=0, le=100)
    max_score: float | None = Field(default=None, gt=0, le=999.99)
    is_active: bool | None = None


class AssignmentResponse(BaseModel):
    """An assignment as the faculty UI sees it."""

    id: int
    course_id: int
    title: str
    description: str | None
    due_at: datetime
    weightage_percent: float | None
    max_score: float
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Submissions --------------------------------------------------------------


class AssignmentSubmissionResponse(BaseModel):
    """One submission with student details and (optional) grading fields."""

    id: int
    assignment_id: int
    student_id: int
    student_code: str
    student_name: str
    submitted_at: datetime
    content: str | None
    status: SubmissionStatus
    score: float | None
    max_score: float | None
    feedback: str | None
    graded_by: int | None
    graded_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SubmissionGrade(BaseModel):
    """Body of ``POST /api/v1/faculty/submissions/{id}/grade``.

    ``score`` has no upper bound here: the service validates it against the
    assignment's max_score (422 when exceeded) and the database rechecks it
    against the stored snapshot. ``feedback`` is optional, capped at 2000
    characters at the API edge (the column itself is unlimited Text).
    """

    score: float = Field(
        ge=0,
        description="Score obtained; must not exceed the assignment's max_score",
    )
    feedback: str | None = Field(default=None, max_length=2000)
    status: SubmissionStatus = SubmissionStatus.GRADED

    @field_validator("status")
    @classmethod
    def _status_must_indicate_a_grade(cls, value: SubmissionStatus) -> SubmissionStatus:
        """GRADED/RETURNED only: a 'submitted' row may carry no grade data."""
        if value not in (SubmissionStatus.GRADED, SubmissionStatus.RETURNED):
            raise ValueError("status must be 'graded' or 'returned'")
        return value


# --- Marks --------------------------------------------------------------------


class MarkCreate(BaseModel):
    """Body of ``POST /api/v1/faculty/marks``."""

    student_id: int
    course_id: int
    component: MarkComponent
    score: float = Field(ge=0, le=100, description="Score obtained")
    max_score: float = Field(gt=0, le=100, description="Maximum possible score")


class MarkUpdate(BaseModel):
    """Body of ``PATCH /api/v1/faculty/marks/{id}`` (partial update).

    Omitted fields stay unchanged. Explicit nulls for the NOT NULL columns
    (score, max_score) and a score above the resulting max_score are rejected
    by the SERVICE with 422 - the schema cannot see the stored row, so the
    cross-field check must happen where the mark is loaded.
    """

    score: float | None = Field(default=None, ge=0, le=100)
    max_score: float | None = Field(default=None, gt=0, le=100)


class MarkResponse(BaseModel):
    """A mark as the faculty UI sees it, including computed percentage/grade."""

    id: int
    student_id: int
    course_id: int
    component: MarkComponent
    score: float
    max_score: float
    percentage: float
    grade: str
    grade_points: float
    recorded_by: int | None
    recorded_at: datetime

    model_config = {"from_attributes": True}


# --- Attendance (atomic batch) ------------------------------------------------


class AttendanceBatchItem(BaseModel):
    """One student's attendance status within a batch."""

    student_id: int
    status: AttendanceStatus


class AttendanceBatchRequest(BaseModel):
    """Body of ``POST /api/v1/faculty/attendance/batch``.

    All records are written atomically - either every record is inserted or
    none are. If ANY record duplicates an existing
    (student_id, timetable_entry_id, attendance_date) tuple, the WHOLE batch
    fails with 409.
    """

    timetable_entry_id: int
    attendance_date: date
    records: list[AttendanceBatchItem] = Field(min_length=1, max_length=200)

    @field_validator("records")
    @classmethod
    def _one_record_per_student(cls, records: list[AttendanceBatchItem]):
        """A student appears at most once per batch (one status per session)."""
        ids = [record.student_id for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("each student may appear only once in an attendance batch")
        return records


class AttendanceBatchResponse(BaseModel):
    """Result of an atomic attendance batch write."""

    marked_count: int
    skipped_duplicate_student_ids: list[int] = Field(default_factory=list)
