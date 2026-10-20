"""
Faculty attendance tests (backend/tests/test_faculty_attendance.py).

Covers:

  * Atomic attendance batch: all records inserted in one transaction.
  * Duplicate attendance (same student/timetable_entry/date) -> 409, whole batch rolls back.
  * Cross-faculty 403 on attendance for another faculty's course.
  * Timetable entry must belong to a course the faculty leads.
"""

from app.db.session import SessionLocal
from app.models import TimetableEntry, User


def _faculty_profile_id(faculty_user_id: int) -> int:
    """Look up a faculty user's FacultyProfile id from the database."""
    db = SessionLocal()
    try:
        user = db.get(User, faculty_user_id)
        assert user is not None and user.faculty_profile is not None
        return user.faculty_profile.id
    finally:
        db.close()


def _setup_course_students_timetable(admin_client, faculty_client) -> tuple[dict, list[dict], dict]:
    """Create a course led by faculty, two students, and a timetable entry."""
    course = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "CS-301",
            "title": "Advanced Data Structures",
            "credits": 4,
            "department": "Computer Science",
            "lead_faculty_id": _faculty_profile_id(faculty_client.user["id"]),
        },
        headers=admin_client.headers,
    )
    assert course.status_code == 201, course.text
    course = course.json()

    students = []
    for i in range(2):
        student = admin_client.client.post(
            "/api/v1/admin/students",
            json={
                "email": f"s{i+1}@campus360.example.com",
                "password": "StudentPass123",
                "student_code": f"ST-2026-100{i+1}",
                "full_name": f"Test Student {i+1}",
            },
            headers=admin_client.headers,
        )
        assert student.status_code == 201, student.text
        students.append(student.json())

        admin_client.client.post(
            "/api/v1/admin/enrollments",
            json={"student_id": students[-1]["id"], "course_id": course["id"]},
            headers=admin_client.headers,
        )

    # Create a timetable entry for this course
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        tt = TimetableEntry(
            course_id=course["id"],
            day_of_week="Monday",
            start_time="09:00",
            end_time="10:00",
            room="Room 101",
        )
        db.add(tt)
        db.commit()
        db.refresh(tt)
        timetable_entry = {"id": tt.id, "course_id": course["id"]}
    finally:
        db.close()

    return course, students, timetable_entry


# ---------------------------------------------------------------------------
# Atomic attendance batch
# ---------------------------------------------------------------------------


def test_attendance_batch_marks_all_records(
    faculty_client, admin_client, db_cleaner
) -> None:
    """A valid batch marks every student and returns the count."""
    course, students, timetable_entry = _setup_course_students_timetable(
        admin_client, faculty_client
    )

    response = faculty_client.client.post(
        "/api/v1/faculty/attendance/batch",
        json={
            "timetable_entry_id": timetable_entry["id"],
            "attendance_date": "2026-10-20",
            "records": [
                {"student_id": students[0]["id"], "status": "present"},
                {"student_id": students[1]["id"], "status": "late"},
            ],
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["marked_count"] == 2
    assert body["skipped_duplicate_student_ids"] == []


def test_attendance_batch_duplicate_returns_409_and_rolls_back(
    faculty_client, admin_client, db_cleaner
) -> None:
    """A duplicate (student, timetable_entry, date) -> 409, nothing inserted."""
    course, students, timetable_entry = _setup_course_students_timetable(
        admin_client, faculty_client
    )

    # First batch succeeds
    response = faculty_client.client.post(
        "/api/v1/faculty/attendance/batch",
        json={
            "timetable_entry_id": timetable_entry["id"],
            "attendance_date": "2026-10-20",
            "records": [
                {"student_id": students[0]["id"], "status": "present"},
            ],
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 201, response.text

    # Second batch with one duplicate + one new student -> 409, nothing inserted
    response = faculty_client.client.post(
        "/api/v1/faculty/attendance/batch",
        json={
            "timetable_entry_id": timetable_entry["id"],
            "attendance_date": "2026-10-20",
            "records": [
                {"student_id": students[0]["id"], "status": "absent"},  # duplicate
                {"student_id": students[1]["id"], "status": "present"},  # new, but rolled back
            ],
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 409, response.text

    # Verify the NEW student was NOT marked (rollback worked)
    from app.db.session import SessionLocal
    from app.models.attendance import AttendanceRecord
    db = SessionLocal()
    try:
        count = db.query(AttendanceRecord).filter(
            AttendanceRecord.timetable_entry_id == timetable_entry["id"],
            AttendanceRecord.attendance_date == __import__("datetime").date(2026, 10, 20),
            AttendanceRecord.student_id == students[1]["id"],
        ).count()
        assert count == 0, "Rollback failed: second student was marked despite 409"
    finally:
        db.close()


def test_attendance_batch_cross_faculty_returns_403(
    faculty_client, faculty_client2, admin_client, db_cleaner
) -> None:
    """Faculty B cannot mark attendance on faculty A's course (403)."""
    course, students, timetable_entry = _setup_course_students_timetable(
        admin_client, faculty_client
    )

    response = faculty_client2.client.post(
        "/api/v1/faculty/attendance/batch",
        json={
            "timetable_entry_id": timetable_entry["id"],
            "attendance_date": "2026-10-20",
            "records": [
                {"student_id": students[0]["id"], "status": "present"},
            ],
        },
        headers=faculty_client2.headers,
    )
    assert response.status_code == 403, response.text


def test_attendance_batch_unknown_timetable_entry_returns_404(
    faculty_client, db_cleaner
) -> None:
    """A missing timetable entry returns 404."""
    response = faculty_client.client.post(
        "/api/v1/faculty/attendance/batch",
        json={
            "timetable_entry_id": 99999999,
            "attendance_date": "2026-10-20",
            "records": [
                {"student_id": 1, "status": "present"},
            ],
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 404, response.text


def test_attendance_batch_unknown_student_returns_404(
    faculty_client, admin_client, db_cleaner
) -> None:
    """A missing student in the batch returns 404."""
    course, students, timetable_entry = _setup_course_students_timetable(
        admin_client, faculty_client
    )

    response = faculty_client.client.post(
        "/api/v1/faculty/attendance/batch",
        json={
            "timetable_entry_id": timetable_entry["id"],
            "attendance_date": "2026-10-20",
            "records": [
                {"student_id": 99999999, "status": "present"},
            ],
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 404, response.text


def test_student_cannot_mark_attendance(student_client, db_cleaner) -> None:
    """A STUDENT gets 403 on the attendance batch endpoint."""
    response = student_client.client.post(
        "/api/v1/faculty/attendance/batch",
        json={
            "timetable_entry_id": 1,
            "attendance_date": "2026-10-20",
            "records": [],
        },
        headers=student_client.headers,
    )
    assert response.status_code == 403, response.text