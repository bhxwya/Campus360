"""
Integration tests against real PostgreSQL (backend/tests/test_models_integration.py).

Isolation & safety model (important to understand):

* conftest.py forces CAMPUS360_DB_NAME=campus360_test BEFORE the app is
  imported, so every connection this test module makes - including Alembic's
  own - points at the dedicated test database, never campus360_db.
* The session-scoped fixture below ASSERTS that guard before running
  `alembic upgrade head`: if the settings ever point somewhere else, the
  suite refuses to run rather than migrate a development schema.
* The schema itself comes from the REAL migration (not create_all), so these
  tests exercise exactly what `alembic upgrade head` builds in development.
* Every test runs inside a transaction that is ROLLED BACK on teardown: no
  row written by a test survives, and tests cannot observe each other's data.
* Tests that EXPECT a constraint violation catch IntegrityError and let the
  teardown roll back the aborted transaction.
"""

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from alembic.config import Config
from app.core.config import PROJECT_ROOT, get_settings
from app.db.session import SessionLocal
from app.models import (
    Assignment,
    AssignmentSubmission,
    Course,
    DayOfWeek,
    Enrollment,
    EnrollmentStatus,
    FacultyProfile,
    Mark,
    MarkComponent,
    StudentProfile,
    TimetableEntry,
    User,
    UserRole,
)
from app.models.attendance import AttendanceRecord
from app.models.enums import AttendanceStatus, SubmissionStatus
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command


@pytest.fixture(scope="session")
def migrated_test_db() -> None:
    """Bring the TEST database to the latest schema via Alembic.

    Runs once per pytest session. `upgrade head` is idempotent: if the test
    database is already current it does nothing.
    """
    settings = get_settings()
    # Hard safety interlock: never migrate anything but the test database.
    assert settings.db_name == settings.test_db_name, (
        f"refusing to migrate db {settings.db_name!r} - expected {settings.test_db_name!r}"
    )
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "head")


@pytest.fixture()
def session(migrated_test_db: None) -> Session:
    """One Session per test, always rolled back (see module docstring)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


# ---------------------------------------------------------------------------
# Small factories so each test states only what IT is about.
# ---------------------------------------------------------------------------
def make_student(
    session: Session, code: str = "ST-1", email: str = "s1@campus.edu"
) -> StudentProfile:
    user = User(
        email=email,
        email_normalized=email.lower(),
        password_hash="x" * 60,  # placeholder hash; Stage 3 issues real ones
        role=UserRole.STUDENT,
    )
    profile = StudentProfile(user=user, student_code=code, full_name="Test Student")
    session.add(profile)
    session.flush()
    return profile


def make_course(session: Session, code: str = "CS-101") -> Course:
    course = Course(code=code, title="Test Course", credits=4)
    session.add(course)
    session.flush()
    return course


def make_faculty(
    session: Session, code: str = "FAC-1", email: str = "f1@campus.edu"
) -> FacultyProfile:
    user = User(
        email=email,
        email_normalized=email.lower(),
        password_hash="x" * 60,
        role=UserRole.FACULTY,
    )
    profile = FacultyProfile(user=user, faculty_code=code, full_name="Dr. Test")
    session.add(profile)
    session.flush()
    return profile


def make_timetable_entry(
    session: Session, course: Course, faculty: FacultyProfile
) -> TimetableEntry:
    entry = TimetableEntry(
        course=course,
        faculty=faculty,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 30),
        room="Room 204",
    )
    session.add(entry)
    session.flush()
    return entry


def make_assignment(
    session: Session, course: Course, faculty: FacultyProfile | None = None, **kwargs
) -> Assignment:
    """One assignment; ``kwargs`` set grading-relevant fields (max_score...)."""
    assignment = Assignment(
        course=course,
        created_by=faculty.id if faculty is not None else None,
        title="Test Assignment",
        due_at=datetime(2026, 11, 1, tzinfo=UTC),
        **kwargs,
    )
    session.add(assignment)
    session.flush()
    return assignment


def make_submission(
    session: Session, assignment: Assignment, student: StudentProfile, **kwargs
) -> AssignmentSubmission:
    """Insert a submission; ``kwargs`` override the submitted-row defaults
    (status, submitted_at, content) to construct grading scenarios."""
    fields: dict = {
        "assignment": assignment,
        "student": student,
        "submitted_at": datetime(2026, 10, 20, tzinfo=UTC),
        "content": "answer",
        "status": SubmissionStatus.SUBMITTED,
    }
    fields.update(kwargs)
    submission = AssignmentSubmission(**fields)
    session.add(submission)
    session.flush()
    return submission


# ---------------------------------------------------------------------------
# Schema is created by the real migration
# ---------------------------------------------------------------------------
def test_schema_is_at_head(session: Session) -> None:
    """The test database schema must be exactly the latest migration's."""
    version = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version is not None  # migration was applied


