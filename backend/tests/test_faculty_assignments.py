"""
Faculty assignment validation tests (backend/tests/test_faculty_assignments.py).

Covers the edge-input guards on the assignment endpoints so bad payloads get
a clean 422 at the API edge instead of a database error surfacing as a 500:

  * max_score mirrors the NUMERIC(5,2) column capacity: 999.99 accepted,
    1000 (and above) rejected - on create AND on PATCH.
  * weightage_percent follows the database CHECK (strictly positive): 0 is
    rejected consistently for create and update; positive values still work.
  * PATCH {"is_active": null} follows the existing NOT-NULL-column PATCH
    policy: an explicit null means "leave unchanged", never a 500.

Course rows are created directly via SQLAlchemy (like the authz tests) so
these tests exercise the validation/ownership layers without depending on
the admin endpoints.
"""

from app.db.session import SessionLocal
from app.models import Course, User


def _faculty_profile_id_from_user(user_id: int) -> int:
    """Look up a faculty user's FacultyProfile id from the database."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        assert user is not None and user.faculty_profile is not None
        return user.faculty_profile.id
    finally:
        db.close()


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


def _owned_course(faculty_client) -> int:
    """A course led by the authenticated faculty member."""
    return _create_course_in_db(_faculty_profile_id_from_user(faculty_client.user["id"]))


def _assignment_body(course_id: int, **overrides) -> dict:
    """POST /faculty/assignments body with sensible defaults."""
    body: dict = {
        "course_id": course_id,
        "title": "Lab Exercise 1",
        "due_at": "2026-11-01T23:59:00Z",
        "weightage_percent": 15.0,
        "max_score": 100.0,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# max_score bounds (NUMERIC(5,2) capacity = 999.99)
# ---------------------------------------------------------------------------


def test_create_assignment_accepts_max_score_at_column_capacity(faculty_client, db_cleaner) -> None:
    """999.99 - the largest value NUMERIC(5,2) can store - is accepted."""
    course_id = _owned_course(faculty_client)

    response = faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json=_assignment_body(course_id, max_score=999.99),
        headers=faculty_client.headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["max_score"] == 999.99


def test_create_assignment_rejects_max_score_above_column_capacity(
    faculty_client, db_cleaner
) -> None:
    """1000+ would overflow NUMERIC(5,2) -> 422 at the edge, and no row."""
    course_id = _owned_course(faculty_client)

    for too_big in (1000, 9999.99):
        response = faculty_client.client.post(
            "/api/v1/faculty/assignments",
            json=_assignment_body(course_id, max_score=too_big),
            headers=faculty_client.headers,
        )
        assert response.status_code == 422, response.text

    # Nothing was written.
    listed = faculty_client.client.get(
        "/api/v1/faculty/assignments", params={"course_id": course_id}
    )
    assert listed.status_code == 200, listed.text
    assert listed.json() == []


def test_patch_assignment_rejects_max_score_above_column_capacity(
    faculty_client, db_cleaner
) -> None:
    """PATCH with an over-capacity max_score -> 422; stored value kept."""
    course_id = _owned_course(faculty_client)
    created = faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json=_assignment_body(course_id, max_score=500.0),
        headers=faculty_client.headers,
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["id"]

    response = faculty_client.client.patch(
        f"/api/v1/faculty/assignments/{assignment_id}",
        json={"max_score": 1000},
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text

    follow_up = faculty_client.client.get(f"/api/v1/faculty/assignments/{assignment_id}")
    assert follow_up.status_code == 200, follow_up.text
    assert follow_up.json()["max_score"] == 500.0


# ---------------------------------------------------------------------------
# weightage_percent (database CHECK: > 0, not >= 0)
# ---------------------------------------------------------------------------


def test_create_assignment_rejects_zero_weightage(faculty_client, db_cleaner) -> None:
    """weightage_percent=0 -> 422 at the edge, not a CHECK violation 500."""
    course_id = _owned_course(faculty_client)

    response = faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json=_assignment_body(course_id, weightage_percent=0),
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text

    # Nothing was written.
    listed = faculty_client.client.get(
        "/api/v1/faculty/assignments", params={"course_id": course_id}
    )
    assert listed.status_code == 200, listed.text
    assert listed.json() == []


def test_patch_assignment_rejects_zero_weightage(faculty_client, db_cleaner) -> None:
    """PATCH weightage 0 -> 422; the stored weightage stays as it was."""
    course_id = _owned_course(faculty_client)
    created = faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json=_assignment_body(course_id, weightage_percent=15.0),
        headers=faculty_client.headers,
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["id"]

    response = faculty_client.client.patch(
        f"/api/v1/faculty/assignments/{assignment_id}",
        json={"weightage_percent": 0},
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text

    follow_up = faculty_client.client.get(f"/api/v1/faculty/assignments/{assignment_id}")
    assert follow_up.status_code == 200, follow_up.text
    assert follow_up.json()["weightage_percent"] == 15.0


def test_assignment_positive_weightage_accepted_for_create_and_update(
    faculty_client, db_cleaner
) -> None:
    """Valid positive weightages still work on both endpoints."""
    course_id = _owned_course(faculty_client)

    created = faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json=_assignment_body(course_id, weightage_percent=12.5),
        headers=faculty_client.headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["weightage_percent"] == 12.5
    assignment_id = created.json()["id"]

    updated = faculty_client.client.patch(
        f"/api/v1/faculty/assignments/{assignment_id}",
        json={"weightage_percent": 20.0},
        headers=faculty_client.headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["weightage_percent"] == 20.0


# ---------------------------------------------------------------------------
# PATCH policy for the NOT NULL is_active column
# ---------------------------------------------------------------------------


def test_patch_assignment_null_is_active_keeps_the_current_value(
    faculty_client, db_cleaner
) -> None:
    """Explicit is_active: null means "leave unchanged" - never a 500."""
    course_id = _owned_course(faculty_client)
    created = faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json=_assignment_body(course_id),
        headers=faculty_client.headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["is_active"] is True
    assignment_id = created.json()["id"]

    # Explicit null for the NOT NULL column: NOT a 500, value untouched.
    null_response = faculty_client.client.patch(
        f"/api/v1/faculty/assignments/{assignment_id}",
        json={"is_active": None},
        headers=faculty_client.headers,
    )
    assert null_response.status_code == 200, null_response.text
    assert null_response.json()["is_active"] is True

    # A real boolean still updates the flag.
    false_response = faculty_client.client.patch(
        f"/api/v1/faculty/assignments/{assignment_id}",
        json={"is_active": False},
        headers=faculty_client.headers,
    )
    assert false_response.status_code == 200, false_response.text
    assert false_response.json()["is_active"] is False

    # Null after deactivation keeps it deactivated.
    again_null = faculty_client.client.patch(
        f"/api/v1/faculty/assignments/{assignment_id}",
        json={"is_active": None},
        headers=faculty_client.headers,
    )
    assert again_null.status_code == 200, again_null.text
    assert again_null.json()["is_active"] is False
