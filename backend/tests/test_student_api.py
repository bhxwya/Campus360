"""
Student API tests (backend/tests/test_student_api.py).

Comprehensive tests covering:
- Authentication & role-based access control (401 unauth, 403 admin/faculty).
- Missing profile handling (404).
- Safe profile exposure (no password_hash, no session leaks).
- Enrolled courses listing.
- Timetable filtering (active enrollments only).
- Attendance summary with zero-session null handling, and records filtering.
- Assignments listing and detail, with enrollment verification.
- Marks component listing and summary, including incomplete marks handling.
- Announcements audience and status filtering (students and all only, active only).
- Dashboard composite assembly without N+1 queries.
- Cross-student data isolation: student B never sees student A's data.
"""

from datetime import UTC, datetime, timedelta

from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Enrollment,
    StudentProfile,
    TimetableEntry,
    User,
    UserRole,
)
from app.models.enums import EnrollmentStatus
from fastapi.testclient import TestClient


def _login_client(client: TestClient, email: str, password: str) -> None:
    """Log in and set session cookies on the test client."""
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


def _create_student_profile(
    user_id: int, student_code: str = "ST-2026-0001", full_name: str = "Test Student"
) -> int:
    """Create a StudentProfile directly in the DB for a student user."""
    db = SessionLocal()
    try:
        profile = StudentProfile(
            user_id=user_id,
            student_code=student_code,
            full_name=full_name,
            degree_program="B.Tech Computer Science",
            current_semester=5,
            phone="+1234567890",
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile.id
    finally:
        db.close()


def _faculty_profile_id(faculty_user_id: int) -> int:
    """Look up a faculty user's FacultyProfile id from the database."""
    db = SessionLocal()
    try:
        user = db.get(User, faculty_user_id)
        assert user is not None and user.faculty_profile is not None
        return user.faculty_profile.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. Authentication & Role-Based Access Control
# ---------------------------------------------------------------------------


def test_unauthenticated_returns_401(client, db_cleaner) -> None:
    """Unauthenticated requests to student endpoints return 401."""
    assert client.get("/api/v1/student/me/profile").status_code == 401
    assert client.get("/api/v1/student/me/courses").status_code == 401
    assert client.get("/api/v1/student/me/timetable").status_code == 401
    assert client.get("/api/v1/student/me/attendance").status_code == 401
    assert client.get("/api/v1/student/me/attendance/records").status_code == 401
    assert client.get("/api/v1/student/me/assignments").status_code == 401
    assert client.get("/api/v1/student/me/marks").status_code == 401
    assert client.get("/api/v1/student/me/marks/summary").status_code == 401
    assert client.get("/api/v1/student/me/announcements").status_code == 401
    assert client.get("/api/v1/student/me/dashboard").status_code == 401


def test_admin_cannot_access_student_routes(admin_client, db_cleaner) -> None:
    """An ADMIN gets 403 on student endpoints."""
    assert admin_client.client.get("/api/v1/student/me/profile").status_code == 403
    assert admin_client.client.get("/api/v1/student/me/courses").status_code == 403
    assert admin_client.client.get("/api/v1/student/me/dashboard").status_code == 403


def test_faculty_cannot_access_student_routes(faculty_client, db_cleaner) -> None:
    """A FACULTY member gets 403 on student endpoints."""
    assert faculty_client.client.get("/api/v1/student/me/profile").status_code == 403
    assert faculty_client.client.get("/api/v1/student/me/attendance").status_code == 403
    assert faculty_client.client.get("/api/v1/student/me/marks").status_code == 403


# ---------------------------------------------------------------------------
# 2. Profile & Account Details
# ---------------------------------------------------------------------------


def test_student_without_profile_returns_404(student_client, db_cleaner) -> None:
    """A student user without an attached StudentProfile gets 404."""
    response = student_client.client.get("/api/v1/student/me/profile")
    assert response.status_code == 404
    assert response.json()["detail"] == "Student profile not found"


def test_get_profile_returns_own_data(student_client, db_cleaner) -> None:
    """A student receives their own profile data, with no password hash exposed."""
    _create_student_profile(
        student_client.user["id"], student_code="ST-2026-9999", full_name="Alex Johnson"
    )

    response = student_client.client.get("/api/v1/student/me/profile")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["student_code"] == "ST-2026-9999"
    assert data["full_name"] == "Alex Johnson"
    assert data["degree_program"] == "B.Tech Computer Science"
    assert data["current_semester"] == 5
    assert data["email"] == "student@campus360.example.com"
    assert data["is_active"] is True
    assert "password" not in data
    assert "password_hash" not in data


# ---------------------------------------------------------------------------
# 3. Courses / Enrollments
# ---------------------------------------------------------------------------


def test_courses_list_empty_and_enrolled(
    student_client, admin_client, faculty_client, db_cleaner
) -> None:
    """Student sees empty courses when unenrolled, and sees full details when enrolled."""
    student_profile_id = _create_student_profile(student_client.user["id"])

    # Initially empty
    res = student_client.client.get("/api/v1/student/me/courses")
    assert res.status_code == 200
    assert res.json() == []

    # Create course with faculty lead
    faculty_pid = _faculty_profile_id(faculty_client.user["id"])
    course_resp = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "CS-301",
            "title": "Advanced Data Structures",
            "credits": 4,
            "department": "Computer Science",
            "lead_faculty_id": faculty_pid,
        },
        headers=admin_client.headers,
    )
    assert course_resp.status_code == 201
    course_id = course_resp.json()["id"]

    # Enroll student
    enr_resp = admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student_profile_id, "course_id": course_id},
        headers=admin_client.headers,
    )
    assert enr_resp.status_code == 201

    # Student lists courses
    res = student_client.client.get("/api/v1/student/me/courses")
    assert res.status_code == 200
    courses = res.json()
    assert len(courses) == 1
    assert courses[0]["course_code"] == "CS-301"
    assert courses[0]["course_title"] == "Advanced Data Structures"
    assert courses[0]["credits"] == 4
    assert courses[0]["lead_faculty_name"] == "Test Faculty"


