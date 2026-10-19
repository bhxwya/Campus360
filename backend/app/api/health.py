"""
Health check endpoints (api/health.py)

Mounted under the API version prefix, so the two endpoints are:

* ``GET /api/v1/health``        - process liveness only; never touches the DB.
* ``GET /api/v1/health/db``     - executes a real query, proving the whole
  chain (FastAPI -> SQLAlchemy -> Psycopg -> PostgreSQL) actually works.

During setup and in production monitoring these two together distinguish
"the API is down" from "the database is unreachable".
"""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])

#: Annotated[...] is the modern FastAPI dependency style: the dependency is
#: declared in the TYPE ANNOTATION instead of as a function-call default
#: (db: Session = Depends(get_db)), which keeps linters happy (ruff B008) and
#: makes the parameter's default visible where it belongs - in the signature
#: type. Every route in this project should follow this pattern.
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/health", summary="Liveness check")
def health() -> dict[str, str]:
    """Return 200 as long as the process serves requests. No database access."""
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
    }


@router.get("/health/db", summary="Database connectivity check")
def database_health(db: DbSession) -> dict[str, str | float]:
    """Run ``SELECT 1`` against PostgreSQL and report round-trip latency.

    Error handling decision: any exception becomes a generic 503. The detailed
    error (which can include host/user information) is logged server-side only
    and ``raise ... from None`` suppresses the original exception chain, so no
    internal diagnostics can leak into an API response.
    """
    started = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Database health check failed")
        raise HTTPException(status_code=503, detail="Database connection failed") from None

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return {"status": "ok", "database": "reachable", "latency_ms": latency_ms}
