"""Break-glass password reset for GymLog.

The last resort when nobody can log in -- most importantly when the *trainer*
has lost their password, since there is no self-service recovery for that
account by design.

Everyday member lockouts should NOT come here: the trainer resets those from
Members -> Reset password. This script exists so that a total lockout is a
five-minute job instead of a database surgery session.

Usage
-----
List accounts (no password data is printed):
    python scripts/set_password.py --list

Change a password:
    python scripts/set_password.py --email trainer@example.com --password "new-pass-here"

Against the LIVE Render database, use Render's shell for the gymlog service so
DATABASE_URL is already set (nothing to paste, nothing to leak):
    Render dashboard -> gymlog -> Shell
    python scripts/set_password.py --list

Or from your own machine with the External Database URL exported first:
    PowerShell:  $env:DATABASE_URL="postgresql://..."; python scripts/set_password.py --list
    Git Bash:    DATABASE_URL="postgresql://..." python scripts/set_password.py --list

With no DATABASE_URL set it operates on the local SQLite file, exactly like the app.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import crud  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine, ensure_schema  # noqa: E402
from app.models import PasswordChangeMethod, User  # noqa: E402


def prepare_schema() -> None:
    """Bring the database up to date before touching it.

    The web app does this on startup, but this script may well be the first
    thing to open a database after an upgrade -- and it is the tool you reach
    for when you are already locked out. It must not be the thing that fails.
    """
    Base.metadata.create_all(bind=engine)
    ensure_schema()


def describe_target() -> str:
    """Say which database we are pointed at, without ever printing credentials."""
    url = settings.database_url
    if url.startswith("sqlite"):
        return f"LOCAL SQLite ({url})"
    tail = url.rsplit("@", 1)[-1] if "@" in url else url
    return f"REMOTE Postgres (...@{tail})"


def list_accounts(db) -> int:
    users = db.scalars(select(User).order_by(User.id)).all()
    if not users:
        print("No accounts exist. The next registration will become the trainer.")
        return 0
    print(f"{'id':>3}  {'name':<24} {'email':<32} role")
    print("-" * 72)
    for u in users:
        print(f"{u.id:>3}  {u.name:<24} {u.email:<32} {u.role.value}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Break-glass password reset. Never prints or logs the password."
    )
    parser.add_argument("--email", help="account to change")
    parser.add_argument("--password", help="the new password")
    parser.add_argument("--list", action="store_true", help="list accounts and exit")
    args = parser.parse_args()

    print(f"Database: {describe_target()}")
    prepare_schema()

    with SessionLocal() as db:
        if args.list:
            return list_accounts(db)

        # Refuse rather than prompt: an interactive prompt risks echoing the
        # password into a shared terminal or shell history.
        if not args.email or not args.password:
            parser.error("both --email and --password are required (or use --list)")

        user = db.scalar(select(User).where(User.email == args.email.strip().lower()))
        if user is None:
            print(f"No account found with email {args.email!r}. Use --list to see accounts.")
            return 1

        try:
            # Uses the app's own hashing + token_version bump + audit row, so
            # this can never drift from what the web app does.
            crud.set_password(db, user, args.password, method=PasswordChangeMethod.cli)
        except ValueError as exc:
            print(f"Rejected: {exc}")
            return 1

        print(f"Password updated for {user.name} <{user.email}> (role: {user.role.value}).")
        print("All existing sessions for this account have been signed out.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
