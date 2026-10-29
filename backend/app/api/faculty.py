"""
Faculty endpoints (api/faculty.py).

Stage 5's faculty API, mounted under ``/api/v1/faculty``. Every route is
guarded by ``require_faculty`` (server-side role check) and every UNSAFE
route is also guarded by ``require_csrf`` (synchronizer token). Additionally,
every course-scoped operation validates that the calling faculty member is
the ``lead_faculty_id`` of that course - substitutes have NO implicit access.

Route map:

  Assignments  GET/POST /assignments, GET/PATCH/DELETE /assignments/{id}
  Submissions  GET /assignments/{id}/submissions,
               POST /submissions/{id}/grade
  Marks        GET/POST /marks, PATCH /marks/{id}
  Attendance   POST /attendance/batch

The router stays thin: it maps HTTP to service calls and turns the service's
``FacultyError`` into a response. No business logic lives here.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession, require_csrf, require_faculty
from app.schemas.faculty import (
    AssignmentCreate,
    AssignmentResponse,
    AssignmentSubmissionResponse,
    AssignmentUpdate,
    AttendanceBatchRequest,
    AttendanceBatchResponse,
    MarkComponent,
    MarkCreate,
    MarkResponse,
    MarkUpdate,
    SubmissionGrade,
)
from app.services import faculty as faculty_service

router = APIRouter(prefix="/faculty", tags=["Faculty"])

#: The authenticated faculty member. Using it as a dependency enforces the
#: role check on EVERY route in this router.
FacultyUser = Annotated[CurrentUser, Depends(require_faculty)]
#: CSRF guard for unsafe (state-changing) requests.
CsrfGuard = Annotated[None, Depends(require_csrf)]


def _fail(exc: faculty_service.FacultyError) -> HTTPException:
    """Translate a service error into an HTTP error response."""
    return HTTPException(status_code=exc.status_code, detail=exc.message)


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------


@router.get(
    "/assignments",
    response_model=list[AssignmentResponse],
    summary="List assignments for courses this faculty leads",
)
def list_assignments(
    _faculty: FacultyUser,
    db: DbSession,
    course_id: Annotated[int | None, Query(description="Filter by course id")] = None,
    is_active: Annotated[bool | None, Query(description="Filter by active flag")] = None,
) -> list[AssignmentResponse]:
    """Return all assignments for courses led by the authenticated faculty."""
    try:
        return faculty_service.list_assignments(
            db, _faculty.id, course_id=course_id, is_active=is_active
        )
    except faculty_service.FacultyError as exc:
        raise _fail(exc) from exc


@router.post(
    "/assignments",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an assignment for a led course",
)
def create_assignment(
    payload: AssignmentCreate,
    _faculty: FacultyUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> AssignmentResponse:
    """Create an assignment. The course must be led by the caller."""
    try:
        return faculty_service.create_assignment(db, _faculty.id, payload)
    except faculty_service.FacultyError as exc:
        raise _fail(exc) from exc


@router.get(
    "/assignments/{assignment_id}",
    response_model=AssignmentResponse,
    summary="Fetch one assignment (owned course only)",
)
def get_assignment(
    assignment_id: int,
    _faculty: FacultyUser,
    db: DbSession,
) -> AssignmentResponse:
    """Fetch an assignment, 403 if the faculty does not lead its course."""
    try:
        return faculty_service.get_assignment(db, _faculty.id, assignment_id)
    except faculty_service.FacultyError as exc:
        raise _fail(exc) from exc


@router.patch(
    "/assignments/{assignment_id}",
    response_model=AssignmentResponse,
    summary="Update an assignment (owned course only)",
)
def update_assignment(
    assignment_id: int,
    payload: AssignmentUpdate,
    _faculty: FacultyUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> AssignmentResponse:
    """Partially update an assignment's fields."""
    try:
        return faculty_service.update_assignment(db, _faculty.id, assignment_id, payload)
    except faculty_service.FacultyError as exc:
        raise _fail(exc) from exc


