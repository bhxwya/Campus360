"""
Admin API tests for courses and enrollments
(backend/tests/test_admin_courses.py).

Covers the catalogue (create/list/read/update/deactivate) and the
many-to-many link between students and courses.

The enrollment tests pin the database-level guarantees:
  * a duplicate (student, course) pair is impossible -> reported as 409,
  * dropping a course never deletes the row (marks/attendance reference it).
"""

from app.db.session import SessionLocal
from app.models import Course, Enrollment


def _course(admin_client, **overrides) -> dict:
    """POST a course and return the parsed body (asserting 201)."""
    payload = {
        "code": "CS-301",
        "title": "Advanced Data Structures",
        "description": "Trees, graphs and hashing.",
        "credits": 4,
        "department": "Computer Science",
    }
    payload.update(overrides)
    response = admin_client.client.post(
        "/api/v1/admin/courses", json=payload, headers=admin_client.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def _student(admin_client, **overrides) -> dict:
    """POST a student and return the parsed body (asserting 201)."""
    payload = {
        "email": "student@campus360.example.com",
        "password": "StudentPass123",
        "student_code": "ST-2026-1000",
        "full_name": "Test Student",
    }
    payload.update(overrides)
    response = admin_client.client.post(
        "/api/v1/admin/students", json=payload, headers=admin_client.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------
def test_create_course_returns_201(admin_client, db_cleaner) -> None:
    """Creating a course returns the safe projection."""
    body = _course(admin_client)

    assert body["code"] == "CS-301"
    assert body["title"] == "Advanced Data Structures"
    assert body["credits"] == 4
    assert body["department"] == "Computer Science"
    assert body["is_active"] is True
    assert body["lead_faculty_id"] is None
    assert body["enrolled_students"] == 0


def test_duplicate_course_code_returns_409(admin_client, db_cleaner) -> None:
    """A duplicate course code -> 409, not 500."""
    _course(admin_client)

    response = admin_client.client.post(
        "/api/v1/admin/courses",
        json={"code": "CS-301", "title": "Duplicate", "credits": 3},
        headers=admin_client.headers,
    )

    assert response.status_code == 409, response.text


def test_zero_or_negative_credits_returns_422(admin_client, db_cleaner) -> None:
    """The schema mirrors the DB CHECK ``credits > 0`` -> clean 422."""
    response = admin_client.client.post(
        "/api/v1/admin/courses",
        json={"code": "BAD-1", "title": "Bad", "credits": 0},
        headers=admin_client.headers,
    )

    assert response.status_code == 422, response.text


def test_course_with_unknown_lead_faculty_returns_422(admin_client, db_cleaner) -> None:
    """A lead faculty id that does not exist is a clear 422, not a DB error."""
    response = admin_client.client.post(
        "/api/v1/admin/courses",
        json={"code": "CS-999", "title": "Orphan", "credits": 3, "lead_faculty_id": 99999999},
        headers=admin_client.headers,
    )

    assert response.status_code == 422, response.text


def test_list_courses_supports_search_and_filter(admin_client, db_cleaner) -> None:
    """Courses can be searched by code/title and filtered by department."""
    _course(admin_client)
    _course(
        admin_client,
        code="PH-101",
        title="Classical Mechanics",
        department="Physics",
    )

    search = admin_client.client.get("/api/v1/admin/courses", params={"search": "CS-301"})
    assert search.status_code == 200, search.text
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["code"] == "CS-301"

    department = admin_client.client.get("/api/v1/admin/courses", params={"department": "Physics"})
    assert department.status_code == 200, department.text
    assert department.json()["total"] == 1
    assert department.json()["items"][0]["code"] == "PH-101"


def test_get_course_by_id(admin_client, db_cleaner) -> None:
    """One course can be fetched by id."""
    created = _course(admin_client)

    response = admin_client.client.get(f"/api/v1/admin/courses/{created['id']}")

    assert response.status_code == 200, response.text
    assert response.json()["code"] == "CS-301"


def test_get_unknown_course_returns_404(admin_client, db_cleaner) -> None:
    """A missing course is a clean 404."""
    response = admin_client.client.get("/api/v1/admin/courses/99999999")

    assert response.status_code == 404, response.text


def test_patch_course_updates_only_supplied_fields(admin_client, db_cleaner) -> None:
    """A PATCH is partial."""
    created = _course(admin_client)

    response = admin_client.client.patch(
        f"/api/v1/admin/courses/{created['id']}",
        json={"credits": 5},
        headers=admin_client.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["credits"] == 5
    assert body["title"] == "Advanced Data Structures"


def test_deactivate_course_keeps_the_row_and_its_enrollments(admin_client, db_cleaner) -> None:
    """Deactivation hides the course from the catalogue but keeps history."""
    course = _course(admin_client)
    student = _student(admin_client)
    enrollment = admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student["id"], "course_id": course["id"]},
        headers=admin_client.headers,
    )
    assert enrollment.status_code == 201, enrollment.text

    response = admin_client.client.delete(
        f"/api/v1/admin/courses/{course['id']}", headers=admin_client.headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False

    db = SessionLocal()
    try:
        assert db.get(Course, course["id"]) is not None
        # The enrollment SURVIVED the course's deactivation.
        assert db.query(Enrollment).filter(Enrollment.course_id == course["id"]).count() == 1
    finally:
        db.close()


def test_inactive_course_is_excluded_by_the_active_filter(admin_client, db_cleaner) -> None:
    """The ``is_active`` filter separates catalogue entries."""
    course = _course(admin_client)
    admin_client.client.delete(
        f"/api/v1/admin/courses/{course['id']}", headers=admin_client.headers
    )

    active = admin_client.client.get("/api/v1/admin/courses", params={"is_active": True})
    inactive = admin_client.client.get("/api/v1/admin/courses", params={"is_active": False})

    assert active.status_code == 200 and inactive.status_code == 200
    active_codes = {item["code"] for item in active.json()["items"]}
    inactive_codes = {item["code"] for item in inactive.json()["items"]}
    assert "CS-301" not in active_codes
    assert "CS-301" in inactive_codes


# ---------------------------------------------------------------------------
# Enrollments
# ---------------------------------------------------------------------------
def test_create_enrollment_returns_201(admin_client, db_cleaner) -> None:
    """Enrolling a student returns the joined enrollment row."""
    course = _course(admin_client)
    student = _student(admin_client)

    response = admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student["id"], "course_id": course["id"]},
        headers=admin_client.headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["student_id"] == student["id"]
    assert body["student_code"] == "ST-2026-1000"
    assert body["student_name"] == "Test Student"
    assert body["course_id"] == course["id"]
    assert body["course_code"] == "CS-301"
    assert body["course_title"] == "Advanced Data Structures"
    assert body["status"] == "active"


def test_duplicate_enrollment_returns_409(admin_client, db_cleaner) -> None:
    """The UNIQUE(student, course) constraint surfaces as a clean 409."""
    course = _course(admin_client)
    student = _student(admin_client)
    payload = {"student_id": student["id"], "course_id": course["id"]}

    first = admin_client.client.post(
        "/api/v1/admin/enrollments", json=payload, headers=admin_client.headers
    )
    assert first.status_code == 201, first.text

    second = admin_client.client.post(
        "/api/v1/admin/enrollments", json=payload, headers=admin_client.headers
    )

    assert second.status_code == 409, second.text


def test_enrollment_with_unknown_student_returns_404(admin_client, db_cleaner) -> None:
    """Enrolling a student who does not exist is a 404."""
    course = _course(admin_client)

    response = admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": 99999999, "course_id": course["id"]},
        headers=admin_client.headers,
    )

    assert response.status_code == 404, response.text