def test_enum_values_stored_as_lowercase(session: Session) -> None:
    """pg_enum() stores member VALUES ('student'), not names ('STUDENT')."""
    session.execute(
        text(
            "INSERT INTO users (email, email_normalized, password_hash, role, is_active) "
            "VALUES ('e@x.edu', 'e@x.edu', 'h', 'student', true)"
        )
    )
    stored = session.execute(text("SELECT role FROM users WHERE email = 'e@x.edu'")).scalar()
    assert stored == "student"


# ---------------------------------------------------------------------------
# Uniqueness rules enforced by PostgreSQL
# ---------------------------------------------------------------------------
def test_duplicate_email_rejected(session: Session) -> None:
    make_student(session, email="dup@campus.edu")
    with pytest.raises(IntegrityError):
        make_student(session, email="dup@campus.edu", code="ST-2")


# ---------------------------------------------------------------------------
# Stage 2.1 hardening: email_normalized must equal lower(email) in the DB.
# These insert RAW user rows (bypassing the factories, which always lowercase
# correctly) so we can prove PostgreSQL itself rejects a mismatch - the exact
# bug an application that forgets to lowercase would otherwise allow.
# ---------------------------------------------------------------------------
def _insert_raw_user(session: Session, email: str, normalized: str) -> None:
    """Insert a users row with an explicit (possibly wrong) email_normalized.

    Deliberately uses raw SQL and explicit values so the test controls the
    normalized column independently of any application-side lowercasing.
    """
    session.execute(
        text(
            "INSERT INTO users (email, email_normalized, password_hash, role, is_active) "
            "VALUES (:e, :n, 'h', 'student', true)"
        ),
        {"e": email, "n": normalized},
    )


def test_mismatched_normalized_email_rejected(session: Session) -> None:
    """email_normalized that is NOT lower(email) must be refused by PostgreSQL."""
    with pytest.raises(IntegrityError):
        # 'BOB@Campus.EDU' lowercased is 'bob@campus.edu', but we store the
        # mixed-case form in email_normalized -> CHECK fails.
        _insert_raw_user(session, email="BOB@Campus.EDU", normalized="BOB@Campus.EDU")
        session.flush()


def test_uppercase_normalized_with_lowercase_email_rejected(session: Session) -> None:
    """Even a 'close' mismatch (uppercase normalized, lowercase email) fails."""
    with pytest.raises(IntegrityError):
        _insert_raw_user(session, email="alice@campus.edu", normalized="Alice@campus.edu")
        session.flush()


def test_case_only_email_with_correct_normalized_accepted(session: Session) -> None:
    """A mixed-case email is FINE as long as email_normalized is its lowercase.

    This is the positive control: the CHECK is not banning mixed-case emails,
    only a normalized column that disagrees with lower(email). It also shows
    the two-accounts-differing-by-case trick is stopped by the UNIQUE index on
    email_normalized, because both would normalize to the same value.
    """
    _insert_raw_user(session, email="Carol@Campus.EDU", normalized="carol@campus.edu")
    session.flush()

    # A second account differing only by case must now collide on the
    # normalized UNIQUE constraint, not slip through.
    with pytest.raises(IntegrityError):
        _insert_raw_user(session, email="CAROL@campus.edu", normalized="carol@campus.edu")
        session.flush()


