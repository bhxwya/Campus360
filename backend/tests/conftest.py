"""
Shared pytest configuration (backend/tests/conftest.py)
========================================================

The environment overrides at the top of this file run at IMPORT time, before
any ``app.*`` module is imported. pydantic-settings gives process environment
variables priority over the .env file, which forces the application under test
onto the dedicated ``campus360_test`` database.

This is the safety mechanism that keeps pytest from ever touching the real
development database (campus360_db): even if .env points at development data,
these overrides win for the whole test run.

NOTE ON ASSIGNMENT (not ``setdefault``): ``setdefault`` only fills in a
variable that is ABSENT. If the developer's shell already exports
``CAMPUS360_DB_NAME=campus360_db`` (a common setup, and how this file was
originally written), ``setdefault`` would silently keep the development
database and the whole suite would run against real data. Assigning
unconditionally is what actually delivers the guarantee this file claims.
"""

import os

# Must run before `from app.main import app` - the engine is built at import.
os.environ["CAMPUS360_ENVIRONMENT"] = "test"
os.environ["CAMPUS360_DB_NAME"] = "campus360_test"

import dataclasses  # noqa: E402  (import after env setup on purpose)

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.api import auth as auth_api  # noqa: E402
from app.core.config import PROJECT_ROOT, get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Announcement,
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    Course,
    Enrollment,
    FacultyProfile,
    Mark,
    TimetableEntry,
    User,
    UserRole,
)
from app.services.auth import create_user  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from alembic import command  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_auth_throttles():
    """Failed-login/activation counters are process-wide; isolate every test."""
    auth_api.reset_login_throttle()
    yield
    auth_api.reset_login_throttle()


@pytest.fixture()
def client() -> TestClient:
    """A TestClient bound to the FastAPI app.

    TestClient (backed by httpx) exercises the real request pipeline -
    middleware, dependencies, routes - without starting a network server.
    The `with` form runs startup/shutdown hooks, which matters once the app
    gains them (e.g. connection pool warm-up).
    """
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Stage 4: shared admin fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def migrated_test_db() -> None:
    """Bring the TEST database to head via Alembic (idempotent).

    The assertion below is a HARD interlock: if the settings ever point at
    the development database, the whole session fails instead of migrating
    real data.
    """
    settings = get_settings()
    assert settings.db_name == settings.test_db_name, (
        f"refusing to migrate db {settings.db_name!r} - expected {settings.test_db_name!r}"
    )
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "head")


@pytest.fixture()
def make_user(migrated_test_db: None):
    """Factory creating committed users; every one is deleted on teardown.

    Deleting a user cascades (ON DELETE CASCADE) to its ``user_sessions``
    rows, so no session data lingers between tests.
    """
    created_ids: list[int] = []

    def _make(email: str, password: str, role: UserRole, is_active: bool = True) -> User:
        db = SessionLocal()
        try:
            user = create_user(db, email=email, password=password, role=role, is_active=is_active)
            # A FACULTY user needs a FacultyProfile so they can be set as
            # ``lead_faculty_id`` on courses (course ownership authority).
            if role == UserRole.FACULTY:
                db.add(
                    FacultyProfile(
                        user_id=user.id,
                        faculty_code=f"FAC-{user.id:06d}",
                        full_name="Test Faculty",
                    )
                )
        finally:
            db.commit()
            db.close()
        created_ids.append(user.id)
        return user

    yield _make

    if created_ids:
        db = SessionLocal()
        try:
            db.query(User).filter(User.id.in_(created_ids)).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


@dataclasses.dataclass
class AuthedAdmin:
    """An authenticated session plus the CSRF header it needs.

    ``headers`` carries the ``X-CSRF-Token`` value the synchronizer-token
    guard expects on every unsafe request; ``csrf_token`` exposes the raw
    value so a test can deliberately send a WRONG one.
    """

    client: TestClient
    csrf_token: str
    user: dict[str, object]

    @property
    def headers(self) -> dict[str, str]:
        """Headers for an unsafe (state-changing) request."""
        return {"X-CSRF-Token": self.csrf_token}


