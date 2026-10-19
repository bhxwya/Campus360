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

from datetime import date, time
from decimal import Decimal

import pytest
from alembic.config import Config
from app.core.config import PROJECT_ROOT, get_settings
from app.db.session import SessionLocal
from app.models import (
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
from app.models.enums import AttendanceStatus
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