def test_duplicate_enrollment_rejected(session: Session) -> None:
    student = make_student(session)
    course = make_course(session)
    session.add(Enrollment(student=student, course=course))
    session.flush()
    with pytest.raises(IntegrityError):
        session.add(Enrollment(student=student, course=course))
        session.flush()


def test_duplicate_attendance_rejected(session: Session) -> None:
    student = make_student(session)
    course = make_course(session)
    faculty = make_faculty(session)
    entry = make_timetable_entry(session, course, faculty)
    day = date(2026, 10, 19)
    session.add(
        AttendanceRecord(
            student=student,
            timetable_entry=entry,
            attendance_date=day,
            status=AttendanceStatus.PRESENT,
        )
    )
    session.flush()
    with pytest.raises(IntegrityError):
        session.add(
            AttendanceRecord(
                student=student,
                timetable_entry=entry,
                attendance_date=day,
                status=AttendanceStatus.ABSENT,
            )
        )
        session.flush()


def test_faculty_double_booking_rejected(session: Session) -> None:
    """The partial unique index blocks the same faculty in two places at once."""
    faculty = make_faculty(session)
    course_a = make_course(session, "CS-101")
    course_b = make_course(session, "CS-102")
    make_timetable_entry(session, course_a, faculty)
    session.flush()
    with pytest.raises(IntegrityError):
        session.add(
            TimetableEntry(
                course=course_b,
                faculty=faculty,
                day_of_week=DayOfWeek.MONDAY,
                start_time=time(9, 0),
                end_time=time(10, 30),
            )
        )
        session.flush()


# ---------------------------------------------------------------------------
# CHECK constraints enforced by PostgreSQL
# ---------------------------------------------------------------------------
def test_negative_mark_rejected(session: Session) -> None:
    student = make_student(session)
    course = make_course(session)
    with pytest.raises(IntegrityError):
        session.add(
            Mark(
                student=student,
                course=course,
                component=MarkComponent.THEORY,
                score=-1,
                max_score=100,
            )
        )
        session.flush()


def test_score_above_max_rejected(session: Session) -> None:
    student = make_student(session)
    course = make_course(session)
    with pytest.raises(IntegrityError):
        session.add(
            Mark(
                student=student,
                course=course,
                component=MarkComponent.THEORY,
                score=95,
                max_score=50,
            )
        )
        session.flush()


def test_end_before_start_rejected(session: Session) -> None:
    course = make_course(session)
    faculty = make_faculty(session)
    with pytest.raises(IntegrityError):
        session.add(
            TimetableEntry(
                course=course,
                faculty=faculty,
                day_of_week=DayOfWeek.MONDAY,
                start_time=time(10, 30),
                end_time=time(9, 0),
            )
        )
        session.flush()


def test_zero_credits_rejected(session: Session) -> None:
    with pytest.raises(IntegrityError):
        session.add(Course(code="CS-000", title="Bad", credits=0))
        session.flush()


# ---------------------------------------------------------------------------
# Deletion policies enforced by PostgreSQL
# ---------------------------------------------------------------------------
def test_enrollment_restrict_blocks_profile_deletion(session: Session) -> None:
    """Academic history pins the student profile (RESTRICT, not CASCADE)."""
    student = make_student(session)
    course = make_course(session)
    session.add(Enrollment(student=student, course=course))
    session.flush()
    with pytest.raises(IntegrityError):
        session.delete(student.user)  # cascades toward the profile, hits RESTRICT
        session.flush()


