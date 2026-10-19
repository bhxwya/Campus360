"""
Attendance tracking (models/attendance.py).

One ``AttendanceRecord`` = one student, in one scheduled session, on one
calendar date. Percentages shown on dashboards are ALWAYS computed from these
rows (COUNT present / COUNT total); storing a "current percentage" column
would inevitably drift out of sync as new records arrive.

The UNIQUE(student_id, timetable_entry_id, attendance_date) constraint is the
key integrity rule: marking the same student twice for the same session is a
database error, protecting against double-clicks, concurrent faculty, or
replayed requests.
"""

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import AttendanceStatus, pg_enum

if TYPE_CHECKING:  # pragma: no cover
    from app.models.timetable import TimetableEntry
    from app.models.user import FacultyProfile, StudentProfile


class AttendanceRecord(TimestampMixin, Base):
    """Attendance of one student in one session instance (entry + date)."""

    __tablename__ = "attendance_records"

    id: Mapped[int] = mapped_column(primary_key=True)

    student_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="RESTRICT"),  # history pinned
        nullable=False,
        index=True,
    )

    timetable_entry_id: Mapped[int] = mapped_column(
        ForeignKey("timetable_entries.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Calendar date this record applies to. The entry defines the weekly
    # pattern; this column pins ONE occurrence of it (e.g. Monday entry on
    # 2026-10-19). Verifying the date really falls on the entry's weekday is
    # application logic (Stage 4) - PostgreSQL CHECKs cannot call date
    # functions portably.
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[AttendanceStatus] = mapped_column(pg_enum(AttendanceStatus), nullable=False)

    marked_by: Mapped[int | None] = mapped_column(
        ForeignKey("faculty_profiles.id", ondelete="SET NULL"),  # who recorded it
    )

    __table_args__ = (
        # Duplicate prevention - the module docstring's headline rule.
        UniqueConstraint(
            "student_id",
            "timetable_entry_id",
            "attendance_date",
            name="uq_attendance_student_session_date",
        ),
    )

    # --- Relationships -----------------------------------------------------
    student: Mapped[StudentProfile] = relationship(  # noqa: F821
        foreign_keys=[student_id]
    )
    timetable_entry: Mapped[TimetableEntry] = relationship(back_populates="attendance_records")
    recorder: Mapped[FacultyProfile | None] = relationship(  # noqa: F821
        foreign_keys=[marked_by]
    )
