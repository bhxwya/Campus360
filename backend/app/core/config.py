"""
Application settings (core/config.py)
=====================================

This module defines the single ``Settings`` object that every other part of
the backend reads configuration from. Nothing else in the codebase should read
environment variables directly - centralising configuration here means:

* secrets live in ``.env`` (git-ignored), never in source code or logs;
* switching between development / test / production is configuration, not code;
* the database URL construction can be unit-tested without a real database.

Environment variables use the ``CAMPUS360_`` prefix (see ``env_prefix`` below),
so ``CAMPUS360_DB_HOST`` maps to ``Settings.db_host``.

Priority order used by pydantic-settings (highest first):
  explicit constructor arguments > process environment > .env file > defaults.
That is why pytest can force the app onto a test database by setting a process
environment variable (see backend/tests/conftest.py).
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL as DatabaseURL

#: Absolute path of the repository root (…/Campus360).
#: This file lives at backend/app/core/config.py, so the root is three levels
#: up. Deriving it from __file__ (instead of the current working directory)
#: keeps the path correct no matter where uvicorn/pytest/alembic is started.
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Typed, validated application configuration.

    Two fields have **no default on purpose**: ``db_password`` and
    ``secret_key``. If they are missing from the environment/.env, the app
    fails at startup with a clear validation error instead of silently running
    with an empty password or a weak (future) signing key. ``secret_key`` is
    currently RESERVED (see its comment) - Stage 3 sessions are opaque and
    server-side, so nothing signs material with it yet - but it remains required
    so a strong secret is always configured and available when needed.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="CAMPUS360_",
        # Tolerate unrelated CAMPUS360_* variables instead of crashing.
        extra="ignore",
    )

    # --- Application ------------------------------------------------------
    app_name: str = "Campus360 API"
    #: development | test | production - controls cookie/logging decisions later.
    environment: str = "development"
    #: All API routes are mounted under this prefix (/api/v1/...).
    api_prefix: str = "/api/v1"

    # --- PostgreSQL (kept as components; see database_url property) -------
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "campus360_db"
    db_user: str = "campus360_user"
    #: SecretStr instead of str: pydantic redacts it in reprs/logs, so an
    #: accidental print of a Settings object cannot leak the password.
    db_password: SecretStr

    # --- Authentication ----------------------------------------------------------
    #: RESERVED for future signed-token work (e.g. if stateless tokens or
    #: password-reset links are ever added). The current Stage 3 sessions are
    #: OPAQUE and looked up server-side, so they need no signing key - this
    #: value is validated and stored but not yet consumed by any code path.
    #: Keeping it required avoids a config/schema churn later and keeps the
    #: "one strong secret per deployment" habit in place. Never hardcode; never
    #: commit.
    secret_key: SecretStr
    #: True in production (HTTPS-only cookies). False for plain-HTTP local dev.
    cookie_secure: bool = False
    #: Session lifetime in seconds (default 8 hours).
    session_max_age_seconds: int = 8 * 60 * 60
    #: Cookie names - kept as settings so they can be renamed without code
    #: changes (e.g. to avoid collisions when several apps share a domain).
    session_cookie_name: str = "campus360_session"
    csrf_cookie_name: str = "campus360_csrf"

    # --- CORS -------------------------------------------------------------
    #: Explicit origins allowed to send credentialed requests (the CORS spec
    #: forbids combining the "*" wildcard with credentials).
    cors_origins: list[str] = ["http://localhost:5500", "http://127.0.0.1:5500"]

    # --- Tests ------------------------------------------------------------
    #: Separate database name so pytest can never touch development data.
    test_db_name: str = "campus360_test"

    @field_validator("secret_key")
    @classmethod
    def _secret_key_must_be_long(cls, value: SecretStr) -> SecretStr:
        """Reject signing keys shorter than 32 characters.

        A short key makes session forgery trivially cheap, so we fail fast at
        startup rather than running with a weak key.
        """
        if len(value.get_secret_value()) < 32:
            raise ValueError(
                "CAMPUS360_SECRET_KEY must be at least 32 characters. Generate one with: "
                'uv run python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return value

    @property
    def database_url(self) -> DatabaseURL:
        """Build the SQLAlchemy connection URL for Psycopg 3.

        Built with ``sqlalchemy.engine.URL.create`` instead of string
        concatenation: it percent-encodes usernames and passwords that contain
        URL-significant characters (@ : / # ? etc.), which a hand-built string
        would not.

        Returns the ``URL`` OBJECT rather than a string on purpose:
        SQLAlchemy masks the password (``***``) whenever a URL is converted to
        text (str()/repr()/f-string), so a URL that ends up in a log or error
        message can never leak the real password. When the raw string is
        genuinely needed (e.g. Alembic's offline mode), call
        ``url.render_as_string(hide_password=False)`` explicitly.
        ``create_engine()`` accepts the URL object directly, which is how
        db/session.py consumes it.
        """
        return DatabaseURL.create(
            drivername="postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide ``Settings`` instance (read-once, shared).

    ``.env`` is parsed on the first call and cached, so every dependency,
    router, and service sees the same configuration object. Tests that need
    fresh values construct ``Settings(...)`` directly, which bypasses the cache.
    """
    return Settings()
