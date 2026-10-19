"""
Password & token unit tests (backend/tests/test_security.py).

Pure-function tests with NO database and NO HTTP - they prove the cryptographic
primitives behave before anything else relies on them:

* a hash is never the plaintext and never contains it,
* verification round-trips and rejects the wrong password,
* the same password hashes differently every time (random salt),
* token hashing is deterministic SHA-256 (so the DB lookup matches),
* generated tokens are unique and high-entropy.
"""

from app.core.security import (
    generate_csrf_token,
    generate_session_token,
    hash_password,
    hash_token,
    verify_password,
)


def test_hash_password_is_not_plaintext() -> None:
    """The stored hash must not be the password, nor contain it (Argon2 salt+pepper)."""
    plain = "correct-horse-battery"
    digest = hash_password(plain)

    assert digest != plain
    assert plain not in digest
    # Argon2id hashes are self-describing and start with the algorithm tag.
    assert digest.startswith("$argon2")


def test_verify_password_roundtrip() -> None:
    """verify_password accepts the real password and rejects a wrong one."""
    digest = hash_password("s3cret-password!")

    assert verify_password("s3cret-password!", digest) is True
    assert verify_password("wrong-password", digest) is False


def test_verify_password_rejects_malformed_hash() -> None:
    """A corrupt/empty hash returns False (never raises) so callers can't leak it."""
    assert verify_password("anything", "") is False
    assert verify_password("anything", "not-a-real-hash") is False


def test_same_password_hashes_differently_each_time() -> None:
    """Random salt: two hashes of the SAME password must differ, so a leaked
    hash cannot reveal that two accounts share a password."""
    a = hash_password("same-password")
    b = hash_password("same-password")

    assert a != b
    # ...yet both still verify against the original password.
    assert verify_password("same-password", a)
    assert verify_password("same-password", b)


def test_hash_token_is_sha256_hex() -> None:
    """Token hashing is deterministic SHA-256: same token -> same 64-hex digest.

    Deterministic (unlike passwords) because we must LOOK UP sessions by the
    cookie's hash on every request, and the tokens are already high-entropy.
    """
    token = "a-token-value"
    digest = hash_token(token)

    assert digest == hash_token(token)  # deterministic
    assert len(digest) == 64  # SHA-256 hex digest
    assert all(ch in "0123456789abcdef" for ch in digest)
    assert digest != token


def test_generated_tokens_are_unique_and_long() -> None:
    """Session/CSRF tokens are 256-bit random values - unique and URL-safe."""
    sessions = {generate_session_token() for _ in range(50)}
    csrf = {generate_csrf_token() for _ in range(50)}

    # 50 draws colliding would mean a broken RNG; token_urlsafe(32) is ~43 chars.
    assert len(sessions) == 50
    assert len(csrf) == 50
    assert all(len(t) >= 40 for t in sessions | csrf)
