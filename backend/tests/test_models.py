"""
Schema/metadata tests WITHOUT a database (backend/tests/test_models.py).

These tests inspect ``Base.metadata`` directly - the same object Alembic's
autogenerate uses - so they run in milliseconds and prove:

* every model is registered (Alembic would silently miss an unimported model);
* tables, unique constraints, CHECK constraints and indexes exist as designed;
* relationships point at the intended classes;
* foreign keys carry the intended ondelete policies;
* the Alembic environment resolves a migration head.

Nothing here touches PostgreSQL - integration behaviour lives in
test_models_integration.py.
"""

from alembic.config import Config
from alembic.script import ScriptDirectory
from app.core.config import PROJECT_ROOT
from app.db.base import Base
from app.models import (
    Announcement,
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    Course,
    Enrollment,
    FacultyProfile,
    Mark,
    StudentProfile,
    TimetableEntry,
    User,
)
from sqlalchemy import CheckConstraint, Numeric, UniqueConstraint
from sqlalchemy.dialects import postgresql

#: The 11 application tables the initial migration must create.
EXPECTED_TABLES = {
    "announcements",
    "assignment_submissions",
    "assignments",
    "attendance_records",
    "courses",
    "enrollments",
    "faculty_profiles",
    "marks",
    "student_profiles",
    "timetable_entries",
    "users",
    "user_sessions",  # Stage 3: server-side auth sessions
}


def test_all_models_registered_in_metadata() -> None:
    """Every table must be visible on Base.metadata - this is what Alembic
    autogenerate compares against the live database. A model file that is
    never imported would be invisible here AND to migrations."""
    assert EXPECTED_TABLES == set(Base.metadata.tables)


def test_unique_constraints_exist() -> None:
    """The 'impossible to duplicate' rules must be declared on the tables."""

    def unique_names(table_name: str) -> set[str]:
        table = Base.metadata.tables[table_name]
        return {uc.name for uc in table.constraints if isinstance(uc, UniqueConstraint)}

    assert "uq_enrollment_student_course" in unique_names("enrollments")
    assert "uq_attendance_student_session_date" in unique_names("attendance_records")
    assert "uq_mark_student_course_component" in unique_names("marks")
    assert "uq_submission_assignment_student" in unique_names("assignment_submissions")
    # 1:1 profile links: unique=True on the column produces a unique constraint
    assert "uq_student_profiles_user_id" in unique_names("student_profiles")
    assert "uq_faculty_profiles_user_id" in unique_names("faculty_profiles")
    assert "uq_users_email" in unique_names("users")


def test_check_constraints_exist() -> None:
    """Numeric sanity rules live in the schema, not only in application code."""

    def check_names(table_name: str) -> set[str]:
        table = Base.metadata.tables[table_name]
        return {c.name for c in table.constraints if isinstance(c, CheckConstraint)}

    assert {"ck_marks_score_not_negative", "ck_marks_score_within_max"} <= check_names("marks")
    assert "ck_assignments_weightage_percent_range" in check_names("assignments")
    assert "ck_timetable_entries_valid_time_range" in check_names("timetable_entries")
    assert "ck_courses_credits_positive" in check_names("courses")
    # Stage 2.1 hardening: email_normalized must equal lower(email) at the DB.
    assert "ck_users_email_normalized_lowercase" in check_names("users")


def test_unique_indexes_on_timetable() -> None:
    """The timetable 'no duplicate slot' rules are INDEXES (not constraints)."""
    timetable = Base.metadata.tables["timetable_entries"]
    unique_indexes = {ix.name for ix in timetable.indexes if ix.unique}

    assert "uq_timetable_course_slot" in unique_indexes
    assert "uq_timetable_faculty_slot" in unique_indexes

    # The faculty one must be PARTIAL (only applies when faculty is assigned).
    faculty_ix = next(ix for ix in timetable.indexes if ix.name == "uq_timetable_faculty_slot")
    assert faculty_ix.dialect_kwargs["postgresql_where"] is not None


def test_relationships_point_at_expected_classes() -> None:
    """Python-side relationship() wiring matches the designed entity graph."""
    assert User.student_profile.property.mapper.class_ is StudentProfile
    assert User.faculty_profile.property.mapper.class_ is FacultyProfile
    assert Enrollment.student.property.mapper.class_ is StudentProfile
    assert Enrollment.course.property.mapper.class_ is Course
    assert AttendanceRecord.timetable_entry.property.mapper.class_ is TimetableEntry
    assert Mark.course.property.mapper.class_ is Course
    assert AssignmentSubmission.assignment.property.mapper.class_ is Assignment
    assert Announcement.author.property.mapper.class_ is User


def test_foreign_key_ondelete_policies() -> None:
    """Deletion behaviour is part of the DESIGN and must survive into the DB
    mapping: history is RESTRICTed, attribution is SET NULL, identity cascades."""
    expected = {
        ("attendance_records", "student_id"): "RESTRICT",
        ("attendance_records", "timetable_entry_id"): "RESTRICT",
        ("enrollments", "course_id"): "RESTRICT",
        ("marks", "student_id"): "RESTRICT",
        ("courses", "lead_faculty_id"): "SET NULL",
        ("timetable_entries", "course_id"): "CASCADE",
        ("student_profiles", "user_id"): "CASCADE",
        ("announcements", "created_by"): "SET NULL",
    }
    for (table_name, column), ondelete in expected.items():
        table = Base.metadata.tables[table_name]
        fks = [fk for fk in table.foreign_keys if fk.parent.name == column]
        assert fks, f"missing FK on {table_name}.{column}"
        assert fks[0].ondelete == ondelete, f"{table_name}.{column} ondelete"


def test_marks_use_numeric_not_float() -> None:
    """Marks must be exact decimals (NUMERIC), never FLOAT/DOUBLE.

    We assert against sqlalchemy.Numeric (the generic base class): on
    PostgreSQL the concrete type is sqlalchemy.Numeric rendered as NUMERIC,
    and postgresql.Numeric is a SUBCLASS of it. Checking the generic parent
    is correct and dialect-portable.
    """
    marks = Base.metadata.tables["marks"]
    for column_name in ("score", "max_score"):
        col = marks.columns[column_name]
        assert isinstance(col.type, Numeric), f"{column_name} must be NUMERIC, got {col.type!r}"
        assert not isinstance(col.type, (postgresql.DOUBLE_PRECISION,)), f"{column_name} is float"


def test_alembic_resolves_a_head_revision() -> None:
    """The Alembic script directory must resolve a head revision - proving the
    versions/ directory is wired to alembic.ini correctly."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() is not None