def test_enrollment_with_unknown_course_returns_404(admin_client, db_cleaner) -> None:
    """Enrolling in a course that does not exist is a 404."""
    student = _student(admin_client)

    response = admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student["id"], "course_id": 99999999},
        headers=admin_client.headers,
    )

    assert response.status_code == 404, response.text


def test_enrolled_course_count_is_reported_on_the_course(admin_client, db_cleaner) -> None:
    """The course reports how many students are actively enrolled."""
    course = _course(admin_client)
    student = _student(admin_client)
    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student["id"], "course_id": course["id"]},
        headers=admin_client.headers,
    )

    response = admin_client.client.get(f"/api/v1/admin/courses/{course['id']}")

    assert response.status_code == 200, response.text
    assert response.json()["enrolled_students"] == 1


def test_enrolled_course_count_is_reported_on_the_student(admin_client, db_cleaner) -> None:
    """The student reports how many courses they are actively taking."""
    course = _course(admin_client)
    student = _student(admin_client)
    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student["id"], "course_id": course["id"]},
        headers=admin_client.headers,
    )

    response = admin_client.client.get(f"/api/v1/admin/students/{student['id']}")

    assert response.status_code == 200, response.text
    assert response.json()["enrolled_courses"] == 1


def test_list_enrollments_filters_by_student_and_course(admin_client, db_cleaner) -> None:
    """The enrollment list can be narrowed by student and by course."""
    course_a = _course(admin_client)
    course_b = _course(admin_client, code="CS-302", title="Databases")
    student = _student(admin_client)
    for course in (course_a, course_b):
        admin_client.client.post(
            "/api/v1/admin/enrollments",
            json={"student_id": student["id"], "course_id": course["id"]},
            headers=admin_client.headers,
        )

    by_student = admin_client.client.get(
        "/api/v1/admin/enrollments", params={"student_id": student["id"]}
    )
    by_course = admin_client.client.get(
        "/api/v1/admin/enrollments", params={"course_id": course_a["id"]}
    )

    assert by_student.status_code == 200, by_student.text
    assert len(by_student.json()) == 2
    assert by_course.status_code == 200, by_course.text
    assert len(by_course.json()) == 1
    assert by_course.json()[0]["course_code"] == "CS-301"


def test_dropping_a_course_keeps_the_enrollment_row(admin_client, db_cleaner) -> None:
    """Dropping a course changes the STATUS; the row is never deleted.

    Attendance and marks reference the enrollment historically, so the
    honest operation is a status change.
    """
    course = _course(admin_client)
    student = _student(admin_client)
    created = admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student["id"], "course_id": course["id"]},
        headers=admin_client.headers,
    )
    assert created.status_code == 201, created.text
    enrollment_id = created.json()["id"]

    response = admin_client.client.patch(
        f"/api/v1/admin/enrollments/{enrollment_id}/status",
        params={"status": "dropped"},
        headers=admin_client.headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "dropped"

    db = SessionLocal()
    try:
        # The row survives...
        assert db.get(Enrollment, enrollment_id) is not None
        assert db.get(Enrollment, enrollment_id).status.value == "dropped"
    finally:
        db.close()

    # ...and the student no longer counts it as an active course.
    student_body = admin_client.client.get(f"/api/v1/admin/students/{student['id']}")
    assert student_body.status_code == 200, student_body.text
    assert student_body.json()["enrolled_courses"] == 0


def test_dropping_an_unknown_enrollment_returns_404(admin_client, db_cleaner) -> None:
    """Changing a missing enrollment is a 404."""
    response = admin_client.client.patch(
        "/api/v1/admin/enrollments/99999999/status",
        params={"status": "dropped"},
        headers=admin_client.headers,
    )

    assert response.status_code == 404, response.text