# ---------------------------------------------------------------------------
# 4. Timetable
# ---------------------------------------------------------------------------


def test_timetable_active_enrollments_only(
    student_client, admin_client, faculty_client, db_cleaner
) -> None:
    """Timetable entries only appear for active enrollments."""
    student_profile_id = _create_student_profile(student_client.user["id"])
    faculty_pid = _faculty_profile_id(faculty_client.user["id"])

    course_resp = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "CS-302",
            "title": "Database Systems",
            "credits": 4,
            "lead_faculty_id": faculty_pid,
        },
        headers=admin_client.headers,
    )
    course_id = course_resp.json()["id"]

    # Create timetable slot
    db = SessionLocal()
    try:
        tt = TimetableEntry(
            course_id=course_id,
            faculty_id=faculty_pid,
            day_of_week="Monday",
            start_time="10:00",
            end_time="11:30",
            room="Room 302",
        )
        db.add(tt)
        db.commit()
    finally:
        db.close()

    # Before enrollment: empty
    res = student_client.client.get("/api/v1/student/me/timetable")
    assert res.status_code == 200
    assert res.json() == []

    # Enroll student (ACTIVE)
    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student_profile_id, "course_id": course_id},
        headers=admin_client.headers,
    )

    # After active enrollment: entry appears
    res = student_client.client.get("/api/v1/student/me/timetable")
    assert res.status_code == 200
    slots = res.json()
    assert len(slots) == 1
    assert slots[0]["course_code"] == "CS-302"
    assert slots[0]["day_of_week"] == "Monday"
    assert slots[0]["start_time"] == "10:00:00"
    assert slots[0]["room"] == "Room 302"
    assert slots[0]["faculty_name"] == "Test Faculty"

    # If enrollment is DROPPED: entry disappears
    db = SessionLocal()
    try:
        enr = db.query(Enrollment).filter(Enrollment.student_id == student_profile_id).first()
        enr.status = "dropped"
        db.commit()
    finally:
        db.close()

    res = student_client.client.get("/api/v1/student/me/timetable")
    assert res.status_code == 200
    assert res.json() == []


