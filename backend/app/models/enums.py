"""
Shared enumerations for fixed value sets (models/enums.py).

PostgreSQL supports native ENUM types, and SQLAlchemy maps Python ``enum``
classes onto them. Why enums instead of free text? A typo like "abent" in a
status column would silently corrupt data an application-level string would
accept. With an enum, the DATABASE rejects any value outside the set, and the
allowed values are documented in one place in code.

All enums are ``StrEnum`` subclasses, so their values behave like ordinary
strings in Python (easy comparisons, JSON serialization) while still being
enums. The migration will create one PostgreSQL type per enum
(e.g. ``CREATE TYPE userrole AS ENUM ('student', ...)``).
"""

import enum

from sqlalchemy import Enum as sa_Enum


def pg_enum(enum_cls: type[enum.StrEnum]) -> sa_Enum:
    """Build a native PostgreSQL enum type that stores member VALUES.

    By default SQLAlchemy stores the member NAME (``'STUDENT'``, ``'MONDAY'``)
    in the database. Campus360 stores the VALUE instead (``'student'``,
    ``'Monday'``) so that:

    * raw SQL / pgAdmin browsing shows exactly the strings the API also uses;
    * the values documented on each enum below are the ones actually persisted.

    ``values_callable`` is the documented hook for this; without it, names and
    values silently diverge whenever a member's value differs from its name.
    """
    return sa_Enum(enum_cls, values_callable=lambda cls: [member.value for member in cls])


class UserRole(enum.StrEnum):
    """Who a User account is. Drives ALL authorization decisions (Stage 3+)."""

    STUDENT = "student"
    FACULTY = "faculty"
    ADMIN = "admin"


class AttendanceStatus(enum.StrEnum):
    """Possible outcomes of one class session for one student.

    Grounded in the existing frontend, which displays exactly these three
    states as status badges on the attendance pages.
    """

    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"


class DayOfWeek(enum.StrEnum):
    """Weekday a recurring timetable entry runs on.

    Stored as the full English name (matching how the frontend timetable
    groups its columns) rather than 0-6 integers, which are unreadable in
    raw SQL psql/pgAdmin browsing.
    """

    MONDAY = "Monday"
    TUESDAY = "Tuesday"
    WEDNESDAY = "Wednesday"
    THURSDAY = "Thursday"
    FRIDAY = "Friday"
    SATURDAY = "Saturday"


class MarkComponent(enum.StrEnum):
    """Which assessment component a Mark row scores.

    Mirrors the existing marks tables: the student results page shows
    Internal-1 / Mid-Term / Lab-Practical, and the faculty page shows
    Theory / Practical. One row per (student, course, component).
    """

    INTERNAL = "internal"
    MIDTERM = "midterm"
    THEORY = "theory"
    PRACTICAL = "practical"


class AnnouncementCategory(enum.StrEnum):
    """Notice categories, taken verbatim from the Admin > Notices page."""

    COURSE_NOTICE = "course_notice"
    ADMINISTRATIVE = "administrative"
    EVENT = "event"
    EXAMINATION = "examination"
    IT_SERVICES = "it_services"
    RESEARCH = "research"


class AnnouncementAudience(enum.StrEnum):
    """Who should see an announcement (the UI's "Target Audience" column)."""

    ALL = "all"
    STUDENTS = "students"
    FACULTY = "faculty"
    ADMIN = "admin"


class AnnouncementStatus(enum.StrEnum):
    """Publication lifecycle. 'Archived' hides a notice without deleting it,
    preserving history (matching the Active/Archived badges in the UI)."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class EnrollmentStatus(enum.StrEnum):
    """Lifecycle of a student's enrollment in a course.

    Kept from day one because dropping a course must NOT delete the
    enrollment row (attendance and marks reference it historically) - the
    status simply changes.
    """

    ACTIVE = "active"
    DROPPED = "dropped"
    COMPLETED = "completed"


class SubmissionStatus(enum.StrEnum):
    """Grading lifecycle of an assignment submission."""

    SUBMITTED = "submitted"
    GRADED = "graded"
    RETURNED = "returned"
