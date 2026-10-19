"""
Courses and enrollments (models/course.py).

``Course`` is a catalogue entry (e.g. "CS-301 Advanced Data Structures").
``Enrollment`` is the many-to-many LINK between students and courses: one
student enrolls in many courses, one course has many students. Link tables
like this are the standard relational way to express "many-to-many" - you
cannot store a list of course ids inside a student row (or vice versa)
without breaking first normal form.

Deletion policy: enrollments RESTRICT course deletion. A course that was
ever taught has attendance, marks and submissions pointing at it; the honest
operations are "deactivate" (is_active=False) - the same pattern the admin
UI's Active/Inactive badges imply.
"""

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import EnrollmentStatus, pg_enum

if TYPE_CHECKING:  # pragma: no cover
    from app.models.assignment import Assignment
    from app.models.mark import Mark
    from app.models.timetable import TimetableEntry


class Course(TimestampMixin, Base):
    """One course in the academic catalogue."""

    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)

    code: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        unique=True,  # "CS-301" identifies one course; duplicates are data bugs
        index=True,  # the UI searches/filters by code constantly
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    credits: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    department: Mapped[str | None] = mapped_column(String(120))  # UI "Department"

    is_active: Mapped[bool] = mapped_column(default=True)  # catalogue visibility

    lead_faculty_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "faculty_profiles.id",
            # If the faculty profile disappears, the course REMAINS - it just
            # loses its lead (the admin UI can reassign). Never cascade a
            # course away because an instructor left.
            ondelete="SET NULL",
        )
    )

    __table_args__ = (
        # Table-level CHECK: credits must be a positive whole number. Named
        # deterministically by the metadata convention as
        # ck_courses_credits_positive (see db/base.py NAMING_CONVENTION).
        CheckConstraint("credits > 0", name="credits_positive"),
    )

    # --- Relationships -----------------------------------------------------
    lead_faculty: Mapped[FacultyProfile | None] = relationship(  # noqa: F821
        back_populates="led_courses",
        foreign_keys=[lead_faculty_id],
    )

    enrollments: Mapped[list[Enrollment]] = relationship(
        back_populates="course",
        passive_deletes=True,  # RESTRICT handled by the FK itself
    )

    timetable_entries: Mapped[list[TimetableEntry]] = relationship(  # noqa: F821
        back_populates="course",
        cascade="all, delete-orphan",
        passive_deletes=True,  # entries die with their course (PostgreSQL side)
    )

    assignments: Mapped[list[Assignment]] = relationship(  # noqa: F821
        back_populates="course",
        passive_deletes=True,
    )

    marks: Mapped[list[Mark]] = relationship(  # noqa: F821
        back_populates="course",
        passive_deletes=True,
    )


class Enrollment(TimestampMixin, Base):
    """A student's registration in a course.

    The UNIQUE(student_id, course_id) constraint is the heart of this table:
    it makes duplicate enrollment a database-level impossibility, not merely
    an application check that a forgotten code path could bypass.
    """

    __tablename__ = "enrollments"

    id: Mapped[int] = mapped_column(primary_key=True)

    student_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,  # "my courses" queries start from the student
    )

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,  # "who is enrolled" queries start from the course
    )

    status: Mapped[EnrollmentStatus] = mapped_column(
        pg_enum(EnrollmentStatus),
        default=EnrollmentStatus.ACTIVE,
        nullable=False,
    )

    __table_args__ = (
        # THE core rule of this table: one row per (student, course) pair.
        # Duplicate enrollment becomes a database-level impossibility, not
        # merely an application check a forgotten code path could bypass.
        UniqueConstraint("student_id", "course_id", name="uq_enrollment_student_course"),
    )

    # --- Relationships -----------------------------------------------------
    student: Mapped[StudentProfile] = relationship(  # noqa: F821
        back_populates="enrollments"
    )
    course: Mapped[Course] = relationship(back_populates="enrollments")