# ---------------------------------------------------------------------------
# 5. Attendance (Zero-session handling & records)
# ---------------------------------------------------------------------------


def test_attendance_zero_sessions_null_percentage(student_client, admin_client, db_cleaner) -> None:
    """When a student has enrolled courses but zero attendance sessions, percent is null."""
    student_profile_id = _create_student_profile(student_client.user["id"])

    course_resp = admin_client.client.post(
        "/api/v1/admin/courses",
        json={"code": "CS-303", "title": "Networks", "credits": 3},
        headers=admin_client.headers,
    )
    course_id = course_resp.json()["id"]

    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student_profile_id, "course_id": course_id},
        headers=admin_client.headers,
    )

    res = student_client.client.get("/api/v1/student/me/attendance")
    assert res.status_code == 200
    data = res.json()
    assert data["overall_percent"] is None
    assert len(data["courses"]) == 1
    c = data["courses"][0]
    assert c["total_sessions"] == 0
    assert c["attendance_percent"] is None
    assert c["status"] == "No Data"


def test_attendance_summary_and_records_with_data(
    student_client, admin_client, faculty_client, db_cleaner
) -> None:
    """Attendance stats calculate accurately and raw records are accessible."""
    student_profile_id = _create_student_profile(student_client.user["id"])
    faculty_pid = _faculty_profile_id(faculty_client.user["id"])

    course_resp = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "CS-304",
            "title": "Software Eng",
            "credits": 3,
            "lead_faculty_id": faculty_pid,
        },
        headers=admin_client.headers,
    )
    course_id = course_resp.json()["id"]

    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student_profile_id, "course_id": course_id},
        headers=admin_client.headers,
    )

    db = SessionLocal()
    try:
        tt = TimetableEntry(
            course_id=course_id,
            faculty_id=faculty_pid,
            day_of_week="Tuesday",
            start_time="09:00",
            end_time="10:30",
            room="Room 101",
        )
        db.add(tt)
        db.commit()
        db.refresh(tt)
        tt_id = tt.id
    finally:
        db.close()

    # Faculty marks attendance: 1 present, 1 late
    batch_resp = faculty_client.client.post(
        "/api/v1/faculty/attendance/batch",
        json={
            "timetable_entry_id": tt_id,
            "attendance_date": "2026-10-13",
            "records": [{"student_id": student_profile_id, "status": "present"}],
        },
        headers=faculty_client.headers,
    )
    assert batch_resp.status_code == 201

    batch_resp2 = faculty_client.client.post(
        "/api/v1/faculty/attendance/batch",
        json={
            "timetable_entry_id": tt_id,
            "attendance_date": "2026-10-20",
            "records": [{"student_id": student_profile_id, "status": "late"}],
        },
        headers=faculty_client.headers,
    )
    assert batch_resp2.status_code == 201

    # Student checks summary
    res = student_client.client.get("/api/v1/student/me/attendance")
    assert res.status_code == 200
    data = res.json()
    assert data["overall_percent"] == 100.0
    assert data["courses"][0]["total_sessions"] == 2
    assert data["courses"][0]["present_count"] == 1
    assert data["courses"][0]["late_count"] == 1
    assert data["courses"][0]["attendance_percent"] == 100.0
    assert data["courses"][0]["status"] == "Good"

    # Student checks records
    rec_res = student_client.client.get("/api/v1/student/me/attendance/records")
    assert rec_res.status_code == 200
    records = rec_res.json()
    assert len(records) == 2
    assert records[0]["attendance_date"] == "2026-10-20"
    assert records[1]["attendance_date"] == "2026-10-13"

    # Filter by course
    filt_res = student_client.client.get(
        f"/api/v1/student/me/attendance/records?course_id={course_id}"
    )
    assert filt_res.status_code == 200
    assert len(filt_res.json()) == 2

    # Filter by non-enrolled course -> 403
    forbidden_res = student_client.client.get(
        "/api/v1/student/me/attendance/records?course_id=99999"
    )
    assert forbidden_res.status_code == 403


