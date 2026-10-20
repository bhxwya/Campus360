"""
Faculty business logic (backend/app/services/faculty.py).

Stage 5's core: every faculty operation lives here, so the router in
``api/faculty.py`` stays thin (translate HTTP <-> service call, map errors to
status codes) and the logic can be reused by scripts and background jobs.

TWO RULES THAT SHAPE THIS MODULE
--------------------------------

1. STRICT COURSE OWNERSHIP. A faculty member may only touch assignments,
   submissions, marks, and attendance for courses where they are the
   ``lead_faculty_id``. Substitutes have NO implicit access. Every
   course-scoped operation validates ownership first.

2. ATOMICITY. Attendance batches commit in ONE transaction: if any record
   duplicates an existing (student, timetable_entry, date) tuple, the entire
   batch rolls back and the router answers 409.

Error contract: services raise ``FacultyError`` with an HTTP status code; the
router converts it into a response. The service layer stays free of
``HTTPException`` so it can be called from a CLI or a task runner.
"""

from importlib import import_module

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.assignment import Assignment, AssignmentSubmission
from app.models.attendance import AttendanceRecord
from app.models.course import Course
from app.models.mark import Mark
from app.models.user import StudentProfile, User
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
    compute_grade,
)


class FacultyError(Exception):
    """Raised when a faculty operation cannot proceed.

    Carries the HTTP status the router should answer with.
    """

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_course_ownership(db: Session, faculty_id: int, course_id: int) -> Course:
    """Return the course if ``faculty_id`` leads it, else raise FacultyError.

    ``courses.lead_faculty_id`` is the SOLE authority for course ownership:
    a faculty member who is not the lead gets 403, not 404 (the course
    exists, they just cannot touch it).
    """
    course = db.get(Course, course_id)
    if course is None:
        raise FacultyError(f"Course {course_id} not found", 404)
    if course.lead_faculty_id != faculty_id:
        raise FacultyError(
            f"Course {course_id} is not led by faculty {faculty_id}",
            403,
        )
    return course


def _faculty_profile_id(db: Session, user_id: int) -> int:
    """Resolve a faculty User to their FacultyProfile id, or raise 404."""
    user = db.get(User, user_id)
    if user is None:
        raise FacultyError(f"User {user_id} not found", 404)
    if user.role.value != "faculty":
        raise FacultyError(f"User {user_id} is not a faculty member", 403)
    profile = user.faculty_profile
    if profile is None:
        raise FacultyError(f"Faculty profile not found for user {user_id}", 404)
    return profile.id


#: AssignmentUpdate fields backed by NOT NULL columns: an explicit JSON null
#: for them in a PATCH body means "leave unchanged" instead of letting
#: PostgreSQL raise an integrity error (which would surface as a 500).
_ASSIGNMENT_NOT_NULL_FIELDS = frozenset({"title", "due_at", "max_score", "is_active"})


