"""
Local user-provisioning CLI (backend/scripts/create_user.py).

This is the ONLY way to create accounts in Stage 3. It runs locally, is never
exposed over HTTP, and takes the password via a hidden interactive prompt -
so it never appears in shell history, process listings, or logs.

Why a CLI instead of a registration endpoint?

* There is no public self-registration by design. A student or faculty account
  is created by a trusted operator (an administrator running this script), not
  by an anonymous request.
* The FIRST admin cannot be created through an admin API because no admin
  exists yet to authorize it - the classic "who guards the guardians?" problem.
  Provisioning it out-of-band, from the server itself, breaks that loop.

Usage (from the project root):

    uv run python backend/scripts/create_user.py --email a.x@campus.edu --role admin
    uv run python backend/scripts/create_user.py --email j.doe@campus.edu --role student

The script then prompts for a password twice (hidden), normalizes the email to
satisfy the DB CHECK (email_normalized = lower(email)), hashes the password with
Argon2id, and inserts the row via the SAME service function the API uses - so
there is exactly one user-creation code path to review.
"""

import argparse
import getpass
import sys
from pathlib import Path

# Make the `app` package importable when running this file directly by path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.services.auth import AuthError, create_user  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Campus360 user account.")
    parser.add_argument("--email", required=True, help="Login email address.")
    parser.add_argument(
        "--role",
        required=True,
        choices=[r.value for r in UserRole],
        help="Account role. There is no way to grant a role outside this list.",
    )
    parser.add_argument(
        "--inactive",
        action="store_true",
        help="Create the account already deactivated (default: active).",
    )
    return parser.parse_args()


def prompt_for_password() -> str:
    """Read the password twice, hidden, and require a non-trivial minimum.

    getpass() keeps the password off the screen and out of argv/shell history.
    We do NOT log it, echo it, or store it anywhere but the Argon2id hash.
    """
    first = getpass.getpass("Password: ")
    if len(first) < 8:
        raise SystemExit("Password must be at least 8 characters.")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise SystemExit("Passwords do not match.")
    return first


def main() -> None:
    args = parse_args()
    password = prompt_for_password()

    # One session for the whole script; create_user commits internally.
    db = SessionLocal()
    try:
        user = create_user(
            db,
            email=args.email,
            password=password,
            role=UserRole(args.role),
            is_active=not args.inactive,
        )
    except AuthError as exc:
        # Duplicate email (or a rare token collision) - report cleanly, no traceback.
        raise SystemExit(f"Could not create user: {exc.message}") from None
    finally:
        db.close()

    print(f"Created user id={user.id} email={user.email} role={user.role.value}")


if __name__ == "__main__":
    main()