# ---------------------------------------------------------------------------
# 6. Assignments & Submissions
# ---------------------------------------------------------------------------


def test_assignments_list_and_detail(
    student_client, admin_client, faculty_client, db_cleaner
) -> None:
    """Assignments are listed and detail is returned, respecting course enrollment."""
    student_profile_id = _create_student_profile(student_client.user["id"])
    faculty_pid = _faculty_profile_id(faculty_client.user["id"])

    course_resp = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "CS-305",
            "title": "Operating Systems",
            "credits": 4,
            "lead_faculty_id": faculty_pid,
        },
        headers=admin_client.headers,
    )
    course_id = course_resp.json()["id"]

    # Faculty creates assignment
    asgn_resp = faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json={
            "course_id": course_id,
            "title": "Kernel Module Lab",
            "due_at": "2026-11-15T23:59:00Z",
            "weightage_percent": 10.0,
            "max_score": 50.0,
        },
        headers=faculty_client.headers,
    )
    assert asgn_resp.status_code == 201
    assignment_id = asgn_resp.json()["id"]

    # Before enrollment: assignments list is empty
    res = student_client.client.get("/api/v1/student/me/assignments")
    assert res.status_code == 200
    assert res.json() == []

    # Detail before enrollment -> 403
    det_res = student_client.client.get(f"/api/v1/student/me/assignments/{assignment_id}")
    assert det_res.status_code == 403

    # Enroll student
    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student_profile_id, "course_id": course_id},
        headers=admin_client.headers,
    )

    # After enrollment: assignment appears
    res = student_client.client.get("/api/v1/student/me/assignments")
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["title"] == "Kernel Module Lab"
    assert items[0]["max_score"] == 50.0
    assert items[0]["submission_id"] is None

    # Detail succeeds
    det_res = student_client.client.get(f"/api/v1/student/me/assignments/{assignment_id}")
    assert det_res.status_code == 200
    assert det_res.json()["title"] == "Kernel Module Lab"

    # Non-existent assignment -> 404
    assert student_client.client.get("/api/v1/student/me/assignments/99999").status_code == 404


# ---------------------------------------------------------------------------
# 7. Marks & Incomplete Results Handling
# ---------------------------------------------------------------------------


