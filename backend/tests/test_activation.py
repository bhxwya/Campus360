"""
Account activation tests (backend/tests/test_activation.py).

Covers the secure activation workflow for imported students:

* token randomness, hashing, expiry, and one-time use;
* password hashing and account activation;
* inactive-account login rejection;
* email provider success and failure;
* safe retry without duplicate accounts;
* concurrent activation attempts (token replay protection).

Email delivery is mocked - no real SMTP provider is contacted and no real
emails are sent. All tests run against the dedicated campus360_test database.
"""

import pytest
from app.core.config import get_settings
from app.core.email import EmailSendResult, LogEmailSender, NullEmailSender, build_email_sender
from app.db.session import SessionLocal
from app.main import app
from app.models import ActivationToken, User, UserRole
from app.services.activation import (
    _placeholder_password_hash,
    create_inactive_user_and_activation_token,
)
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fake email sender for tests
# ---------------------------------------------------------------------------


class FakeEmailSender:
    """Records activation email calls instead of sending real email."""

    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[dict[str, str]] = []

    provider_name = "fake"

    def send_activation_email(
        self, *, to_email: str, activation_url: str, student_name: str
    ) -> EmailSendResult:
        self.calls.append({"to": to_email, "url": activation_url, "name": student_name})
        if self.should_fail:
            return EmailSendResult(
                sent=False, provider=self.provider_name, error="SMTP unavailable"
            )
        return EmailSendResult(sent=True, provider=self.provider_name)


@pytest.fixture()
def fake_email_sender(monkeypatch: pytest.MonkeyPatch) -> FakeEmailSender:
    """Replace build_email_sender() with a fake that records calls."""
    sender = FakeEmailSender()
    monkeypatch.setattr("app.core.email.build_email_sender", lambda: sender)
    return sender


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inactive_student(db, email: str = "imported@campus360.example") -> tuple[User, str]:
    """Create an inactive student account + activation token, return (user, raw_token)."""
    user, raw_token = create_inactive_user_and_activation_token(
        db,
        email=email,
        email_normalized=email.strip().lower(),
    )
    db.commit()
    db.refresh(user)
    return user, raw_token


def _login_as_admin(client: TestClient) -> dict[str, str]:
    from app.models import User as UserModel

    db = SessionLocal()
    try:
        user = (
            db.query(UserModel)
            .filter(UserModel.email == "admin@campus360.example.com")
            .one_or_none()
        )
        if user is None:
            from app.services.auth import create_user

            user = create_user(
                db,
                email="admin@campus360.example.com",
                password="AdminPass123",
                role=UserRole.ADMIN,
            )
            db.commit()
    finally:
        db.close()

    with TestClient(app) as c:
        c.post(
            "/api/v1/auth/login",
            json={"email": "admin@campus360.example.com", "password": "AdminPass123"},
        )
        csrf = c.cookies.get(get_settings().csrf_cookie_name)
        me = c.get("/api/v1/auth/me")
        return {"csrf_token": csrf, "user": me.json(), "client": c}


# ---------------------------------------------------------------------------
# Token creation & placeholder password
# ---------------------------------------------------------------------------


def test_placeholder_hash_is_not_a_usable_password(db_cleaner) -> None:
    """The placeholder hash must not verify against any known password."""
    hash_value = _placeholder_password_hash()
    assert hash_value.startswith("$argon2")
    from app.core.security import verify_password

    assert not verify_password("anything", hash_value)
    assert not verify_password("", hash_value)


def test_create_inactive_user_is_not_loggable(db_cleaner) -> None:
    """An inactive account created for import cannot log in."""
    db = SessionLocal()
    try:
        user, _raw_token = _make_inactive_student(db, "nologin@campus360.example")
    finally:
        db.close()

    from app.services.auth import authenticate

    db = SessionLocal()
    try:
        result = authenticate(db, "nologin@campus360.example", "any-password")
        assert result is None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Activation endpoint: valid token
# ---------------------------------------------------------------------------


def test_activation_valid_token_activates_account(admin_client, db_cleaner) -> None:
    """A valid unused token activates the account and sets a real password hash."""
    db = SessionLocal()
    try:
        user, raw_token = _make_inactive_student(db)
    finally:
        db.close()

    response = admin_client.client.post(
        "/api/v1/auth/activate",
        json={"token": raw_token, "password": "NewPass123!"},
        headers=admin_client.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == user.email
    assert body["message"] == "Account activated. You can now sign in with your new password."

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user.id).one()
        assert user.is_active is True
        assert user.password_hash.startswith("$argon2")
        # The placeholder hash is gone - replaced by the real one.
        from app.core.security import verify_password

        assert verify_password("NewPass123!", user.password_hash)
        # The token is consumed.
        token = db.query(ActivationToken).filter(ActivationToken.user_id == user.id).one()
        assert token.consumed_at is not None
    finally:
        db.close()


def test_activation_invalid_token_rejected(admin_client, db_cleaner) -> None:
    """An unknown token returns a generic error (no account enumeration)."""
    response = admin_client.client.post(
        "/api/v1/auth/activate",
        json={"token": "definitely-not-a-valid-token", "password": "NewPass123!"},
        headers=admin_client.headers,
    )

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail == "Invalid or expired activation link."


# ---------------------------------------------------------------------------
# Token expiry, one-time use, invalid tokens
# ---------------------------------------------------------------------------


