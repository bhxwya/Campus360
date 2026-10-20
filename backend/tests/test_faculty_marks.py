"""
Faculty marks tests (backend/tests/test_faculty_marks.py).

Covers:

  * Recording a mark (student, course, component, score/max_score 0-100).
  * Computed percentage + grade in the response.
  * Duplicate mark (same student/course/component) -> 409; a score above
    max_score -> 422 validated BEFORE insert (never misread as a duplicate).
  * Cross-faculty 403 on marks of another faculty's course.
  * Score/max_score bounds (0-100 enforced via schema).
  * PATCH /marks/{id}: successful score updates, partial-update semantics
    (omitted fields unchanged), explicit nulls -> 422, scores above the
    resulting max_score -> 422 - rejected updates leave the row untouched.
"""

from app.db.session import SessionLocal
from app.models import User


def _faculty_profile_id(faculty_user_id: int) -> int:
    """Look up a faculty user's FacultyProfile id from the database."""
    db = SessionLocal()
    try:
        user = db.get(User, faculty_user_id)
        assert user is not None and user.faculty_profile is not None
        return user.faculty_profile.id
    finally:
        db.close()


def _setup_course_and_student(admin_client, faculty_client) -> tuple[dict, dict]:
    """Create a course led by faculty, a student, and enrol them."""
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

    student = admin_client.client.post(
        "/api/v1/admin/students",
        json={
            "email": "s1@campus360.example.com",
            "password": "StudentPass123",
            "student_code": "ST-2026-1001",
            "full_name": "Test Student",
        },
        headers=admin_client.headers,
    )
    assert student.status_code == 201, student.text
    student = student.json()

    admin_client.client.post(
        "/api/v1/admin/enrollments",
        json={"student_id": student["id"], "course_id": course["id"]},
        headers=admin_client.headers,
    )

    return course, student


# ---------------------------------------------------------------------------
# Mark recording
# ---------------------------------------------------------------------------


def test_create_mark_returns_computed_percentage_and_grade(
    faculty_client, admin_client, db_cleaner
) -> None:
    """A mark response includes percentage and letter grade."""
    course, student = _setup_course_and_student(admin_client, faculty_client)

    response = faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student["id"],
            "course_id": course["id"],
            "component": "internal",
            "score": 24.0,
            "max_score": 30.0,
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["student_id"] == student["id"]
    assert body["course_id"] == course["id"]
    assert body["component"] == "internal"
    assert body["score"] == 24.0
    assert body["max_score"] == 30.0
    assert body["percentage"] == 80.0  # 24/30 * 100
    assert body["grade"] == "A+"
    assert body["grade_points"] == 9.0


def test_create_mark_duplicate_returns_409(
    faculty_client, admin_client, db_cleaner
) -> None:
    """Same (student, course, component) twice -> 409, not 500."""
    course, student = _setup_course_and_student(admin_client, faculty_client)

    faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student["id"],
            "course_id": course["id"],
            "component": "internal",
            "score": 24.0,
            "max_score": 30.0,
        },
        headers=faculty_client.headers,
    )

    response = faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student["id"],
            "course_id": course["id"],
            "component": "internal",
            "score": 20.0,
            "max_score": 30.0,
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 409, response.text


def test_create_mark_cross_faculty_returns_403(
    faculty_client, faculty_client2, admin_client, db_cleaner
) -> None:
    """Faculty B cannot record marks on faculty A's course (403)."""
    course, student = _setup_course_and_student(admin_client, faculty_client)

    response = faculty_client2.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student["id"],
            "course_id": course["id"],
            "component": "internal",
            "score": 24.0,
            "max_score": 30.0,
        },
        headers=faculty_client2.headers,
    )
    assert response.status_code == 403, response.text


def test_create_mark_score_bounds_422(
    faculty_client, admin_client, db_cleaner
) -> None:
    """Score outside 0-100 or max_score outside 0-100 -> 422."""
    course, student = _setup_course_and_student(admin_client, faculty_client)

    # Score too high
    response = faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student["id"],
            "course_id": course["id"],
            "component": "internal",
            "score": 101.0,
            "max_score": 30.0,
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text

    # max_score too high
    response = faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student["id"],
            "course_id": course["id"],
            "component": "internal",
            "score": 24.0,
            "max_score": 101.0,
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text