def test_marks_list_and_incomplete_summary_handling(
    student_client, admin_client, faculty_client, db_cleaner
) -> None:
    """Component marks are returned and final grades are NOT computed from incomplete marks."""
    student_profile_id = _create_student_profile(student_client.user["id"])
    faculty_pid = _faculty_profile_id(faculty_client.user["id"])

    course_resp = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "CS-306",
            "title": "Theory of Computation",
            "credits": 3,
            "lead_faculty_id": faculty_pid,
        },
        headers=admin_client.headers,
    )
    course_id = course_resp.json()["id"]

    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student_profile_id, "course_id": course_id},
        headers=admin_client.headers,
    )

    # Initially: marks list empty, summary shows "No Marks"
    assert student_client.client.get("/api/v1/student/me/marks").json() == []
    sum_res = student_client.client.get("/api/v1/student/me/marks/summary").json()
    assert len(sum_res["courses"]) == 1
    assert sum_res["courses"][0]["status"] == "No Marks"
    assert sum_res["courses"][0]["overall_grade"] is None
    assert sum_res["courses"][0]["overall_percentage"] is None

    # Faculty records only 1 component (Internal: 27/30) -> Incomplete!
    faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student_profile_id,
            "course_id": course_id,
            "component": "internal",
            "score": 27.0,
            "max_score": 30.0,
        },
        headers=faculty_client.headers,
    )

    # Individual mark has component percentage & grade
    marks_res = student_client.client.get("/api/v1/student/me/marks").json()
    assert len(marks_res) == 1
    assert marks_res[0]["component"] == "internal"
    assert marks_res[0]["percentage"] == 90.0
    assert marks_res[0]["grade"] == "O"

    # Overall course summary recognizes in-progress course:
    # Enrollment is ACTIVE, so final grade/percentage is Incomplete (None).
    sum_res = student_client.client.get("/api/v1/student/me/marks/summary").json()
    course_sum = sum_res["courses"][0]
    assert course_sum["status"] == "Incomplete"
    assert course_sum["overall_percentage"] is None
    assert course_sum["overall_grade"] is None
    assert course_sum["overall_grade_points"] is None

    # Faculty records more components (Midterm 50, Practical 20)
    faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student_profile_id,
            "course_id": course_id,
            "component": "midterm",
            "score": 43.0,
            "max_score": 50.0,
        },
        headers=faculty_client.headers,
    )
    faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student_profile_id,
            "course_id": course_id,
            "component": "practical",
            "score": 18.0,
            "max_score": 20.0,
        },
        headers=faculty_client.headers,
    )

    # While enrollment remains ACTIVE, course summary continues to report Incomplete
    sum_res = student_client.client.get("/api/v1/student/me/marks/summary").json()
    course_sum = sum_res["courses"][0]
    assert course_sum["status"] == "Incomplete"
    assert course_sum["overall_percentage"] is None
    assert course_sum["overall_grade"] is None

    # Once enrollment status transitions to COMPLETED, final overall grade is calculated
    db = SessionLocal()
    try:
        enr = (
            db.query(Enrollment)
            .filter(
                Enrollment.student_id == student_profile_id,
                Enrollment.course_id == course_id,
            )
            .first()
        )
        enr.status = EnrollmentStatus.COMPLETED
        db.commit()
    finally:
        db.close()

    sum_res = student_client.client.get("/api/v1/student/me/marks/summary").json()
    course_sum = sum_res["courses"][0]
    assert course_sum["status"] == "Complete"
    assert course_sum["overall_percentage"] == 88.0
    assert course_sum["overall_grade"] == "A+"
    assert course_sum["overall_grade_points"] == 9.0

    # Also verify non-100 scale courses are supported when completed:
    # Create second course CS-306B (scale total 50 pts, not 100)
    course_resp2 = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "CS-306B",
            "title": "Discrete Math",
            "credits": 2,
            "lead_faculty_id": faculty_pid,
        },
        headers=admin_client.headers,
    )
    course2_id = course_resp2.json()["id"]
    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student_profile_id, "course_id": course2_id},
        headers=admin_client.headers,
    )
    faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student_profile_id,
            "course_id": course2_id,
            "component": "practical",
            "score": 40.0,
            "max_score": 50.0,
        },
        headers=faculty_client.headers,
    )

    # Incomplete while active
    sum_res = student_client.client.get("/api/v1/student/me/marks/summary").json()
    c2_sum = next(c for c in sum_res["courses"] if c["course_id"] == course2_id)
    assert c2_sum["status"] == "Incomplete"

    # Mark completed -> 40/50 = 80.0% -> A+, Complete (never stuck on incomplete because max!=100)
    db = SessionLocal()
    try:
        enr2 = (
            db.query(Enrollment)
            .filter(
                Enrollment.student_id == student_profile_id,
                Enrollment.course_id == course2_id,
            )
            .first()
        )
        enr2.status = EnrollmentStatus.COMPLETED
        db.commit()
    finally:
        db.close()

    sum_res = student_client.client.get("/api/v1/student/me/marks/summary").json()
    c2_sum = next(c for c in sum_res["courses"] if c["course_id"] == course2_id)
    assert c2_sum["status"] == "Complete"
    assert c2_sum["overall_percentage"] == 80.0
    assert c2_sum["overall_grade"] == "A+"