def _login(client: TestClient, email: str, password: str) -> AuthedAdmin:
    """Log in and capture the CSRF token from the response cookie."""
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    csrf_cookie = response.cookies.get(get_settings().csrf_cookie_name)
    assert csrf_cookie, "login did not set a CSRF cookie"
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    return AuthedAdmin(client=client, csrf_token=csrf_cookie, user=me.json())


#: Every authed fixture below builds ITS OWN TestClient (instead of sharing
#: the module-level `client` fixture). One TestClient == one cookie jar; if
#: two role fixtures shared a client, the LAST login would overwrite the
#: session cookie and every later request would run as the wrong user. With
#: separate clients, a single test can legitimately act as admin AND faculty
#: AND student at the same time.


@pytest.fixture()
def admin_client(make_user) -> AuthedAdmin:
    """A TestClient logged in as an ADMIN, with the CSRF header ready."""
    make_user("admin@campus360.example.com", "AdminPass123", UserRole.ADMIN)
    with TestClient(app) as test_client:
        yield _login(test_client, "admin@campus360.example.com", "AdminPass123")


@pytest.fixture()
def student_client(make_user) -> AuthedAdmin:
    """A TestClient logged in as a STUDENT (a NON-admin, for 403 tests)."""
    make_user("student@campus360.example.com", "StudentPass123", UserRole.STUDENT)
    with TestClient(app) as test_client:
        yield _login(test_client, "student@campus360.example.com", "StudentPass123")


@pytest.fixture()
def faculty_client(make_user) -> AuthedAdmin:
    """A TestClient logged in as a FACULTY member (also a non-admin)."""
    make_user("faculty@campus360.example.com", "FacultyPass123", UserRole.FACULTY)
    with TestClient(app) as test_client:
        yield _login(test_client, "faculty@campus360.example.com", "FacultyPass123")


@pytest.fixture()
def faculty_client2(make_user) -> AuthedAdmin:
    """A second FACULTY client, for cross-faculty 403 tests."""
    make_user("faculty2@campus360.example.com", "FacultyPass456", UserRole.FACULTY)
    with TestClient(app) as test_client:
        yield _login(test_client, "faculty2@campus360.example.com", "FacultyPass456")


#: Tables that Stage 4/5 tests create rows in, in DELETION order (children
#: first) so the RESTRICT foreign keys never block the cleanup.
_CLEANUP_ORDER = (
    ("assignment_submissions", AssignmentSubmission),
    ("assignments", Assignment),
    ("marks", Mark),
    ("attendance_records", AttendanceRecord),
    ("enrollments", Enrollment),
    ("timetable_entries", TimetableEntry),
    ("announcements", Announcement),
    ("courses", Course),
)


@pytest.fixture()
def db_cleaner(migrated_test_db: None):
    """Delete every row created during the test, in FK-safe order.

    ``make_user`` cleans up the USERS it created, but the admin API also
    creates students, courses, enrollments and notices. This fixture
    snapshots the highest id of each table before the test and removes
    everything above it afterwards, so tests never leak rows into each
    other (and the aggregate report tests stay deterministic).
    """
    db = SessionLocal()
    try:
        snapshot: dict[str, int] = {}
        for name, model in _CLEANUP_ORDER:
            snapshot[name] = db.scalar(select(func.max(model.id))) or 0
        snapshot["users"] = db.scalar(select(func.max(User.id))) or 0
    finally:
        db.close()

    yield snapshot

    db = SessionLocal()
    try:
        for name, model in _CLEANUP_ORDER:
            db.query(model).filter(model.id > snapshot[name]).delete(synchronize_session=False)
        # Users last: their profiles and sessions cascade away with them.
        db.query(User).filter(User.id > snapshot["users"]).delete(synchronize_session=False)
        db.commit()
    except Exception:  # pragma: no cover - teardown must not mask failures
        db.rollback()
        raise
    finally:
        db.close()
