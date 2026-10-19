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
"""

import os

# Must run before `from app.main import app` - the engine is built at import.
os.environ.setdefault("CAMPUS360_ENVIRONMENT", "test")
os.environ.setdefault("CAMPUS360_DB_NAME", "campus360_test")

import pytest  # noqa: E402  (import after env setup on purpose)
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


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