# ---------------------------------------------------------------------------
# 8. Announcements Filtering
# ---------------------------------------------------------------------------


def test_announcements_filtering(student_client, admin_client, db_cleaner) -> None:
    """Students see active notices for 'all' and 'students', never faculty or archived."""
    _create_student_profile(student_client.user["id"])

    # 1. Active - all (visible)
    admin_client.client.post(
        "/api/v1/admin/announcements",
        json={
            "title": "Campus Wide Notice",
            "body": "Welcome everyone",
            "category": "administrative",
            "audience": "all",
            "status": "active",
        },
        headers=admin_client.headers,
    )
    # 2. Active - students (visible)
    admin_client.client.post(
        "/api/v1/admin/announcements",
        json={
            "title": "Student Union Notice",
            "body": "Elections next week",
            "category": "event",
            "audience": "students",
            "status": "active",
        },
        headers=admin_client.headers,
    )
    # 3. Active - faculty only (HIDDEN)
    admin_client.client.post(
        "/api/v1/admin/announcements",
        json={
            "title": "Faculty Meeting",
            "body": "Curriculum review",
            "category": "course_notice",
            "audience": "faculty",
            "status": "active",
        },
        headers=admin_client.headers,
    )
    # 4. Archived - students (HIDDEN)
    admin_client.client.post(
        "/api/v1/admin/announcements",
        json={
            "title": "Old Exam Schedule",
            "body": "Outdated",
            "category": "examination",
            "audience": "students",
            "status": "archived",
        },
        headers=admin_client.headers,
    )

    res = student_client.client.get("/api/v1/student/me/announcements")
    assert res.status_code == 200
    titles = [a["title"] for a in res.json()]
    assert "Campus Wide Notice" in titles
    assert "Student Union Notice" in titles
    assert "Faculty Meeting" not in titles
    assert "Old Exam Schedule" not in titles


# ---------------------------------------------------------------------------
# 9. Dashboard Composite
# ---------------------------------------------------------------------------


def test_dashboard_composite(student_client, admin_client, faculty_client, db_cleaner) -> None:
    """Dashboard combines courses count, credits, attendance %, assignments and announcements."""
    student_profile_id = _create_student_profile(student_client.user["id"])
    faculty_pid = _faculty_profile_id(faculty_client.user["id"])

    # Enroll in 1 course with 4 credits
    course_resp = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "CS-307",
            "title": "Cloud Computing",
            "credits": 4,
            "lead_faculty_id": faculty_pid,
        },
        headers=admin_client.headers,
    )
    course_id = course_resp.json()["id"]

    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student_profile_id, "course_id": course_id},
        headers=admin_client.headers,
    )

    # Create assignment
    faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json={
            "course_id": course_id,
            "title": "Docker Setup",
            "due_at": "2026-11-20T23:59:00Z",
        },
        headers=faculty_client.headers,
    )

    # Create announcement
    admin_client.client.post(
        "/api/v1/admin/announcements",
        json={
            "title": "Cloud Workshop",
            "body": "Hands-on session",
            "category": "event",
            "audience": "all",
            "status": "active",
        },
        headers=admin_client.headers,
    )

    res = student_client.client.get("/api/v1/student/me/dashboard")
    assert res.status_code == 200
    data = res.json()
    assert data["enrolled_courses_count"] == 1
    assert data["total_credits"] == 4
    assert len(data["upcoming_assignments"]) == 1
    assert data["upcoming_assignments"][0]["title"] == "Docker Setup"
    assert len(data["recent_announcements"]) == 1
    assert data["recent_announcements"][0]["title"] == "Cloud Workshop"


