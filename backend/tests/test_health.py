"""
Health endpoint tests (backend/tests/test_health.py)

These verify the two foundation guarantees of Stage 1:
  * the API process serves requests,
  * the API can execute a real query against PostgreSQL (via the test database).
"""


def test_health_returns_ok_without_database(client) -> None:
    """/api/v1/health must answer 200 and identify the environment."""
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "Campus360 API"
    # conftest.py forces the app into the test environment.
    assert body["environment"] == "test"


def test_health_db_reports_reachable(client) -> None:
    """/api/v1/health/db must run SELECT 1 against the TEST database.

    Fails loudly if the test database or its credentials are misconfigured -
    which is exactly what we want: a green suite proves real connectivity.
    """
    response = client.get("/api/v1/health/db")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "reachable"
    assert body["latency_ms"] >= 0