@router.delete(
    "/assignments/{assignment_id}",
    response_model=AssignmentResponse,
    summary="Deactivate an assignment (owned course only)",
)
def deactivate_assignment(
    assignment_id: int,
    _faculty: FacultyUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> AssignmentResponse:
    """Deactivate (never delete) an assignment, keeping its submissions."""
    try:
        return faculty_service.deactivate_assignment(db, _faculty.id, assignment_id)
    except faculty_service.FacultyError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------


@router.get(
    "/assignments/{assignment_id}/submissions",
    response_model=list[AssignmentSubmissionResponse],
    summary="List submissions for an owned assignment",
)
def list_submissions(
    assignment_id: int,
    _faculty: FacultyUser,
    db: DbSession,
) -> list[AssignmentSubmissionResponse]:
    """Return every submission for an assignment the faculty leads."""
    try:
        return faculty_service.list_submissions(db, _faculty.id, assignment_id)
    except faculty_service.FacultyError as exc:
        raise _fail(exc) from exc


@router.post(
    "/submissions/{submission_id}/grade",
    response_model=AssignmentSubmissionResponse,
    summary="Grade a submission (score, feedback, status)",
)
def grade_submission(
    submission_id: int,
    payload: SubmissionGrade,
    _faculty: FacultyUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> AssignmentSubmissionResponse:
    """Set score, feedback and status on a submission for an owned assignment.

    The score must not exceed the assignment's max_score (422 otherwise);
    grading also records the max_score snapshot, the grader's faculty
    profile id and the grading timestamp.
    """
    try:
        return faculty_service.grade_submission(db, _faculty.id, submission_id, payload)
    except faculty_service.FacultyError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------


@router.get(
    "/marks",
    response_model=list[MarkResponse],
    summary="List marks for courses this faculty leads",
)
def list_marks(
    _faculty: FacultyUser,
    db: DbSession,
    course_id: Annotated[int | None, Query(description="Filter by course id")] = None,
    component: Annotated[MarkComponent | None, Query(description="Filter by component")] = None,
    student_id: Annotated[int | None, Query(description="Filter by student id")] = None,
) -> list[MarkResponse]:
    """Return marks for courses led by the authenticated faculty."""
    try:
        return faculty_service.list_marks(
            db,
            _faculty.id,
            course_id=course_id,
            component=component,
            student_id=student_id,
        )
    except faculty_service.FacultyError as exc:
        raise _fail(exc) from exc


@router.post(
    "/marks",
    response_model=MarkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a mark for a student in a led course",
)
def create_mark(
    payload: MarkCreate,
    _faculty: FacultyUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> MarkResponse:
    """Create a mark. UNIQUE(student, course, component) -> 409 on duplicate.

    A score above the mark's max_score -> 422, validated before insert.
    """
    try:
        return faculty_service.create_mark(db, _faculty.id, payload)
    except faculty_service.FacultyError as exc:
        raise _fail(exc) from exc


@router.patch(
    "/marks/{mark_id}",
    response_model=MarkResponse,
    summary="Update a mark (owned course only)",
)
def update_mark(
    mark_id: int,
    payload: MarkUpdate,
    _faculty: FacultyUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> MarkResponse:
    """Partially update a mark's score or max_score.

    Omitted fields stay unchanged; explicit nulls and scores above the
    resulting max_score are rejected with 422 before anything is written.
    """
    try:
        return faculty_service.update_mark(db, _faculty.id, mark_id, payload)
    except faculty_service.FacultyError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Attendance (atomic batch)
# ---------------------------------------------------------------------------


@router.post(
    "/attendance/batch",
    response_model=AttendanceBatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Atomically mark attendance for multiple students",
)
def create_attendance_batch(
    payload: AttendanceBatchRequest,
    _faculty: FacultyUser,
    _csrf: CsrfGuard,
    db: DbSession,
) -> AttendanceBatchResponse:
    """Mark attendance for many students in ONE transaction.

    If any record duplicates an existing
    (student, timetable_entry, date) tuple, the whole batch rolls back
    and the client receives 409.
    """
    try:
        return faculty_service.create_attendance_batch(db, _faculty.id, payload)
    except faculty_service.FacultyError as exc:
        raise _fail(exc) from exc
