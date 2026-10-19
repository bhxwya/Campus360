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

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        # Weightage is a percentage: 0 < w <= 100, but only when provided
        # (NULL weightages are allowed - not every assignment is graded).
        CheckConstraint(
            "weightage_percent IS NULL OR (weightage_percent > 0 AND weightage_percent <= 100)",
            name="weightage_percent_range",
        ),
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

    __table_args__ = (
        # One submission row per student per assignment - resubmitting
        # updates this row (Stage 4).
        UniqueConstraint(
            "assignment_id",
            "student_id",
            name="uq_submission_assignment_student",
        ),
    )

    # --- Relationships -----------------------------------------------------
    assignment: Mapped[Assignment] = relationship(back_populates="submissions")
    student: Mapped[StudentProfile] = relationship()  # noqa: F821
