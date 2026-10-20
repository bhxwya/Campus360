"""
Assignments and student submissions (models/assignment.py).

``Assignment`` is coursework published by a faculty member for a course;
``AssignmentSubmission`` is one student's submission for it.

Two integrity rules live in the database:

* UNIQUE(assignment_id, student_id) on submissions - "submit twice" becomes
  an UPDATE of the existing row, never a second row (the UI also shows one
  submission status per student per assignment).
* CHECK due_at > created timestamps is intentionally NOT here: due dates in
  the past are legitimate (backfilling), so validation belongs in the service
  layer (Stage 4), not the schema.
* Grading consistency lives in CHECKs too: a ``submitted`` submission carries
  no grade fields, while ``graded``/``returned`` ones must carry score +
  max_score (the grading-time snapshot) + graded_at. Scores are bounded by
  that snapshot - never by a hardcoded 100 - because an assignment's
  ``max_score`` may legitimately exceed 100.

No file-upload columns on purpose: the project scope never defined a file
storage workflow, so ``content`` holds text (or a link a student pastes).
If uploads are added later, they get their own table with type/size limits.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import SubmissionStatus, pg_enum

if TYPE_CHECKING:  # pragma: no cover
    from app.models.course import Course
    from app.models.user import FacultyProfile, StudentProfile


class Assignment(TimestampMixin, Base):
    """One assignment (project, report, lab task...) attached to a course."""

    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="RESTRICT"),  # never orphan-delete
        nullable=False,
        index=True,
    )

    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("faculty_profiles.id", ondelete="SET NULL"),
        # SET NULL, not RESTRICT: if the creator's account is later removed,
        # the assignment (and its deadlines) must keep working for students.
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Deadline as a timezone-aware instant: "Friday 23:59" differs per UTC
    # offset, so a plain date/time would be ambiguous for a real campus.
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,  # "due this week" queries sort/filter on it
    )

    # Numeric(5,2) because 100.00 must be exact: FLOAT would eventually store
    # 99.99999997 - unacceptable when summing weights toward a final grade.
    weightage_percent: Mapped[float | None] = mapped_column(Numeric(5, 2))

    # Maximum attainable score on this assignment. Defaults to 100, must be
    # positive, and may be updated later - graded submissions keep the
    # max_score SNAPSHOT taken at grading time (see AssignmentSubmission),
    # so changing the scale never rewrites grades already given.
    # server_default matches migration 9d2e1ea7b2d9 (and backfills rows the
    # migration creates a default for); the Python default keeps the value
    # present on new instances before the first flush.
    max_score: Mapped[float] = mapped_column(
        Numeric(5, 2),
        default=100.0,
        server_default="100",
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        # Weightage is a percentage: 0 < w <= 100, but only when provided
        # (NULL weightages are allowed - not every assignment is graded).
        CheckConstraint(
            "weightage_percent IS NULL OR (weightage_percent > 0 AND weightage_percent <= 100)",
            name="weightage_percent_range",
        ),
        # The attainable maximum must be positive (NULL impossible: NOT NULL,
        # and the default of 100 satisfies this trivially).
        CheckConstraint("max_score > 0", name="max_score_positive"),
    )

    # --- Relationships -----------------------------------------------------
    course: Mapped[Course] = relationship(back_populates="assignments")
    creator: Mapped[FacultyProfile | None] = relationship()  # noqa: F821

    submissions: Mapped[list[AssignmentSubmission]] = relationship(
        back_populates="assignment",
        passive_deletes=True,
    )


class AssignmentSubmission(TimestampMixin, Base):
    """One student's submission for one assignment (at most one row)."""

    __tablename__ = "assignment_submissions"

    id: Mapped[int] = mapped_column(primary_key=True)

    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    student_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        # Server-side clock again (see TimestampMixin): the submission time
        # must be the database's truth, not a client's possibly-wrong clock.
    )

    content: Mapped[str | None] = mapped_column(Text)  # text answer or pasted link

    status: Mapped[SubmissionStatus] = mapped_column(
        pg_enum(SubmissionStatus),
        default=SubmissionStatus.SUBMITTED,
        nullable=False,
    )

    #: The faculty-assigned score for this submission, bounded by ``max_score``
    #: below - NOT by a hardcoded 100: an assignment's scale may allow scores
    #: above 100. Separate from the Mark table's course-level component marks -
    #: this is the grade for this specific assignment only.
    score: Mapped[float | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    #: The assignment's max_score captured when the grade was set (a
    #: snapshot): changing the assignment's maximum later never rewrites
    #: grades already given on the old scale.
    max_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)

    #: Optional grader comments shown back to the student (the API limits
    #: this to 2000 characters; the column itself stays unlimited Text).
    feedback: Mapped[str | None] = mapped_column(Text)

    #: Which faculty member graded this: FacultyProfile id (the same key as
    #: Mark.recorded_by), NOT User id. SET NULL keeps historical grades if
    #: the grader's account is later removed - which is also why graded_by
    #: appears in no CHECK constraint.
    graded_by: Mapped[int | None] = mapped_column(
        ForeignKey("faculty_profiles.id", ondelete="SET NULL"),
    )

    #: When the grade was set (database clock, like submitted_at). Present
    #: exactly when the row is graded/returned - enforced by the
    #: submission_grade_state CHECK below.
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # One submission row per student per assignment - resubmitting
        # updates this row (Stage 4).
        UniqueConstraint(
            "assignment_id",
            "student_id",
            name="uq_submission_assignment_student",
        ),
        # score must sit within 0..max_score when supplied. The explicit
        # "max_score IS NOT NULL" guard matters: a bare "score <= max_score"
        # evaluates to NULL - which PASSES a CHECK - when max_score is NULL.
        CheckConstraint(
            "score IS NULL OR (max_score IS NOT NULL AND score >= 0 AND score <= max_score)",
            name="submission_score_range",
        ),
        # The grading-time snapshot itself must be positive when present.
        CheckConstraint(
            "max_score IS NULL OR max_score > 0",
            name="submission_max_score_positive",
        ),
        # Grading state machine. The literals are the LOWERCASE values
        # PostgreSQL actually stores (pg_enum): submitted rows carry no grade
        # data; graded AND returned rows must carry score + snapshot +
        # graded_at. feedback and graded_by stay optional - graded_by is
        # SET NULL on account removal, so it must never gate row validity.
        CheckConstraint(
            "status = 'submitted' AND score IS NULL AND max_score IS NULL AND graded_at IS NULL"
            " OR status IN ('graded', 'returned') AND score IS NOT NULL AND max_score IS NOT NULL"
            " AND graded_at IS NOT NULL",
            name="submission_grade_state",
        ),
    )

    # --- Relationships -----------------------------------------------------
    assignment: Mapped[Assignment] = relationship(back_populates="submissions")
    student: Mapped[StudentProfile] = relationship()  # noqa: F821