def test_valid_schema_roundtrip(session: Session) -> None:
    """Happy path: a full academic chain inserts and reads back consistently."""
    student = make_student(session)
    course = make_course(session)
    faculty = make_faculty(session)
    entry = make_timetable_entry(session, course, faculty)
    session.add(Enrollment(student=student, course=course, status=EnrollmentStatus.ACTIVE))
    session.add(
        AttendanceRecord(
            student=student,
            timetable_entry=entry,
            attendance_date=date(2026, 10, 19),
            status=AttendanceStatus.LATE,
            marked_by=faculty.id,
        )
    )
    session.add(
        Mark(
            student=student,
            course=course,
            component=MarkComponent.MIDTERM,
            score=Decimal("42.50"),
            max_score=Decimal("50.00"),
            recorded_by=faculty.id,
        )
    )
    session.flush()

    mark = session.query(Mark).one()
    assert mark.score == Decimal("42.50")  # NUMERIC round-trips exactly
    assert mark.recorder.faculty_code == "FAC-1"
    assert entry.course.code == "CS-101"


# ---------------------------------------------------------------------------
# Assignment max_score + submission grading state (PostgreSQL-enforced)
# ---------------------------------------------------------------------------
def test_assignment_max_score_defaults_to_100(session: Session) -> None:
    """Both the ORM default and the MIGRATION's server default must yield 100."""
    course = make_course(session)
    assignment = make_assignment(session, course)
    session.refresh(assignment)
    assert assignment.max_score == Decimal("100")  # ORM default + DB round-trip

    # Raw SQL proves the migration's SERVER default with no ORM defaults in
    # play - what a pre-existing row would be backfilled with.
    raw = session.execute(
        text(
            "INSERT INTO assignments (course_id, title, due_at, is_active) "
            "VALUES (:c, 'Raw assignment', now(), true) RETURNING max_score"
        ),
        {"c": course.id},
    ).scalar()
    assert raw == Decimal("100")


def test_assignment_zero_max_score_rejected(session: Session) -> None:
    """max_score must be positive (ck_assignments_max_score_positive)."""
    course = make_course(session)
    with pytest.raises(IntegrityError):
        make_assignment(session, course, max_score=0)


def test_assignment_negative_max_score_rejected(session: Session) -> None:
    """A negative grading scale must never store."""
    course = make_course(session)
    with pytest.raises(IntegrityError):
        make_assignment(session, course, max_score=-5)


def test_submitted_submission_carries_no_grade_fields(session: Session) -> None:
    """Positive control: a plain submitted row (grade fields all NULL) is valid."""
    student = make_student(session)
    course = make_course(session)
    faculty = make_faculty(session)
    assignment = make_assignment(session, course, faculty)
    submission = make_submission(session, assignment, student)
    assert submission.status == SubmissionStatus.SUBMITTED
    assert submission.score is None
    assert submission.max_score is None
    assert submission.graded_at is None


def test_score_above_snapshot_max_rejected(session: Session) -> None:
    """score > snapshot max_score violates ck_..._submission_score_range."""
    student = make_student(session)
    course = make_course(session)
    faculty = make_faculty(session)
    assignment = make_assignment(session, course, faculty, max_score=64)
    with pytest.raises(IntegrityError):
        make_submission(
            session,
            assignment,
            student,
            score=Decimal("65"),
            max_score=Decimal("64"),
            status=SubmissionStatus.GRADED,
            graded_by=faculty.id,
            graded_at=datetime(2026, 10, 20, tzinfo=UTC),
        )


def test_score_equal_to_snapshot_max_accepted(session: Session) -> None:
    """The boundary value must be storable - only scores ABOVE it fail."""
    student = make_student(session)
    course = make_course(session)
    faculty = make_faculty(session)
    assignment = make_assignment(session, course, faculty, max_score=64)
    submission = make_submission(
        session,
        assignment,
        student,
        score=Decimal("64"),
        max_score=Decimal("64"),
        status=SubmissionStatus.GRADED,
        graded_by=faculty.id,
        graded_at=datetime(2026, 10, 20, tzinfo=UTC),
    )
    assert submission.score == Decimal("64.00")


