"""
Admin API tests for the reports (backend/tests/test_admin_reports.py).

The reports are COMPUTED aggregates over the live rows, so these tests
create real data (students, courses, enrolments, timetable entries,
attendance records, marks) and then check the numbers the API reports.

Two honest-emptiness rules are pinned here:
  * ``average_cgpa`` and ``faculty_publication_count`` are ALWAYS null,
    because the schema has no grade scale and no publications table. The
    API says so in ``notes`` instead of inventing a number.
  * an attendance/pass report with nothing to report is ``null``, not 0 -
    "no data" and "everyone failed" are very different claims.
"""

from datetime import date, time

from app.db.session import SessionLocal
from app.models import (
    AttendanceRecord,
    AttendanceStatus,
    Course,
    DayOfWeek,
    Mark,
    MarkComponent,
    TimetableEntry,
)


def _course(admin_client, **overrides) -> dict:
    """POST a course and return the parsed body."""
    payload = {"code": "CS-301", "title": "Data Structures", "credits": 4}
    payload.update(overrides)
    response = admin_client.client.post(
        "/api/v1/admin/courses", json=payload, headers=admin_client.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def _student(admin_client, **overrides) -> dict:
    """POST a student and return the parsed body."""
    payload = {
        "email": "student@campus360.example.com",
        "password": "StudentPass123",
        "student_code": "ST-2026-2000",
        "full_name": "Report Student",
    }
    payload.update(overrides)
    response = admin_client.client.post(
        "/api/v1/admin/students", json=payload, headers=admin_client.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def _seed_attendance(course_id: int, student_id: int, statuses: list[str]) -> None:
    """Insert attendance records for one student in one course.

    A timetable entry is required (attendance references a scheduled
    session), so one is created on the fly.
    """
    db = SessionLocal()
    try:
        course = db.get(Course, course_id)
        entry = TimetableEntry(
            course=course,
            day_of_week=DayOfWeek.MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 30),
            room="204",
        )
        db.add(entry)
        db.flush()
        for index, status in enumerate(statuses):
            db.add(
                AttendanceRecord(
                    student_id=student_id,
                    timetable_entry_id=entry.id,
                    attendance_date=date(2026, 10, 5 + index),
                    status=AttendanceStatus(status),
                )
            )
        db.commit()
    finally:
        db.close()


def _seed_mark(student_id: int, course_id: int, score: float, max_score: float) -> None:
    """Insert one scored component for a student in a course."""
    db = SessionLocal()
    try:
        db.add(
            Mark(
                student_id=student_id,
                course_id=course_id,
                component=MarkComponent.INTERNAL,
                score=score,
                max_score=max_score,
            )
        )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def test_report_summary_counts_students_and_courses(admin_client, db_cleaner) -> None:
    """The summary counts the rows it can see."""
    _course(admin_client)
    _student(admin_client)

    response = admin_client.client.get("/api/v1/admin/reports/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_students"] >= 1
    assert body["active_students"] >= 1
    assert body["total_courses"] >= 1
    assert body["total_faculty"] >= 0


def test_report_summary_counts_deactivated_students_as_inactive(admin_client, db_cleaner) -> None:
    """A deactivated student is still counted, but not as active.

    Deactivation flips ``users.is_active`` and keeps the profile row, so the
    dashboard's active-student count drops by one while the total is
    unchanged - matching the Student Directory's ``is_active`` definition.
    """
    before = admin_client.client.get("/api/v1/admin/reports/summary").json()
    student = _student(admin_client)
    with_student = admin_client.client.get("/api/v1/admin/reports/summary").json()

    admin_client.client.delete(
        f"/api/v1/admin/students/{student['id']}", headers=admin_client.headers
    )

    after = admin_client.client.get("/api/v1/admin/reports/summary").json()
    assert with_student["active_students"] == before["active_students"] + 1
    assert with_student["total_students"] == before["total_students"] + 1
    assert after["active_students"] == before["active_students"]
    assert after["total_students"] == before["total_students"]


def test_report_summary_active_students_match_directory_active_filter(
    admin_client, db_cleaner
) -> None:
    """The dashboard active-student count must equal the directory's active count.

    Both endpoints define an active student as a profile whose account
    (``users.is_active``) is true - one definition, one number - and both
    count every row (the directory ``total`` is not truncated by pagination).
    """
    before = admin_client.client.get("/api/v1/admin/reports/summary").json()
    total_before, active_before = before["total_students"], before["active_students"]

    created = [
        _student(
            admin_client,
            student_code="ST-REP-001",
            email="rep001@campus360.example.com",
        ),
        _student(
            admin_client,
            student_code="ST-REP-002",
            email="rep002@campus360.example.com",
        ),
        _student(
            admin_client,
            student_code="ST-REP-003",
            email="rep003@campus360.example.com",
        ),
    ]
    admin_client.client.delete(
        f"/api/v1/admin/students/{created[2]['id']}", headers=admin_client.headers
    )

    after = admin_client.client.get("/api/v1/admin/reports/summary").json()
    active_dir = admin_client.client.get(
        "/api/v1/admin/students?is_active=true", headers=admin_client.headers
    ).json()
    inactive_dir = admin_client.client.get(
        "/api/v1/admin/students?is_active=false", headers=admin_client.headers
    ).json()

    assert after["total_students"] == total_before + 3
    assert after["active_students"] == active_before + 2
    assert active_dir["total"] == after["active_students"]
    assert inactive_dir["total"] == after["total_students"] - after["active_students"]
    assert after["total_students"] == after["active_students"] + inactive_dir["total"]


def test_report_summary_returns_null_for_unsupported_metrics(admin_client, db_cleaner) -> None:
    """CGPA and publications are null - the schema cannot supply them."""
    response = admin_client.client.get("/api/v1/admin/reports/summary")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["average_cgpa"] is None
    assert body["faculty_publication_count"] is None
    # ...and the API EXPLAINS why, so the frontend can hide those tiles.
    assert isinstance(body["notes"], list)
    assert any("average_cgpa" in note for note in body["notes"])
    assert any("publication" in note for note in body["notes"])


def test_report_summary_attendance_rate_counts_late_as_attended(admin_client, db_cleaner) -> None:
    """PRESENT and LATE both count as attended.

    Two sessions: one PRESENT, one LATE -> 100% attended, not 50%.
    """
    course = _course(admin_client)
    student = _student(admin_client)
    _seed_attendance(course["id"], student["id"], ["present", "late"])

    response = admin_client.client.get("/api/v1/admin/reports/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["institution_attendance_rate"] == 100.0


def test_report_summary_pass_percentage_uses_the_40_percent_mark(admin_client, db_cleaner) -> None:
    """A student at 40% PASSES; a student at 39% does not.

    The pass mark is the documented institutional constant
    ``services.admin.PASS_MARK_PERCENT`` (40%).
    """
    course = _course(admin_client)
    passing = _student(admin_client, email="pass@campus360.example.com", student_code="ST-PASS")
    failing = _student(admin_client, email="fail@campus360.example.com", student_code="ST-FAIL")

    _seed_mark(passing["id"], course["id"], 40, 100)  # exactly 40% -> passes
    _seed_mark(failing["id"], course["id"], 39, 100)  # 39% -> fails

    response = admin_client.client.get("/api/v1/admin/reports/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pass_percentage"] == 50.0


def test_report_summary_pass_percentage_is_null_when_nothing_is_graded(
    admin_client, db_cleaner
) -> None:
    """No marks -> the pass percentage is null, not 0.

    "Nobody has been graded" and "everybody failed" are different claims,
    and only the first one is true here.
    """
    response = admin_client.client.get("/api/v1/admin/reports/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pass_percentage"] is None


# ---------------------------------------------------------------------------
# Enrollment report
# ---------------------------------------------------------------------------
def test_enrollment_report_counts_active_enrollments(admin_client, db_cleaner) -> None:
    """Only ACTIVE enrolments are counted; dropped ones are history."""
    course = _course(admin_client)
    student = _student(admin_client)
    created = admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student["id"], "course_id": course["id"]},
        headers=admin_client.headers,
    )
    assert created.status_code == 201, created.text

    response = admin_client.client.get("/api/v1/admin/reports/enrollments")
    assert response.status_code == 200, response.text
    body = response.json()
    row = next(item for item in body["courses"] if item["course_id"] == course["id"])
    assert row["enrolled_students"] == 1
    assert body["total_enrollments"] >= 1

    # Dropping the enrolment removes it from the ACTIVE count.
    admin_client.client.patch(
        f"/api/v1/admin/enrollments/{created.json()['id']}/status",
        params={"status": "dropped"},
        headers=admin_client.headers,
    )
    after = admin_client.client.get("/api/v1/admin/reports/enrollments")
    assert after.status_code == 200, after.text
    row_after = next(item for item in after.json()["courses"] if item["course_id"] == course["id"])
    assert row_after["enrolled_students"] == 0


