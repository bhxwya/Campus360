"""
Student read-only endpoints (api/student.py).

Stage 7A: every route is a safe GET guarded by require_student.
All records are scoped exclusively to the authenticated student's profile.

Route map:
  Profile      GET /me/profile
               GET /me/courses
  Timetable    GET /me/timetable
  Attendance   GET /me/attendance
               GET /me/attendance/records
  Assignments  GET /me/assignments
               GET /me/assignments/{assignment_id}
  Marks        GET /me/marks
               GET /me/marks/summary
  Announce     GET /me/announcements
  Dashboard    GET /me/dashboard
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import CurrentUser, DbSession, require_student
from app.schemas.student import (
    AttendanceSummaryResponse,
    DashboardResponse,
    MarksSummaryResponse,
    MyAttendanceRecordResponse,
    MyEnrollmentResponse,
    MyTimetableEntryResponse,
    StudentAnnouncementResponse,
    StudentAssignmentResponse,
    StudentMarkResponse,
    StudentProfileResponse,
)
from app.services import student as student_service

router = APIRouter(prefix="/student", tags=["Student"])

StudentUser = Annotated[CurrentUser, Depends(require_student)]


def _fail(exc: student_service.StudentError) -> HTTPException:
    """Translate a service error into an HTTP error response."""
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get(
    "/me/profile",
    response_model=StudentProfileResponse,
    summary="Get authenticated student profile",
)
def get_profile(
    _student: StudentUser,
    db: DbSession,
) -> StudentProfileResponse:
    """Return the profile and account details for the authenticated student."""
    try:
        return student_service.get_profile(db, _student.id)
    except student_service.StudentError as exc:
        raise _fail(exc) from exc


@router.get(
    "/me/courses",
    response_model=list[MyEnrollmentResponse],
    summary="List enrolled courses",
)
def list_courses(
    _student: StudentUser,
    db: DbSession,
) -> list[MyEnrollmentResponse]:
    """Return all course enrollments for the authenticated student."""
    try:
        return student_service.list_courses(db, _student.id)
    except student_service.StudentError as exc:
        raise _fail(exc) from exc


@router.get(
    "/me/timetable",
    response_model=list[MyTimetableEntryResponse],
    summary="Get weekly timetable",
)
def get_timetable(
    _student: StudentUser,
    db: DbSession,
) -> list[MyTimetableEntryResponse]:
    """Return weekly scheduled slots for all actively enrolled courses."""
    try:
        return student_service.get_timetable(db, _student.id)
    except student_service.StudentError as exc:
        raise _fail(exc) from exc


@router.get(
    "/me/attendance",
    response_model=AttendanceSummaryResponse,
    summary="Get attendance summary",
)
def get_attendance_summary(
    _student: StudentUser,
    db: DbSession,
) -> AttendanceSummaryResponse:
    """Return overall and per-course attendance rates, handling zero sessions safely."""
    try:
        return student_service.get_attendance_summary(db, _student.id)
    except student_service.StudentError as exc:
        raise _fail(exc) from exc


@router.get(
    "/me/attendance/records",
    response_model=list[MyAttendanceRecordResponse],
    summary="List attendance session records",
)
def list_attendance_records(
    _student: StudentUser,
    db: DbSession,
    course_id: Annotated[
        int | None, Query(description="Filter records by course ID")
    ] = None,
) -> list[MyAttendanceRecordResponse]:
    """Return chronological attendance records, optionally filtered by enrolled course."""
    try:
        return student_service.list_attendance_records(
            db, _student.id, course_id=course_id
        )
    except student_service.StudentError as exc:
        raise _fail(exc) from exc


@router.get(
    "/me/assignments",
    response_model=list[StudentAssignmentResponse],
    summary="List active course assignments",
)
def list_assignments(
    _student: StudentUser,
    db: DbSession,
) -> list[StudentAssignmentResponse]:
    """Return active assignments for enrolled courses with the student's submission status."""
    try:
        return student_service.list_assignments(db, _student.id)
    except student_service.StudentError as exc:
        raise _fail(exc) from exc


@router.get(
    "/me/assignments/{assignment_id}",
    response_model=StudentAssignmentResponse,
    summary="Get assignment details",
)
def get_assignment(
    assignment_id: int,
    _student: StudentUser,
    db: DbSession,
) -> StudentAssignmentResponse:
    """Return a single assignment and any submission made by the student."""
    try:
        return student_service.get_assignment(db, _student.id, assignment_id)
    except student_service.StudentError as exc:
        raise _fail(exc) from exc


@router.get(
    "/me/marks",
    response_model=list[StudentMarkResponse],
    summary="List component marks",
)
def list_marks(
    _student: StudentUser,
    db: DbSession,
) -> list[StudentMarkResponse]:
    """Return all scored assessment component marks for the authenticated student."""
    try:
        return student_service.list_marks(db, _student.id)
    except student_service.StudentError as exc:
        raise _fail(exc) from exc


@router.get(
    "/me/marks/summary",
    response_model=MarksSummaryResponse,
    summary="Get marks summary and grades",
)
def get_marks_summary(
    _student: StudentUser,
    db: DbSession,
) -> MarksSummaryResponse:
    """Return course-level scorecards, distinguishing incomplete or missing marks."""
    try:
        return student_service.get_marks_summary(db, _student.id)
    except student_service.StudentError as exc:
        raise _fail(exc) from exc


@router.get(
    "/me/announcements",
    response_model=list[StudentAnnouncementResponse],
    summary="List student announcements",
)
def list_announcements(
    _student: StudentUser,
    db: DbSession,
) -> list[StudentAnnouncementResponse]:
    """Return active published announcements addressed to students."""
    try:
        return student_service.list_announcements(db, _student.id)
    except student_service.StudentError as exc:
        raise _fail(exc) from exc


@router.get(
    "/me/dashboard",
    response_model=DashboardResponse,
    summary="Get student dashboard data",
)
def get_dashboard(
    _student: StudentUser,
    db: DbSession,
) -> DashboardResponse:
    """Return composite metrics and recent activity for the student dashboard."""
    try:
        return student_service.get_dashboard(db, _student.id)
    except student_service.StudentError as exc:
        raise _fail(exc) from exc