def test_score_without_snapshot_rejected(session: Session) -> None:
    """A score with NULL max_score must fail: the range CHECK's NOT NULL guard
    (a bare 'score <= max_score' would silently PASS via SQL NULL logic)."""
    student = make_student(session)
    course = make_course(session)
    faculty = make_faculty(session)
    assignment = make_assignment(session, course, faculty)
    with pytest.raises(IntegrityError):
        make_submission(
            session,
            assignment,
            student,
            score=Decimal("50"),
            status=SubmissionStatus.GRADED,
            graded_at=datetime(2026, 10, 20, tzinfo=UTC),
        )


def test_graded_status_requires_score_and_timestamp(session: Session) -> None:
    """submission_grade_state: a 'graded' row without a score is impossible."""
    student = make_student(session)
    course = make_course(session)
    faculty = make_faculty(session)
    assignment = make_assignment(session, course, faculty)
    with pytest.raises(IntegrityError):
        make_submission(
            session,
            assignment,
            student,
            max_score=Decimal("100"),
            status=SubmissionStatus.GRADED,
            graded_at=datetime(2026, 10, 20, tzinfo=UTC),
        )


def test_returned_status_requires_full_grading_state(session: Session) -> None:
    """RETURNED is a graded state too: bare 'returned' (no score) must fail."""
    student = make_student(session)
    course = make_course(session)
    faculty = make_faculty(session)
    assignment = make_assignment(session, course, faculty)
    with pytest.raises(IntegrityError):
        make_submission(session, assignment, student, status=SubmissionStatus.RETURNED)


def test_submitted_status_with_graded_at_rejected(session: Session) -> None:
    """A submitted row carrying a grading timestamp contradicts grade_state."""
    student = make_student(session)
    course = make_course(session)
    faculty = make_faculty(session)
    assignment = make_assignment(session, course, faculty)
    with pytest.raises(IntegrityError):
        make_submission(
            session,
            assignment,
            student,
            status=SubmissionStatus.SUBMITTED,
            graded_at=datetime(2026, 10, 20, tzinfo=UTC),
        )


def test_valid_grading_roundtrip(session: Session) -> None:
    """Full grade state (score, snapshot, feedback, grader, time) round-trips."""
    student = make_student(session)
    course = make_course(session)
    faculty = make_faculty(session)
    assignment = make_assignment(session, course, faculty, max_score=150)
    graded = make_submission(
        session,
        assignment,
        student,
        score=Decimal("120.50"),
        max_score=Decimal("150.00"),
        feedback="Excellent extra-credit work",
        graded_by=faculty.id,
        graded_at=datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
        status=SubmissionStatus.GRADED,
    )
    assert graded.score == Decimal("120.50")
    assert graded.max_score == Decimal("150.00")
    assert graded.feedback == "Excellent extra-credit work"
    assert graded.graded_by == faculty.id
    assert graded.graded_at == datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def test_grade_snapshot_survives_assignment_max_change(session: Session) -> None:
    """Editing the assignment's maximum never rewrites stored snapshots, and
    the row-level CHECK compares against the SNAPSHOT, not the live row."""
    student = make_student(session)
    course = make_course(session)
    faculty = make_faculty(session)
    assignment = make_assignment(session, course, faculty, max_score=100)
    graded = make_submission(
        session,
        assignment,
        student,
        score=Decimal("95"),
        max_score=Decimal("100"),
        status=SubmissionStatus.GRADED,
        graded_by=faculty.id,
        graded_at=datetime(2026, 10, 20, tzinfo=UTC),
    )

    # Squeeze the assignment below the already-issued score: still valid,
    # because the grade was earned on the scale it snapshotted.
    assignment.max_score = Decimal("50")
    session.flush()
    session.expire(graded)

    assert graded.max_score == Decimal("100.00")
    assert graded.score == Decimal("95.00")
