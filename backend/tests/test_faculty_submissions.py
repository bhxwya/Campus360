"""
Faculty submission & grading tests (backend/tests/test_faculty_submissions.py).

Covers:

  * Listing submissions for an owned assignment.
  * Grading: score validated against the ASSIGNMENT's max_score (scales
    above 100 work), feedback, and the grading-time max_score snapshot +
    graded_by (faculty profile id) + graded_at audit fields.
  * Score bounds: negatives rejected by the schema, scores above the
    assignment maximum rejected by the service - 422 either way.
  * Status rules: only 'graded'/'returned' may carry a grade; snapshots
    never change when the assignment's maximum is edited later.
  * Cross-faculty 403 on submissions of another faculty's assignment.
  * Student cannot grade submissions.
"""

from datetime import UTC, datetime

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


def _setup_course_and_student(admin_client, faculty_client) -> tuple[dict, dict, dict]:
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


def _create_assignment(faculty_client, course_id: int, max_score: float | None = None) -> dict:
    """Create an assignment and return the parsed body.

    ``max_score=None`` omits the field so the schema default (100) applies;
    pass a number to define a custom grading scale.
    """
    body: dict = {
        "course_id": course_id,
        "title": "Lab Exercise 1",
        "due_at": "2026-11-01T23:59:00Z",
        "weightage_percent": 15.0,
    }
    if max_score is not None:
        body["max_score"] = max_score
    response = faculty_client.client.post(
        "/api/v1/faculty/assignments",
        json=body,
        headers=faculty_client.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_submission(assignment_id: int, student_id: int) -> int:
    """Insert a SUBMITTED submission directly and return its id.

    Stage 5 has no student submission endpoint, so tests bypass the API for
    the SETUP step only; everything under test goes through the API.
    """
    from app.models.assignment import AssignmentSubmission
    from app.models.enums import SubmissionStatus

    db = SessionLocal()
    try:
        submission = AssignmentSubmission(
            assignment_id=assignment_id,
            student_id=student_id,
            submitted_at=datetime.now(UTC),
            content="My answer",
            status=SubmissionStatus.SUBMITTED,
        )
        db.add(submission)
        db.commit()
        db.refresh(submission)
        return submission.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Submission listing
# ---------------------------------------------------------------------------


def test_list_submissions_empty_for_new_assignment(
    faculty_client, admin_client, db_cleaner
) -> None:
    """A new assignment has no submissions yet -> empty list."""
    course, _ = _setup_course_and_student(admin_client, faculty_client)
    assignment = _create_assignment(faculty_client, course["id"])

    response = faculty_client.client.get(
        f"/api/v1/faculty/assignments/{assignment['id']}/submissions"
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_list_submissions_returns_student_details(faculty_client, admin_client, db_cleaner) -> None:
    """Submission response includes student code/name and null grading fields."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    assignment = _create_assignment(faculty_client, course["id"])

    from app.db.session import SessionLocal
    from app.models.assignment import AssignmentSubmission
    from app.models.enums import SubmissionStatus

    db = SessionLocal()
    try:
        submission = AssignmentSubmission(
            assignment_id=assignment["id"],
            student_id=student["id"],
            submitted_at=datetime.now(UTC),
            content="My answer here",
            status=SubmissionStatus.SUBMITTED,
        )
        db.add(submission)
        db.commit()
        db.refresh(submission)
    finally:
        db.close()

    response = faculty_client.client.get(
        f"/api/v1/faculty/assignments/{assignment['id']}/submissions"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["student_code"] == student["student_code"]
    assert body[0]["student_name"] == student["full_name"]
    assert body[0]["status"] == "submitted"
    assert body[0]["score"] is None  # not graded yet
    assert body[0]["max_score"] is None  # snapshot exists only once graded
    assert body[0]["feedback"] is None
    assert body[0]["graded_by"] is None
    assert body[0]["graded_at"] is None


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def test_grade_submission_sets_score_and_status(faculty_client, admin_client, db_cleaner) -> None:
    """Grading sets score + snapshot + grader + timestamp, flips to GRADED."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    assignment = _create_assignment(faculty_client, course["id"])

    from app.db.session import SessionLocal
    from app.models.assignment import AssignmentSubmission
    from app.models.enums import SubmissionStatus

    db = SessionLocal()
    try:
        submission = AssignmentSubmission(
            assignment_id=assignment["id"],
            student_id=student["id"],
            submitted_at=datetime.now(UTC),
            content="My answer",
            status=SubmissionStatus.SUBMITTED,
        )
        db.add(submission)
        db.commit()
        db.refresh(submission)
        submission_id = submission.id
    finally:
        db.close()

    response = faculty_client.client.post(
        f"/api/v1/faculty/submissions/{submission_id}/grade",
        json={"score": 87.5, "feedback": "Solid work - see inline notes.", "status": "graded"},
        headers=faculty_client.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["score"] == 87.5
    assert body["status"] == "graded"
    assert body["feedback"] == "Solid work - see inline notes."
    # The default assignment maximum (100) is snapshotted onto the grade.
    assert body["max_score"] == 100
    assert body["graded_at"] is not None
    # graded_by stores the FacultyProfile id, NOT the User id.
    assert body["graded_by"] == _faculty_profile_id(faculty_client.user["id"])


def test_grade_submission_score_out_of_range_returns_422(
    faculty_client, admin_client, db_cleaner
) -> None:
    """A negative score fails the schema; a score above the assignment's
    maximum (default 100) fails service validation - 422 either way."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    assignment = _create_assignment(faculty_client, course["id"])

    from app.db.session import SessionLocal
    from app.models.assignment import AssignmentSubmission
    from app.models.enums import SubmissionStatus

    db = SessionLocal()
    try:
        submission = AssignmentSubmission(
            assignment_id=assignment["id"],
            student_id=student["id"],
            submitted_at=datetime.now(UTC),
            content="My answer",
            status=SubmissionStatus.SUBMITTED,
        )
        db.add(submission)
        db.commit()
        db.refresh(submission)
        submission_id = submission.id
    finally:
        db.close()

    # Score too high
    response = faculty_client.client.post(
        f"/api/v1/faculty/submissions/{submission_id}/grade",
        json={"score": 101.0, "status": "graded"},
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text

    # Score too low
    response = faculty_client.client.post(
        f"/api/v1/faculty/submissions/{submission_id}/grade",
        json={"score": -1.0, "status": "graded"},
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text


def test_grade_rejects_score_above_assignment_maximum(
    faculty_client, admin_client, db_cleaner
) -> None:
    """The ceiling is the ASSIGNMENT's max_score: 64 means 65 fails, 60 passes."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    assignment = _create_assignment(faculty_client, course["id"], max_score=64)
    submission_id = _make_submission(assignment["id"], student["id"])

    response = faculty_client.client.post(
        f"/api/v1/faculty/submissions/{submission_id}/grade",
        json={"score": 65, "status": "graded"},
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text

    response = faculty_client.client.post(
        f"/api/v1/faculty/submissions/{submission_id}/grade",
        json={"score": 60, "status": "graded"},
        headers=faculty_client.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["score"] == 60
    assert body["max_score"] == 64  # snapshot of the assignment maximum


def test_grade_allows_scores_above_100_when_assignment_permits(
    faculty_client, admin_client, db_cleaner
) -> None:
    """max_score=150: 120 grades fine (no universal-100 ceiling); 151 -> 422."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    assignment = _create_assignment(faculty_client, course["id"], max_score=150)
    submission_id = _make_submission(assignment["id"], student["id"])

    response = faculty_client.client.post(
        f"/api/v1/faculty/submissions/{submission_id}/grade",
        json={"score": 120, "status": "graded"},
        headers=faculty_client.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["score"] == 120
    assert body["max_score"] == 150

    response = faculty_client.client.post(
        f"/api/v1/faculty/submissions/{submission_id}/grade",
        json={"score": 151, "status": "graded"},
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text


def test_grade_submission_rejects_submitted_status(
    faculty_client, admin_client, db_cleaner
) -> None:
    """'submitted' means no grade exists - scoring it is contradictory (422)."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    assignment = _create_assignment(faculty_client, course["id"])
    submission_id = _make_submission(assignment["id"], student["id"])

    response = faculty_client.client.post(
        f"/api/v1/faculty/submissions/{submission_id}/grade",
        json={"score": 50, "status": "submitted"},
        headers=faculty_client.headers,
    )
    assert response.status_code == 422, response.text


def test_grade_submission_returned_requires_full_grading_state(
    faculty_client, admin_client, db_cleaner
) -> None:
    """RETURNED needs score + snapshot + graded_at like GRADED; feedback optional."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    assignment = _create_assignment(faculty_client, course["id"])
    submission_id = _make_submission(assignment["id"], student["id"])

    response = faculty_client.client.post(
        f"/api/v1/faculty/submissions/{submission_id}/grade",
        json={"score": 30, "feedback": "Revise the proof.", "status": "returned"},
        headers=faculty_client.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "returned"
    assert body["score"] == 30
    assert body["max_score"] == 100
    assert body["feedback"] == "Revise the proof."
    assert body["graded_by"] == _faculty_profile_id(faculty_client.user["id"])
    assert body["graded_at"] is not None


def test_assignment_max_score_change_does_not_rewrite_existing_grades(
    faculty_client, admin_client, db_cleaner
) -> None:
    """Snapshots are immutable: editing the assignment's maximum later never
    changes grades already recorded on the old scale."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    assignment = _create_assignment(faculty_client, course["id"])  # default 100
    submission_id = _make_submission(assignment["id"], student["id"])

    response = faculty_client.client.post(
        f"/api/v1/faculty/submissions/{submission_id}/grade",
        json={"score": 95, "status": "graded"},
        headers=faculty_client.headers,
    )
    assert response.status_code == 200, response.text

    patch = faculty_client.client.patch(
        f"/api/v1/faculty/assignments/{assignment['id']}",
        json={"max_score": 50},
        headers=faculty_client.headers,
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["max_score"] == 50

    listing = faculty_client.client.get(
        f"/api/v1/faculty/assignments/{assignment['id']}/submissions"
    )
    assert listing.status_code == 200, listing.text
    row = listing.json()[0]
    assert row["score"] == 95
    assert row["max_score"] == 100  # grading-time snapshot, unchanged


def test_grade_submission_unknown_submission_returns_404(faculty_client, db_cleaner) -> None:
    """Grading a non-existent submission returns 404."""
    response = faculty_client.client.post(
        "/api/v1/faculty/submissions/99999999/grade",
        json={"score": 50, "status": "graded"},
        headers=faculty_client.headers,
    )
    assert response.status_code == 404, response.text


def test_grade_submission_cross_faculty_returns_403(
    faculty_client, faculty_client2, admin_client, db_cleaner
) -> None:
    """Faculty B cannot grade a submission on faculty A's assignment (403)."""
    course, student = _setup_course_and_student(admin_client, faculty_client)
    assignment = _create_assignment(faculty_client, course["id"])

    from app.db.session import SessionLocal
    from app.models.assignment import AssignmentSubmission
    from app.models.enums import SubmissionStatus

    db = SessionLocal()
    try:
        submission = AssignmentSubmission(
            assignment_id=assignment["id"],
            student_id=student["id"],
            submitted_at=datetime.now(UTC),
            content="My answer",
            status=SubmissionStatus.SUBMITTED,
        )
        db.add(submission)
        db.commit()
        db.refresh(submission)
        submission_id = submission.id
    finally:
        db.close()

    # Deliberately absurd score: ownership must be checked BEFORE score
    # validation, so 403 (not 422) proves that ordering.
    response = faculty_client2.client.post(
        f"/api/v1/faculty/submissions/{submission_id}/grade",
        json={"score": 999999, "status": "graded"},
        headers=faculty_client2.headers,
    )
    assert response.status_code == 403, response.text


def test_student_cannot_grade_submissions(student_client, db_cleaner) -> None:
    """A STUDENT gets 403 on the grade endpoint."""
    response = student_client.client.post(
        "/api/v1/faculty/submissions/1/grade",
        json={"score": 50, "status": "graded"},
        headers=student_client.headers,
    )
    assert response.status_code == 403, response.text