def test_list_marks_filters_by_course_and_component(
    faculty_client, admin_client, db_cleaner
) -> None:
    """Mark list can be filtered by course_id and component."""
    course, student = _setup_course_and_student(admin_client, faculty_client)

    faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student["id"],
            "course_id": course["id"],
            "component": "internal",
            "score": 24.0,
            "max_score": 30.0,
        },
        headers=faculty_client.headers,
    )
    faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student["id"],
            "course_id": course["id"],
            "component": "midterm",
            "score": 35.0,
            "max_score": 50.0,
        },
        headers=faculty_client.headers,
    )

    # Filter by component
    response = faculty_client.client.get(
        "/api/v1/faculty/marks",
        params={"course_id": course["id"], "component": "internal"},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["component"] == "internal"

    # Filter by course
    response = faculty_client.client.get(
        "/api/v1/faculty/marks", params={"course_id": course["id"]}
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 2


# ---------------------------------------------------------------------------
# Mark creation error classification (409 vs 422)
# ---------------------------------------------------------------------------


def test_create_mark_score_above_max_returns_422_not_409(
    faculty_client, admin_client, db_cleaner
) -> None:
    """score > max_score is a 422 validation failure, not a 409 duplicate.

    Both values pass the 0-100 schema bounds, so without the pre-insert
    range check this hits the score_within_max CHECK - which must be
    reported as 422, not mislabelled as a duplicate conflict. Nothing may
    be written on failure.
    """
    course, student = _setup_course_and_student(admin_client, faculty_client)

    response = faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student["id"],
            "course_id": course["id"],
            "component": "internal",
            "score": 90.0,
            "max_score": 50.0,
        },
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text

    # Nothing was recorded, so the same tuple is still free...
    listed = faculty_client.client.get(
        "/api/v1/faculty/marks", params={"course_id": course["id"]}
    )
    assert listed.status_code == 200, listed.text
    assert listed.json() == []

    # ...and a corrected payload succeeds (no partial state, no stuck txn).
    retry = faculty_client.client.post(
        "/api/v1/faculty/marks",
        json={
            "student_id": student["id"],
            "course_id": course["id"],
            "component": "internal",
            "score": 45.0,
            "max_score": 50.0,
        },
        headers=faculty_client.headers,
    )
    assert retry.status_code == 201, retry.text


# ---------------------------------------------------------------------------
# PATCH /marks/{mark_id}
# ---------------------------------------------------------------------------


def _create_mark(faculty_client, course_id: int, student_id: int, **overrides) -> dict:
    """POST /faculty/marks with 24/30 defaults; return the parsed body."""
    body: dict = {
        "student_id": student_id,
        "course_id": course_id,
        "component": "internal",
        "score": 24.0,
        "max_score": 30.0,
    }
    body.update(overrides)
    response = faculty_client.client.post(
        "/api/v1/faculty/marks", json=body, headers=faculty_client.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def _get_mark(
    faculty_client, course_id: int, student_id: int, component: str = "internal"
) -> dict:
    """Fetch the one stored mark via the list endpoint (no GET-by-id route)."""
    response = faculty_client.client.get(
        "/api/v1/faculty/marks",
        params={
            "course_id": course_id,
            "student_id": student_id,
            "component": component,
        },
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1, rows
    return rows[0]


def test_update_mark_changes_score_and_recomputes_percentage(
    faculty_client, admin_client, db_cleaner
) -> None:
    """PATCH score -> 200; percentage/grade recompute, other fields stay."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    mark = _create_mark(faculty_client, course["id"], student["id"])

    response = faculty_client.client.patch(
        f"/api/v1/faculty/marks/{mark['id']}",
        json={"score": 27.0},
        headers=faculty_client.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["score"] == 27.0
    assert body["max_score"] == 30.0  # omitted -> unchanged
    assert body["percentage"] == 90.0  # 27/30 * 100
    assert body["grade"] == "O"
    assert body["grade_points"] == 10.0
    assert body["component"] == "internal"
    assert body["student_id"] == student["id"]
    assert body["course_id"] == course["id"]


def test_update_mark_partial_update_leaves_omitted_fields_unchanged(
    faculty_client, admin_client, db_cleaner
) -> None:
    """PATCH only max_score: score, component and ids all stay put."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    mark = _create_mark(faculty_client, course["id"], student["id"])

    response = faculty_client.client.patch(
        f"/api/v1/faculty/marks/{mark['id']}",
        json={"max_score": 50.0},
        headers=faculty_client.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == mark["id"]
    assert body["max_score"] == 50.0
    assert body["score"] == 24.0  # omitted -> unchanged
    assert body["percentage"] == 48.0  # 24/50 * 100
    assert body["component"] == "internal"
    assert body["student_id"] == student["id"]
    assert body["course_id"] == course["id"]


def test_update_mark_explicit_nulls_return_422_and_keep_the_mark(
    faculty_client, admin_client, db_cleaner
) -> None:
    """Explicit score/max_score nulls -> 422, never a NOT NULL 500."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    mark = _create_mark(faculty_client, course["id"], student["id"])

    for null_field in ("score", "max_score"):
        response = faculty_client.client.patch(
            f"/api/v1/faculty/marks/{mark['id']}",
            json={null_field: None},
            headers=faculty_client.headers,
        )
        assert response.status_code == 422, response.text

    stored = _get_mark(faculty_client, course["id"], student["id"])
    assert stored["score"] == 24.0
    assert stored["max_score"] == 30.0


def test_update_mark_score_above_max_returns_422_and_keeps_the_mark(
    faculty_client, admin_client, db_cleaner
) -> None:
    """A score above the effective max_score is rejected before commit."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    mark = _create_mark(faculty_client, course["id"], student["id"])  # 24/30

    # Above the STORED max_score (max_score omitted).
    response = faculty_client.client.patch(
        f"/api/v1/faculty/marks/{mark['id']}",
        json={"score": 90.0},
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text

    # Above a LOWERED max_score sent in the same payload.
    response = faculty_client.client.patch(
        f"/api/v1/faculty/marks/{mark['id']}",
        json={"score": 45.0, "max_score": 40.0},
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text

    stored = _get_mark(faculty_client, course["id"], student["id"])
    assert stored["score"] == 24.0
    assert stored["max_score"] == 30.0