def _assignment_response(assignment: Assignment) -> AssignmentResponse:
    """Build the assignment projection."""
    return AssignmentResponse(
        id=assignment.id,
        course_id=assignment.course_id,
        title=assignment.title,
        description=assignment.description,
        due_at=assignment.due_at,
        weightage_percent=(
            float(assignment.weightage_percent)
            if assignment.weightage_percent is not None
            else None
        ),
        max_score=float(assignment.max_score),
        is_active=assignment.is_active,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


def _submission_response(submission: AssignmentSubmission) -> AssignmentSubmissionResponse:
    """Build the submission projection, joined with the student profile."""
    student = submission.student
    return AssignmentSubmissionResponse(
        id=submission.id,
        assignment_id=submission.assignment_id,
        student_id=student.id,
        student_code=student.student_code,
        student_name=student.full_name,
        submitted_at=submission.submitted_at,
        content=submission.content,
        status=submission.status,
        score=(
            float(submission.score) if submission.score is not None else None
        ),
        max_score=(
            float(submission.max_score)
            if submission.max_score is not None
            else None
        ),
        feedback=submission.feedback,
        graded_by=submission.graded_by,
        graded_at=submission.graded_at,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
    )


def _mark_percentage(score: float, max_score: float) -> float:
    """percentage = score / max_score * 100, rounded to 2 dp.

    The values arrive as ``Decimal`` straight from the Numeric columns, so
    they are coerced to float before the arithmetic (Decimal * float raises).
    """
    return round(float(score) * 100.0 / float(max_score), 2)


def _mark_response(mark: Mark) -> MarkResponse:
    """Build the mark projection with computed percentage and grade."""
    percentage = _mark_percentage(mark.score, mark.max_score)
    grade, grade_points = compute_grade(percentage)
    return MarkResponse(
        id=mark.id,
        student_id=mark.student_id,
        course_id=mark.course_id,
        component=mark.component,
        score=float(mark.score),
        max_score=float(mark.max_score),
        percentage=percentage,
        grade=grade,
        grade_points=grade_points,
        recorded_by=mark.recorded_by,
        recorded_at=mark.recorded_at,
    )


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------


def create_assignment(
    db: Session,
    faculty_user_id: int,
    payload: AssignmentCreate,
) -> AssignmentResponse:
    """Create an assignment for a course the faculty member leads."""
    faculty_id = _faculty_profile_id(db, faculty_user_id)
    _require_course_ownership(db, faculty_id, payload.course_id)

    assignment = Assignment(
        course_id=payload.course_id,
        created_by=faculty_id,
        title=payload.title,
        description=payload.description,
        due_at=payload.due_at,
        weightage_percent=payload.weightage_percent,
        max_score=payload.max_score,
        is_active=True,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return _assignment_response(assignment)


def list_assignments(
    db: Session,
    faculty_user_id: int,
    *,
    course_id: int | None = None,
    is_active: bool | None = None,
) -> list[AssignmentResponse]:
    """List assignments for courses the faculty member leads.

    An explicit ``course_id`` filter is a course-scoped request: it must be
    validated like any other (403 if another faculty leads it, 404 if it does
    not exist) instead of silently returning an empty list.
    """
    faculty_id = _faculty_profile_id(db, faculty_user_id)
    if course_id is not None:
        _require_course_ownership(db, faculty_id, course_id)

    base = (
        select(Assignment)
        .join(Course, Assignment.course_id == Course.id)
        .where(Course.lead_faculty_id == faculty_id)
    )
    if course_id is not None:
        base = base.where(Assignment.course_id == course_id)
    if is_active is not None:
        base = base.where(Assignment.is_active == is_active)

    rows = db.scalars(base.order_by(Assignment.due_at.desc())).all()
    return [_assignment_response(row) for row in rows]


def get_assignment(
    db: Session,
    faculty_user_id: int,
    assignment_id: int,
) -> AssignmentResponse:
    """Fetch one assignment, only if the faculty member leads its course."""
    faculty_id = _faculty_profile_id(db, faculty_user_id)

    assignment = db.get(Assignment, assignment_id)
    if assignment is None:
        raise FacultyError(f"Assignment {assignment_id} not found", 404)
    _require_course_ownership(db, faculty_id, assignment.course_id)
    return _assignment_response(assignment)


def update_assignment(
    db: Session,
    faculty_user_id: int,
    assignment_id: int,
    payload: AssignmentUpdate,
) -> AssignmentResponse:
    """Apply the fields the client actually sent to an assignment."""
    faculty_id = _faculty_profile_id(db, faculty_user_id)

    assignment = db.get(Assignment, assignment_id)
    if assignment is None:
        raise FacultyError(f"Assignment {assignment_id} not found", 404)
    _require_course_ownership(db, faculty_id, assignment.course_id)

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if value is None and field in _ASSIGNMENT_NOT_NULL_FIELDS:
            continue
        setattr(assignment, field, value)

    db.commit()
    db.refresh(assignment)
    return _assignment_response(assignment)


def deactivate_assignment(
    db: Session,
    faculty_user_id: int,
    assignment_id: int,
) -> AssignmentResponse:
    """Deactivate (never delete) an assignment, keeping its submissions."""
    faculty_id = _faculty_profile_id(db, faculty_user_id)

    assignment = db.get(Assignment, assignment_id)
    if assignment is None:
        raise FacultyError(f"Assignment {assignment_id} not found", 404)
    _require_course_ownership(db, faculty_id, assignment.course_id)

    assignment.is_active = False
    db.commit()
    db.refresh(assignment)
    return _assignment_response(assignment)


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------


def list_submissions(
    db: Session,
    faculty_user_id: int,
    assignment_id: int,
) -> list[AssignmentSubmissionResponse]:
    """List all submissions for an assignment the faculty member owns."""
    faculty_id = _faculty_profile_id(db, faculty_user_id)

    assignment = db.get(Assignment, assignment_id)
    if assignment is None:
        raise FacultyError(f"Assignment {assignment_id} not found", 404)
    _require_course_ownership(db, faculty_id, assignment.course_id)

    rows = (
        db.query(AssignmentSubmission)
        .options(joinedload(AssignmentSubmission.student))
        .filter(AssignmentSubmission.assignment_id == assignment_id)
        .order_by(AssignmentSubmission.submitted_at.desc())
        .all()
    )
    return [_submission_response(row) for row in rows]


def grade_submission(
    db: Session,
    faculty_user_id: int,
    submission_id: int,
    payload: SubmissionGrade,
) -> AssignmentSubmissionResponse:
    """Grade a submission: score + feedback + status for an owned assignment.

    Order matters: ownership is proven BEFORE score validation, so a
    non-owner always gets 403 no matter what the payload looks like. The
    score is then checked against the ASSIGNMENT's max_score - not a
    hardcoded 100, because assignments may permit scores above 100.

    Grading writes the grade state atomically: score, the max_score
    SNAPSHOT (so later changes to the assignment never rewrite old grades),
    feedback, graded_by (the FacultyProfile id) and graded_at (database
    clock). ``payload.status`` can only be GRADED/RETURNED - the schema
    validator refuses 'submitted', which the database CHECK would reject
    anyway.
    """
    faculty_id = _faculty_profile_id(db, faculty_user_id)

    submission = db.get(AssignmentSubmission, submission_id)
    if submission is None:
        raise FacultyError(f"Submission {submission_id} not found", 404)

    assignment = db.get(Assignment, submission.assignment_id)
    if assignment is None:
        raise FacultyError(f"Assignment {submission.assignment_id} not found", 404)
    _require_course_ownership(db, faculty_id, assignment.course_id)

    max_score = float(assignment.max_score)
    if payload.score > max_score:
        raise FacultyError(
            f"Score {payload.score} exceeds the assignment maximum {max_score}",
            422,
        )

    submission.score = payload.score
    submission.max_score = max_score
    submission.feedback = payload.feedback
    submission.graded_by = faculty_id
    submission.graded_at = func.now()
    submission.status = payload.status
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # The checks above should make this unreachable; translate any
        # residual constraint violation (e.g. a concurrent edit of the
        # assignment between validation and commit) into a clean 422.
        raise FacultyError(
            "Grading was rejected by the database constraints",
            422,
        ) from exc
    db.refresh(submission)
    return _submission_response(submission)


# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------


def list_marks(
    db: Session,
    faculty_user_id: int,
    *,
    course_id: int | None = None,
    component: MarkComponent | None = None,
    student_id: int | None = None,
) -> list[MarkResponse]:
    """List marks for courses the faculty member leads.

    An explicit ``course_id`` filter is a course-scoped request: it must be
    validated like any other (403 if another faculty leads it, 404 if it does
    not exist) instead of silently returning an empty list.
    """
    faculty_id = _faculty_profile_id(db, faculty_user_id)
    if course_id is not None:
        _require_course_ownership(db, faculty_id, course_id)

    base = db.query(Mark).join(Course, Mark.course_id == Course.id).where(
        Course.lead_faculty_id == faculty_id
    )
    if course_id is not None:
        base = base.where(Mark.course_id == course_id)
    if component is not None:
        base = base.where(Mark.component == component)
    if student_id is not None:
        base = base.where(Mark.student_id == student_id)

    rows = base.order_by(Mark.recorded_at.desc()).all()
    return [_mark_response(row) for row in rows]


def create_mark(
    db: Session,
    faculty_user_id: int,
    payload: MarkCreate,
) -> MarkResponse:
    """Record a mark for a student in a course the faculty member leads.

    The score range is validated BEFORE the insert, so a value above
    max_score fails with 422 without touching the database.
    UNIQUE(student_id, course_id, component) still makes a duplicate mark
    impossible at the database level; if the commit fails anyway, the
    IntegrityError is classified by database STATE, not by the driver's
    error text: after a rollback, a visible row for the same
    (student, course, component) tuple means the unique key collided
    (409), while a CHECK/FK violation left no row behind (422).
    """
    faculty_id = _faculty_profile_id(db, faculty_user_id)
    _require_course_ownership(db, faculty_id, payload.course_id)

    # Verify the student exists.
    student = db.get(StudentProfile, payload.student_id)
    if student is None:
        raise FacultyError(f"Student {payload.student_id} not found", 404)

    # Validate the score range BEFORE inserting: the schema bounds alone
    # cannot see score <= max_score, and the database CHECK must not be the
    # first line of defence (its violation would otherwise be misread as a
    # duplicate in the IntegrityError handler below).
    if payload.score > payload.max_score:
        raise FacultyError(
            f"Score {payload.score} exceeds the mark maximum {payload.max_score}",
            422,
        )

    mark = Mark(
        student_id=payload.student_id,
        course_id=payload.course_id,
        component=payload.component,
        score=payload.score,
        max_score=payload.max_score,
        recorded_by=faculty_id,
    )
    db.add(mark)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Classify by STATE, not by driver-specific error strings: a
        # committed duplicate for this tuple is visible after the rollback
        # (409); a CHECK/FK violation never became a row (422 fallback).
        duplicate = (
            db.query(Mark.id)
            .filter(
                Mark.student_id == payload.student_id,
                Mark.course_id == payload.course_id,
                Mark.component == payload.component,
            )
            .first()
        )
        if duplicate is not None:
            raise FacultyError(
                f"A {payload.component.value} mark already exists for "
                f"student {payload.student_id} in course {payload.course_id}",
                409,
            ) from exc
        raise FacultyError(
            "Mark was rejected by the database constraints",
            422,
        ) from exc
    db.refresh(mark)
    return _mark_response(mark)


def update_mark(
    db: Session,
    faculty_user_id: int,
    mark_id: int,
    payload: MarkUpdate,
) -> MarkResponse:
    """Partially update a mark for an owned course.

    Order matters: ownership is proven FIRST, so a non-owner gets 403 no
    matter what the payload looks like. After that, an explicit null for a
    NOT NULL column (422 - it would otherwise reach PostgreSQL and fail as
    a 500) and any score above the resulting max_score are rejected BEFORE
    commit. Omitted fields keep their stored values.
    """
    faculty_id = _faculty_profile_id(db, faculty_user_id)

    mark = db.get(Mark, mark_id)
    if mark is None:
        raise FacultyError(f"Mark {mark_id} not found", 404)
    _require_course_ownership(db, faculty_id, mark.course_id)

    data = payload.model_dump(exclude_unset=True)
    # Explicit nulls for the NOT NULL columns (score, max_score) mean "bad
    # request", not "set to NULL" - reject them before any mutation.
    for field, value in data.items():
        if value is None:
            raise FacultyError(f"{field} must not be null", 422)

    # Cross-field CHECK: score <= max_score against the MERGED result, so
    # both a lone high score and a lowered max_score are caught before
    # commit (omitted fields fall back to the stored values).
    new_score = float(data.get("score", mark.score))
    new_max_score = float(data.get("max_score", mark.max_score))
    if new_score > new_max_score:
        raise FacultyError(
            f"Score {new_score} exceeds the mark maximum {new_max_score}",
            422,
        )

    for field, value in data.items():
        setattr(mark, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Validation above should make this unreachable; translate any
        # residual constraint violation (e.g. a concurrent edit between
        # validation and commit) into a clean 422.
        raise FacultyError(
            "Mark update was rejected by the database constraints",
            422,
        ) from exc
    db.refresh(mark)
    return _mark_response(mark)


# ---------------------------------------------------------------------------
# Attendance (atomic batch)
# ---------------------------------------------------------------------------


def create_attendance_batch(
    db: Session,
    faculty_user_id: int,
    payload: AttendanceBatchRequest,
) -> AttendanceBatchResponse:
    """Atomically mark attendance for multiple students in one session.

    Every record is inserted inside a single transaction. If ANY record
    duplicates an existing (student_id, timetable_entry_id, attendance_date)
    tuple, the WHOLE batch rolls back and FacultyError(409) is raised.

    The timetable entry's course must be led by the calling faculty member.
    """
    faculty_id = _faculty_profile_id(db, faculty_user_id)

    TimetableEntry = import_module("app.models.timetable").TimetableEntry

    # Verify the timetable entry exists and its course is led by this faculty.
    timetable_entry = db.get(TimetableEntry, payload.timetable_entry_id)
    if timetable_entry is None:
        raise FacultyError(
            f"Timetable entry {payload.timetable_entry_id} not found",
            404,
        )
    _require_course_ownership(db, faculty_id, timetable_entry.course_id)

    # Verify every student exists.
    student_ids = [r.student_id for r in payload.records]
    students = {
        s.id: s
        for s in db.query(StudentProfile).filter(
            StudentProfile.id.in_(student_ids)
        ).all()
    }
    missing = set(student_ids) - set(students.keys())
    if missing:
        raise FacultyError(
            f"Student(s) not found: {sorted(missing)}",
            404,
        )

    # Pre-check for existing attendance records on this timetable entry + date.
    existing = (
        db.query(AttendanceRecord.student_id)
        .filter(
            AttendanceRecord.timetable_entry_id == payload.timetable_entry_id,
            AttendanceRecord.attendance_date == payload.attendance_date,
            AttendanceRecord.student_id.in_(student_ids),
        )
        .all()
    )
    duplicate_ids = {row.student_id for row in existing}
    if duplicate_ids:
        raise FacultyError(
            f"Attendance already recorded for student(s) "
            f"{sorted(duplicate_ids)} on {payload.attendance_date} "
            f"for timetable entry {payload.timetable_entry_id}",
            409,
        )

    # All checks passed - insert every record in one transaction.
    for item in payload.records:
        record = AttendanceRecord(
            timetable_entry_id=payload.timetable_entry_id,
            student_id=item.student_id,
            attendance_date=payload.attendance_date,
            status=item.status,
            marked_by=faculty_id,
        )
        db.add(record)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Race condition: another request inserted a duplicate between our
        # pre-check and commit. Report as 409.
        raise FacultyError(
            "Attendance batch failed: duplicate record(s) detected",
            409,
        ) from exc

    return AttendanceBatchResponse(
        marked_count=len(payload.records),
        skipped_duplicate_student_ids=[],
    )