"""
Faculty authorization tests (backend/tests/test_faculty_authz.py).

Covers the two safety properties that matter most for faculty APIs:

  * Non-faculty roles (student, admin) cannot reach faculty routes (403).
  * A faculty member cannot touch courses they do not LEAD (403) -
    ``courses.lead_faculty_id`` is the sole ownership authority.

Course rows are created directly via SQLAlchemy (rather than through the
admin API) so these tests exercise the service layer's ownership guard
without depending on the admin endpoints.
"""

from app.db.session import SessionLocal
from app.models import Course, User


def _create_course_in_db(lead_faculty_id: int) -> int:
    """Create a course directly in the DB. Returns course id."""
    db = SessionLocal()
    try:
        course = Course(
            code="CS-301",
            title="Advanced Data Structures",
            credits=4,
            department="Computer Science",
            lead_faculty_id=lead_faculty_id,
        )
        db.add(course)
        db.commit()
        db.refresh(course)
        return course.id
    finally:
        db.close()


def _faculty_profile_id_from_user(user_id: int) -> int:
    """Look up a faculty user's FacultyProfile id from the database."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        assert user is not None and user.faculty_profile is not None
        return user.faculty_profile.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Role-based access
# ---------------------------------------------------------------------------


def test_student_cannot_access_faculty_routes(student_client, db_cleaner) -> None:
    """A STUDENT gets 403 on every faculty endpoint (role gate)."""
    response = student_client.client.get("/api/v1/faculty/assignments")
    assert response.status_code == 403, response.text

    response = student_client.client.post(
        "/api/v1/faculty/assignments",
        json={"course_id": 1, "title": "X", "due_at": "2026-12-01T23:59:00Z"},
        headers=student_client.headers,
    )
    assert response.status_code == 403, response.text

    response = student_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": 1,
            "course_id": 1,
            "component": "internal",
            "score": 20,
            "max_score": 30,
        },
        headers=student_client.headers,
    )
    assert response.status_code == 403, response.text

    response = student_client.client.post(
        "/api/v1/faculty/attendance/batch",
        json={"timetable_entry_id": 1, "attendance_date": "2026-10-20", "records": []},
        headers=student_client.headers,
    )
    assert response.status_code == 403, response.text


def test_admin_cannot_access_faculty_routes(admin_client, db_cleaner) -> None:
    """An ADMIN gets 403 on faculty routes (faculty-only router)."""
    response = admin_client.client.get("/api/v1/faculty/assignments")
    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# Course-ownership enforcement
# ---------------------------------------------------------------------------


def test_faculty_cannot_access_another_facultys_course(
    faculty_client, faculty_client2, db_cleaner
) -> None:
    """Faculty B cannot touch a course led by faculty A (403, not 404)."""
    # Fixture faculties already have FacultyProfiles (conftest make_user)
    faculty_a_profile_id = _faculty_profile_id_from_user(faculty_client.user["id"])

    # Faculty A leads a course
    course_id = _create_course_in_db(faculty_a_profile_id)

    # Faculty B lists assignments for faculty A's course -> 403
    response = faculty_client2.client.get(
        f"/api/v1/faculty/assignments?course_id={course_id}"
    )
    assert response.status_code == 403, response.text

    # Faculty B creates an assignment on faculty A's course -> 403
    response = faculty_client2.client.post(
        "/api/v1/faculty/assignments",
        json={
            "course_id": course_id,
            "title": "Should Fail",
            "due_at": "2026-12-01T23:59:00Z",
        },
        headers=faculty_client2.headers,
    )
    assert response.status_code == 403, response.text

    # Faculty A creates an assignment on their own course -> 201
    a_resp = faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json={
            "course_id": course_id,
            "title": "Owned Assignment",
            "due_at": "2026-12-01T23:59:00Z",
        },
        headers=faculty_client.headers,
    )
    assert a_resp.status_code == 201, a_resp.text
    assignment_id = a_resp.json()["id"]

    # Faculty B reads faculty A's assignment -> 403
    response = faculty_client2.client.get(
        f"/api/v1/faculty/assignments/{assignment_id}"
    )
    assert response.status_code == 403, response.text

    # Faculty B updates faculty A's assignment -> 403
    response = faculty_client2.client.patch(
        f"/api/v1/faculty/assignments/{assignment_id}",
        json={"title": "Hacked"},
        headers=faculty_client2.headers,
    )
    assert response.status_code == 403, response.text

    # Faculty B deactivates faculty A's assignment -> 403
    response = faculty_client2.client.delete(
        f"/api/v1/faculty/assignments/{assignment_id}",
        headers=faculty_client2.headers,
    )
    assert response.status_code == 403, response.text


def test_faculty_can_crud_own_course_assignments(
    faculty_client, db_cleaner
) -> None:
    """Faculty A can full CRUD assignments on courses they lead."""
    faculty_profile_id = _faculty_profile_id_from_user(faculty_client.user["id"])
    course_id = _create_course_in_db(faculty_profile_id)

    # Create
    response = faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json={
            "course_id": course_id,
            "title": "Lab Exercise 1",
            "due_at": "2026-11-01T23:59:00Z",
            "weightage_percent": 15.0,
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 201, response.text
    assignment = response.json()
    assert assignment["title"] == "Lab Exercise 1"
    assert assignment["course_id"] == course_id

    # List
    response = faculty_client.client.get(
        f"/api/v1/faculty/assignments?course_id={course_id}"
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1

    # Get one
    response = faculty_client.client.get(
        f"/api/v1/faculty/assignments/{assignment['id']}"
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == assignment["id"]

    # Update
    response = faculty_client.client.patch(
        f"/api/v1/faculty/assignments/{assignment['id']}",
        json={"title": "Lab Exercise 1 (Revised)"},
        headers=faculty_client.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Lab Exercise 1 (Revised)"

    # Deactivate
    response = faculty_client.client.delete(
        f"/api/v1/faculty/assignments/{assignment['id']}",
        headers=faculty_client.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False