def test_enrollment_report_groups_by_department(admin_client, db_cleaner) -> None:
    """The report breaks the totals down per department."""
    course = _course(admin_client, department="Computer Science")
    student = _student(admin_client)
    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student["id"], "course_id": course["id"]},
        headers=admin_client.headers,
    )

    response = admin_client.client.get("/api/v1/admin/reports/enrollments")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["by_department"]["Computer Science"] >= 1


# ---------------------------------------------------------------------------
# Attendance report
# ---------------------------------------------------------------------------
def test_attendance_report_is_per_course(admin_client, db_cleaner) -> None:
    """The attendance report breaks the rates down per course."""
    course = _course(admin_client)
    student = _student(admin_client)
    # 3 sessions: 2 present, 1 absent -> 66.67% attended.
    _seed_attendance(course["id"], student["id"], ["present", "present", "absent"])

    response = admin_client.client.get("/api/v1/admin/reports/attendance")
    assert response.status_code == 200, response.text
    body = response.json()
    row = next(item for item in body["courses"] if item["course_id"] == course["id"])
    assert row["total_records"] == 3
    assert row["present_count"] == 2
    assert row["absent_count"] == 1
    assert row["late_count"] == 0
    assert row["attendance_percentage"] == 66.67
    assert body["institution_attendance_rate"] == 66.67


def test_attendance_report_counts_late_as_attended(admin_client, db_cleaner) -> None:
    """LATE is reported separately but counts towards the attended rate."""
    course = _course(admin_client)
    student = _student(admin_client)
    _seed_attendance(course["id"], student["id"], ["present", "late"])

    response = admin_client.client.get("/api/v1/admin/reports/attendance")
    assert response.status_code == 200, response.text
    row = next(item for item in response.json()["courses"] if item["course_id"] == course["id"])
    assert row["late_count"] == 1
    assert row["attendance_percentage"] == 100.0


def test_attendance_report_percentage_is_null_without_records(admin_client, db_cleaner) -> None:
    """A course with no attendance records has a null rate, not 0%."""
    course = _course(admin_client)

    response = admin_client.client.get("/api/v1/admin/reports/attendance")
    assert response.status_code == 200, response.text
    row = next(item for item in response.json()["courses"] if item["course_id"] == course["id"])
    assert row["total_records"] == 0
    assert row["attendance_percentage"] is None
