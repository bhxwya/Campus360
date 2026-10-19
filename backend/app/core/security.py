"""
Password hashing and token helpers (core/security.py).

Two responsibilities, both pure functions (no database, no request context):

1. Password hashing with Argon2id - the winner of the Password Hashing
   Competition and a current OWASP recommendation. Argon2 is deliberately
   SLOW and SALTED: identical passwords produce different hashes, and
   guessing offline is expensive. ``verify_password`` returns a bool and
   never raises, so callers cannot accidentally leak "this hash was malformed"
   as a distinguishable error.

2. Opaque session/CSRF token generation and hashing. Tokens are 256-bit
   random strings (``secrets`` - cryptographically secure). We store only
   their SHA-256 hex digest in the database:

   * The token is HIGH-ENTROPY random, so unlike a password it does not need
     a slow KDF - a fast hash is correct here (and cheap on every request).
   * Storing the hash means a leaked ``user_sessions`` table cannot be replayed
     as live sessions, exactly like storing password hashes instead of
     passwords.
"""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: Argon2id hasher with library defaults (OWASP-approved parameters).
_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Return a salted Argon2id hash of ``plain``. Never store the plain text."""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """True if ``plain`` matches ``hashed``; False on mismatch OR bad hash.

    All verification failures collapse to False so callers can return one
    generic "invalid credentials" error whether the hash was wrong, corrupt,
    or absent.
    """
    try:
        return _hasher.verify(hashed, plain)
    except VerifyMismatchError, VerificationError, InvalidHashError:
        return False


def generate_session_token() -> str:
    """A 256-bit URL-safe random session token (sent to the browser once)."""
    return secrets.token_urlsafe(32)


def generate_csrf_token() -> str:
    """A 256-bit URL-safe random CSRF token (synchronizer-token design)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a session/CSRF token - what we persist.

    Only the digest is stored; the raw token lives solely in the browser
    cookie. 64 hex characters, well within the String(64) columns.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


#: A real Argon2 hash of a throwaway string. Used to equalize login timing
#: when an email does not exist: without it, a missing user would skip the
#: (slow) verify step and respond measurably faster, letting an attacker
#: enumerate which emails are registered purely from response times.
_DUMMY_HASH = hash_password("campus360-timing-equalizer")


def dummy_verify() -> None:
    """Burn the same CPU time as a real verify, then discard the result.

    Called on the "no such user" branch of login so every failed attempt
    costs roughly the same wall-clock time regardless of whether the account
    exists.
    """
    verify_password("campus360-timing-equalizer", _DUMMY_HASH)