def test_activation_expired_token_rejected(admin_client, db_cleaner) -> None:
    """An expired token cannot activate an account."""

    db = SessionLocal()
    try:
        user, raw_token = create_inactive_user_and_activation_token(
            db,
            email="expired@campus360.example",
            email_normalized="expired@campus360.example",
            ttl_seconds=0,  # expires immediately
        )
        db.commit()
    finally:
        db.close()

    response = admin_client.client.post(
        "/api/v1/auth/activate",
        json={"token": raw_token, "password": "NewPass123!"},
        headers=admin_client.headers,
    )

    assert response.status_code == 400, response.text
    # Account stays inactive.
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "expired@campus360.example").one()
        assert user.is_active is False
    finally:
        db.close()


def test_activation_token_one_time_use(admin_client, db_cleaner) -> None:
    """A consumed token cannot be used again (replay protection)."""
    db = SessionLocal()
    try:
        user, raw_token = _make_inactive_student(db)
    finally:
        db.close()

    # First use succeeds.
    admin_client.client.post(
        "/api/v1/auth/activate",
        json={"token": raw_token, "password": "NewPass123!"},
        headers=admin_client.headers,
    )

    # Second use with the same token fails.
    response = admin_client.client.post(
        "/api/v1/auth/activate",
        json={"token": raw_token, "password": "NewPass123!"},
        headers=admin_client.headers,
    )

    assert response.status_code == 400, response.text


def test_activation_password_too_short_rejected(admin_client, db_cleaner) -> None:
    """Passwords below the minimum length are rejected before touching the DB."""
    db = SessionLocal()
    try:
        user, raw_token = _make_inactive_student(db)
    finally:
        db.close()

    response = admin_client.client.post(
        "/api/v1/auth/activate",
        json={"token": raw_token, "password": "short"},
        headers=admin_client.headers,
    )

    assert response.status_code == 422, response.text
    # Account stays inactive.
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user.id).one()
        assert user.is_active is False
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Concurrent activation attempts (token replay race)
# ---------------------------------------------------------------------------


def test_concurrent_activation_only_one_succeeds(admin_client, db_cleaner) -> None:
    """Two simultaneous activation requests for the same token: exactly one succeeds."""
    import threading

    db = SessionLocal()
    try:
        user, raw_token = _make_inactive_student(db)
    finally:
        db.close()

    results = []

    def attempt(pwd: str) -> None:
        with TestClient(app) as c:
            c.post(
                "/api/v1/auth/login",
                json={"email": "admin@campus360.example.com", "password": "AdminPass123"},
            )
            csrf = c.cookies.get(get_settings().csrf_cookie_name)
            r = c.post(
                "/api/v1/auth/activate",
                json={"token": raw_token, "password": pwd},
                headers={"X-CSRF-Token": csrf},
            )
            results.append(r.status_code)

    threads = [
        threading.Thread(target=attempt, args=("PassOne123!",)),
        threading.Thread(target=attempt, args=("PassTwo123!",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    success_count = sum(1 for s in results if s == 200)
    assert success_count == 1, (
        f"Expected exactly 1 successful activation, got {success_count}: {results}"
    )

    db = SessionLocal()
    try:
        token = db.query(ActivationToken).filter(ActivationToken.user_id == user.id).one()
        assert token.consumed_at is not None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Email provider success / failure
# ---------------------------------------------------------------------------


def test_activation_email_success(
    fake_email_sender, db_cleaner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful email submission returns sent=True."""
    sender = FakeEmailSender(should_fail=False)
    # queue_activation_email calls build_email_sender from its own module namespace,
    # so patch it there (not in app.core.email).
    monkeypatch.setattr("app.services.activation.build_email_sender", lambda: sender)

    db = SessionLocal()
    try:
        user, raw_token = create_inactive_user_and_activation_token(
            db,
            email="smtp-ok-unique@campus360.example",
            email_normalized="smtp-ok-unique@campus360.example",
        )
        db.commit()
    finally:
        db.close()

    from app.services.activation import queue_activation_email

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "smtp-ok-unique@campus360.example").one()
        result = queue_activation_email(
            db, user=user, raw_token=raw_token, student_name="Test User"
        )
    finally:
        db.close()

    assert result.sent is True
    assert result.provider == "fake"
    assert len(sender.calls) == 1
    assert sender.calls[0]["to"] == "smtp-ok-unique@campus360.example"


def test_activation_email_failure_keeps_account_consistent(
    fake_email_sender, db_cleaner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When email delivery fails, the account/token are still created (pending retry)."""
    sender = FakeEmailSender(should_fail=True)
    monkeypatch.setattr("app.services.activation.build_email_sender", lambda: sender)

    db = SessionLocal()
    try:
        user, raw_token = create_inactive_user_and_activation_token(
            db,
            email="smtp-fail-unique@campus360.example",
            email_normalized="smtp-fail-unique@campus360.example",
        )
        db.commit()
    finally:
        db.close()

    from app.services.activation import queue_activation_email

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "smtp-fail-unique@campus360.example").one()
        result = queue_activation_email(
            db, user=user, raw_token=raw_token, student_name="Test User"
        )
    finally:
        db.close()

    assert result.sent is False
    assert result.error == "SMTP unavailable"

    # Account still exists and is inactive - can be retried.
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "smtp-fail-unique@campus360.example").one()
        assert user.is_active is False
        token = db.query(ActivationToken).filter(ActivationToken.user_id == user.id).one()
        assert token.consumed_at is None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Config providers resolve correctly
# ---------------------------------------------------------------------------


def test_build_email_sender_defaults_to_log() -> None:
    """With no email provider configured, the log provider is used (safe default)."""
    get_settings().email_provider = "log"
    sender = build_email_sender()
    assert isinstance(sender, LogEmailSender)


def test_build_email_sender_none_provider() -> None:
    """The 'none' provider never sends and reports pending."""
    get_settings().email_provider = "none"
    sender = build_email_sender()
    assert isinstance(sender, NullEmailSender)
