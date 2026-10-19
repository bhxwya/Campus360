"""
Marks / assessment results (models/mark.py).

One ``Mark`` row = one scored component (internal, midterm, theory or
practical) for one student in one course. Grade LETTERS (O/A+/A/...) are
computed from percentages at read time - storing them would duplicate state
that could contradict the scores.

Why NUMERIC(6,2) instead of float: IEEE-754 floats cannot exactly represent
many decimal fractions (0.1 + 0.2 == 0.30000000000000004). PostgreSQL
NUMERIC/DECIMAL is exact arithmetic - the right tool for anything graded or
monetary.

The CHECK(score <= max_score) constraint is enforced inside PostgreSQL, so
even a buggy application cannot record 95 out of 50.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import MarkComponent, pg_enum

if TYPE_CHECKING:  # pragma: no cover
    from app.models.course import Course
    from app.models.user import FacultyProfile, StudentProfile


class Mark(Base):
    """A single scored component for a student in a course.

    Deliberately WITHOUT TimestampMixin: rows are usually inserted once
    during result entry, but ``recorded_at`` (below) is the meaningful
    instant; an ``updated_at`` would add noise without a use case yet.
    """

    __tablename__ = "marks"

    id: Mapped[int] = mapped_column(primary_key=True)

    student_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    component: Mapped[MarkComponent] = mapped_column(pg_enum(MarkComponent), nullable=False)

    # NUMERIC(6,2): up to 99999.99 with exactly two decimals.
    score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    max_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)

    recorded_by: Mapped[int | None] = mapped_column(
        ForeignKey("faculty_profiles.id", ondelete="SET NULL"),
    )

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # The two CHECKs below are why floats are rejected at the door too:
        UniqueConstraint(
            "student_id",
            "course_id",
            "component",
            name="uq_mark_student_course_component",
        ),
        CheckConstraint("score >= 0", name="score_not_negative"),
        CheckConstraint("max_score > 0", name="max_score_positive"),
        CheckConstraint("score <= max_score", name="score_within_max"),
    )

    # --- Relationships -----------------------------------------------------
    student: Mapped[StudentProfile] = relationship()  # noqa: F821
    course: Mapped[Course] = relationship(back_populates="marks")
    recorder: Mapped[FacultyProfile | None] = relationship()  # noqa: F821
