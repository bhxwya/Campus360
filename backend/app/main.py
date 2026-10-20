"""
Campus360 FastAPI application (app/main.py)
===========================================

This module assembles the application. It stays intentionally small: each
concern lives in its own module.

  configuration ....... app/core/config.py
  database ............ app/db/session.py (engine + per-request Session)
  routes .............. app/api/<feature>.py, included below under /api/v1
  frontend ............ served as static files by THIS app (see below)

Development workflow decision (documented in the README):
the API and the existing HTML/CSS/JavaScript frontend are served from ONE
origin (http://localhost:8000). The browser then talks to the API same-origin,
so session cookies work without any CORS complication. CORS middleware is
still configured from settings so the frontend CAN be run separately
(e.g. VS Code Live Server on :5500) if preferred.

No Jinja2 here: HTML pages are the project's existing static files, returned
verbatim. The backend only ever returns JSON from /api/v1.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, auth, health
from app.core.config import get_settings

#: Repository root (…/Campus360). This file is backend/app/main.py,
#: so the root is two levels up - robust regardless of working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

settings = get_settings()

#: Static subdirectories of the existing frontend that are safe to serve.
#: Deliberately enumerated instead of mounting the whole repository, so backend
#: source code, .env, and .git can never be downloaded over HTTP.
FRONTEND_DIRS = ("css", "js", "assets", "pages")


def create_app() -> FastAPI:
    """Build (but do not run) the FastAPI application.

    Called once at module import (line `app = create_app()` below). Keeping
    construction in a factory makes it reusable for tests that need a fresh,
    differently-configured app instance.
    """
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "REST API for the Campus360 educational management system: "
            "authentication, courses, attendance, marks, announcements and more. "
            "Interactive documentation is available at /docs."
        ),
        # OpenAPI/ReDoc are kept enabled: FastAPI's auto-generated docs are the
        # project's API documentation (requirement) and are harmless locally.
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # --- CORS ---------------------------------------------------------------
    # Needed ONLY when the frontend is served from another origin (Live Server).
    # allow_credentials=True is required for the HttpOnly session cookie that
    # real authentication (Stage 3) will use; that is also why the allowed
    # origins are an explicit list from configuration - the CORS standard
    # forbids "*" together with credentials.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # --- API routes ---------------------------------------------------------
    # Every router is versioned via the shared /api/v1 prefix. Feature routers
    # are included here as they land (auth first, then academic resources).
    application.include_router(health.router, prefix=settings.api_prefix)
    application.include_router(auth.router, prefix=settings.api_prefix)
    # Stage 4: the admin API. Every route inside depends on `require_admin`,
    # so the role check is enforced by the server, not by the frontend.
    application.include_router(admin.router, prefix=settings.api_prefix)

    # --- Existing frontend (static files, untouched) -------------------------
    # Mounting each subdirectory separately (instead of "/" over the repo)
    # keeps backend/, .env and .git out of reach of HTTP clients.
    for directory in FRONTEND_DIRS:
        application.mount(
            f"/{directory}",
            StaticFiles(directory=PROJECT_ROOT / directory),
            name=directory,
        )

    @application.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        """Serve the existing landing page at the site root."""
        return FileResponse(PROJECT_ROOT / "index.html")

    return application


#: Module-level singleton used by uvicorn (`uvicorn app.main:app`) and by tests.
app = create_app()
