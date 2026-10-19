"""ORM models package (backend/app/models).

This module is the SINGLE import point for every SQLAlchemy model. Importing
it registers all tables on ``Base.metadata``, which both Alembic's
autogeneration (alembic/env.py does ``from app import models``) and the
relationship() string-resolving machinery rely on.

Layout (one file per aggregate, enums shared):

    enums.py         all fixed-value enums (roles, statuses, ...)
    user.py          User, StudentProfile, FacultyProfile
    course.py        Course, Enrollment
    timetable.py     TimetableEntry
    attendance.py    AttendanceRecord
    assignment.py    Assignment, AssignmentSubmission
    mark.py          Mark
    announcement.py  Announcement
"""

from app.models.announcement import Announcement
from app.models.assignment import Assignment, AssignmentSubmission
from app.models.attendance import AttendanceRecord
from app.models.course import Course, Enrollment
from app.models.enums import (
    AnnouncementAudience,
    AnnouncementCategory,
    AnnouncementStatus,
    AttendanceStatus,
    DayOfWeek,
    EnrollmentStatus,
    MarkComponent,
    SubmissionStatus,
    UserRole,
)
from app.models.mark import Mark
from app.models.timetable import TimetableEntry
from app.models.user import FacultyProfile, StudentProfile, User

__all__ = [
    "Announcement",
    "AnnouncementAudience",
    "AnnouncementCategory",
    "AnnouncementStatus",
    "Assignment",
    "AssignmentSubmission",
    "AttendanceRecord",
    "AttendanceStatus",
    "Course",
    "DayOfWeek",
    "Enrollment",
    "EnrollmentStatus",
    "FacultyProfile",
    "Mark",
    "MarkComponent",
    "StudentProfile",
    "SubmissionStatus",
    "TimetableEntry",
    "User",
    "UserRole",
]