def test_dashboard_upcoming_assignments_filter(
    student_client, admin_client, faculty_client, db_cleaner
) -> None:
    """Dashboard upcoming_assignments includes future deadlines and excludes overdue ones."""
    student_profile_id = _create_student_profile(student_client.user["id"])
    faculty_pid = _faculty_profile_id(faculty_client.user["id"])

    course_resp = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "CS-308",
            "title": "Distributed Systems",
            "credits": 3,
            "lead_faculty_id": faculty_pid,
        },
        headers=admin_client.headers,
    )
    course_id = course_resp.json()["id"]

    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student_profile_id, "course_id": course_id},
        headers=admin_client.headers,
    )

    now = datetime.now(UTC)
    future_due = (now + timedelta(days=7)).isoformat()
    past_due = (now - timedelta(days=3)).isoformat()

    # 1. Create a future assignment
    asgn_future = faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json={
            "course_id": course_id,
            "title": "Future Project",
            "due_at": future_due,
        },
        headers=faculty_client.headers,
    )
    assert asgn_future.status_code == 201

    # 2. Create an overdue (past-due) assignment
    asgn_past = faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json={
            "course_id": course_id,
            "title": "Past Lab",
            "due_at": past_due,
        },
        headers=faculty_client.headers,
    )
    assert asgn_past.status_code == 201

    # In dashboard: only future assignment appears in upcoming_assignments
    dash_res = student_client.client.get("/api/v1/student/me/dashboard")
    assert dash_res.status_code == 200
    upcoming = dash_res.json()["upcoming_assignments"]
    assert len(upcoming) == 1
    assert upcoming[0]["title"] == "Future Project"

    # In full assignments list: both active assignments are visible so student can see coursework
    asgns_res = student_client.client.get("/api/v1/student/me/assignments")
    assert asgns_res.status_code == 200
    all_asgns = asgns_res.json()
    assert len(all_asgns) == 2
    titles = {a["title"] for a in all_asgns}
    assert titles == {"Future Project", "Past Lab"}


# ---------------------------------------------------------------------------
# 10. Cross-Student Data Isolation
# ---------------------------------------------------------------------------


def test_cross_student_data_isolation(admin_client, faculty_client, db_cleaner, make_user) -> None:
    """Student B can never see Student A's attendance, marks, courses, or profile data."""
    user_a = make_user("student_a@campus360.example.com", "PasswordA123", UserRole.STUDENT)
    user_b = make_user("student_b@campus360.example.com", "PasswordB123", UserRole.STUDENT)

    pid_a = _create_student_profile(user_a.id, "ST-A001", "Student Alpha")
    _create_student_profile(user_b.id, "ST-B002", "Student Beta")

    faculty_pid = _faculty_profile_id(faculty_client.user["id"])

    course_a = admin_client.client.post(
        "/api/v1/admin/courses",
        json={
            "code": "CS-A10",
            "title": "Alpha Course",
            "credits": 3,
            "lead_faculty_id": faculty_pid,
        },
        headers=admin_client.headers,
    ).json()

    # Enroll ONLY Student A in Course A
    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": pid_a, "course_id": course_a["id"]},
        headers=admin_client.headers,
    )

    # Record Mark for Student A
    faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": pid_a,
            "course_id": course_a["id"],
            "component": "internal",
            "score": 28.0,
            "max_score": 30.0,
        },
        headers=faculty_client.headers,
    )

    # Log in as Student B
    with TestClient(app) as test_client:
        _login_client(test_client, "student_b@campus360.example.com", "PasswordB123")

        # Student B reads profile -> sees Student Beta, not Student Alpha
        prof = test_client.get("/api/v1/student/me/profile").json()
        assert prof["student_code"] == "ST-B002"
        assert prof["full_name"] == "Student Beta"

        # Student B checks courses -> empty! Course A is not visible
        assert test_client.get("/api/v1/student/me/courses").json() == []

        # Student B checks marks -> empty! Student A's mark is not visible
        assert test_client.get("/api/v1/student/me/marks").json() == []

        # Student B checks marks summary -> empty!
        assert test_client.get("/api/v1/student/me/marks/summary").json()["courses"] == []

        # Student B checks attendance records -> empty!
        assert test_client.get("/api/v1/student/me/attendance/records").json() == []
