"""
Scheduled class sessions (models/timetable.py).

``TimetableEntry`` describes ONE recurring weekly slot: "CS-301 happens every
Monday 09:00-10:30 in Room 204, taught by Dr. X". A real timetable is a
PATTERN per week during a semester, not one row per calendar date - which is
also how the frontend's timetable page is laid out (day columns).

The partial unique index below is worth studying: it prevents scheduling the
SAME faculty member in two rooms at the same weekday+time, but only where a
faculty is actually assigned (WHERE faculty_id IS NOT NULL). Partial indexes
encode rules that only apply to a subset of rows - exactly the shape of
"assigned faculty can't be double-booked".
"""

from datetime import time
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Time, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import DayOfWeek, pg_enum

if TYPE_CHECKING:  # pragma: no cover
    from app.models.attendance import AttendanceRecord
    from app.models.course import Course
    from app.models.user import FacultyProfile


class TimetableEntry(TimestampMixin, Base):
    """One recurring weekly class session of a course."""

    __tablename__ = "timetable_entries"

    id: Mapped[int] = mapped_column(primary_key=True)

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),  # dies with its course
        nullable=False,
        index=True,
    )

    faculty_id: Mapped[int | None] = mapped_column(
        ForeignKey("faculty_profiles.id", ondelete="SET NULL"),  # slot survives
        index=True,
    )

    day_of_week: Mapped[DayOfWeek] = mapped_column(pg_enum(DayOfWeek), nullable=False)

    # PostgreSQL TIME - a wall-clock time-of-day, no date component, which is
    # exactly what a weekly recurring schedule stores.
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    room: Mapped[str | None] = mapped_column(String(40))  # UI "Room / Venue"

    __table_args__ = (
        # A session must END after it STARTS. (Same-day assumption is fine:
        # overnight classes are out of the project's scope.)
        CheckConstraint("end_time > start_time", name="valid_time_range"),
        # One course cannot have two identical sessions at the same slot.
        Index(
            "uq_timetable_course_slot",
            "course_id",
            "day_of_week",
            "start_time",
            unique=True,
        ),
        # The double-booking guard described in the module docstring.
        # postgresql_where takes a SQL expression (text() wraps raw SQL) and
        # renders as: CREATE UNIQUE INDEX ... WHERE faculty_id IS NOT NULL.
        Index(
            "uq_timetable_faculty_slot",
            "faculty_id",
            "day_of_week",
            "start_time",
            unique=True,
            postgresql_where=text("faculty_id IS NOT NULL"),
        ),
        # Reads like "all Monday sessions" or "Dr. X's weekly schedule".
        Index("ix_timetable_day", "day_of_week", "start_time"),
    )

    # --- Relationships -----------------------------------------------------
    course: Mapped[Course] = relationship(back_populates="timetable_entries")
    faculty: Mapped[FacultyProfile | None] = relationship()  # noqa: F821

    attendance_records: Mapped[list[AttendanceRecord]] = relationship(  # noqa: F821
        back_populates="timetable_entry",
        passive_deletes=True,  # RESTRICT via the FK: attendance pins the entry
    )
