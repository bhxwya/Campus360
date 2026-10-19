"""
Settings unit tests (backend/tests/test_settings.py)

No database required - these protect behaviours that are easy to break
silently: credential encoding in the connection URL, password masking when a
URL becomes text, and rejection of a weak session-signing key.
"""

import pytest
from app.core.config import Settings
from pydantic import ValidationError


def _make_settings(**overrides) -> Settings:
    """Build a Settings object that is fully deterministic.

    Connection fields are passed as constructor arguments, which OUTRANK
    process environment variables and the .env file in pydantic-settings -
    so these assertions hold regardless of how the developer's local
    environment (or pytest's test-database override) is configured.
    """
    defaults = {
        "db_host": "localhost",
        "db_port": 5432,
        "db_name": "campus360_db",
        "db_user": "campus360_user",
        "db_password": "unit-test-db-password",
        "secret_key": "unit-test-signing-key-long-enough-0123456789",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_database_url_uses_psycopg_driver() -> None:
    """The URL must target SQLAlchemy's Psycopg 3 dialect (postgresql+psycopg)."""
    url = _make_settings().database_url

    assert url.drivername == "postgresql+psycopg"
    assert url.host == "localhost"
    assert url.database == "campus360_db"
    assert url.username == "campus360_user"


def test_database_url_encodes_special_characters_in_password() -> None:
    """Passwords often contain @ : / - all of which are meaningful in a URL.

    URL.create must percent-encode them so the connection string stays
    parseable, while the URL object still exposes the original password to
    the driver.
    """
    tricky_password = "p@ss:w/rd!value"
    url = _make_settings(db_password=tricky_password).database_url

    # Rendered with the password visible on purpose: the default str(URL)
    # masks it (asserted separately below), so encoding can only be observed
    # through render_as_string.
    rendered = url.render_as_string(hide_password=False)

    # The raw password must NOT appear unencoded in the rendered URL...
    assert "p@ss" not in rendered
    # ...it must be percent-encoded instead.
    assert "%40" in rendered

    # Round-trip: SQLAlchemy hands the driver exactly the original password.
    assert url.password == tricky_password


def test_string_conversion_masks_password() -> None:
    """str(URL) must hide the password - this is what keeps logs leak-free."""
    url = _make_settings(db_password="super-secret-value").database_url

    assert "super-secret-value" not in str(url)
    assert "***" in str(url)


def test_short_secret_key_is_rejected() -> None:
    """A signing key below 32 characters must fail startup-level validation."""
    with pytest.raises(ValidationError, match="at least 32 characters"):
        _make_settings(secret_key="too-short")


def test_secret_str_is_redacted_in_repr() -> None:
    """Printing a Settings object must never reveal the raw password."""
    settings = _make_settings(db_password="super-secret-value")

    assert "super-secret-value" not in repr(settings)
    assert "super-secret-value" not in str(settings)
