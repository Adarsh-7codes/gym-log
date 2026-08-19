"""Task 1 — the break-glass CLI script.

This is the only route back in when the *trainer* loses their password, so it
has to work against both the local SQLite file and the live Postgres database,
and it must never print the password it sets.
"""
import subprocess
import sys
from pathlib import Path

from conftest import add_member, login, logout, register_trainer

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "set_password.py"


def run_cli(db_url, *args):
    import os

    env = {**os.environ, "DATABASE_URL": db_url, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, env=env, cwd=str(ROOT))


def db_url_of(client):
    """The throwaway database the client fixture configured."""
    from app.config import settings

    return settings.database_url


def test_list_shows_accounts_without_password_data(client):
    register_trainer(client)
    add_member(client)

    r = run_cli(db_url_of(client), "--list")
    assert r.returncode == 0, r.stderr
    assert "trainer@example.com" in r.stdout
    assert "mo@example.com" in r.stdout
    # No hash material should ever be printed.
    assert "$2b$" not in r.stdout
    assert "password_hash" not in r.stdout


def test_cli_changes_the_password_and_old_one_stops_working(client):
    register_trainer(client)
    url = db_url_of(client)

    r = run_cli(url, "--email", "trainer@example.com", "--password", "recovered123")
    assert r.returncode == 0, r.stderr
    assert "Ada Trainer" in r.stdout and "trainer" in r.stdout

    logout(client)
    assert login(client, "trainer@example.com", "pw123456",
                 role="trainer").headers["location"].startswith("/login")
    assert login(client, "trainer@example.com", "recovered123",
                 role="trainer").headers["location"] == "/dashboard"


def test_cli_never_prints_the_password(client):
    register_trainer(client)
    secret = "verysecretvalue1"
    r = run_cli(db_url_of(client), "--email", "trainer@example.com", "--password", secret)
    assert secret not in r.stdout
    assert secret not in r.stderr


def test_cli_refuses_missing_arguments(client):
    register_trainer(client)
    r = run_cli(db_url_of(client), "--email", "trainer@example.com")
    assert r.returncode != 0
    assert "required" in (r.stderr + r.stdout).lower()


def test_cli_rejects_a_too_short_password(client):
    register_trainer(client)
    r = run_cli(db_url_of(client), "--email", "trainer@example.com", "--password", "abc")
    assert r.returncode == 1
    assert "at least 8" in r.stdout
    logout(client)
    # Unchanged.
    assert login(client, "trainer@example.com", "pw123456",
                 role="trainer").headers["location"] == "/dashboard"


def test_cli_reports_unknown_email(client):
    register_trainer(client)
    r = run_cli(db_url_of(client), "--email", "nobody@example.com", "--password", "whatever1")
    assert r.returncode == 1
    assert "No account found" in r.stdout


def test_cli_masks_remote_credentials_in_its_output(client):
    """A Postgres URL must never have its password echoed into the terminal."""
    register_trainer(client)
    r = run_cli("postgresql://gymlog:sup3rs3cret@db.example.com:5432/gymlog", "--list")
    combined = r.stdout + r.stderr
    assert "sup3rs3cret" not in combined
    assert "REMOTE Postgres" in combined


def test_cli_change_is_audited_as_cli(client):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import PasswordChange, PasswordChangeMethod

    register_trainer(client)
    run_cli(db_url_of(client), "--email", "trainer@example.com", "--password", "recovered123")

    with SessionLocal() as s:
        row = s.scalar(select(PasswordChange).order_by(PasswordChange.id.desc()))
        assert row is not None
        assert row.method == PasswordChangeMethod.cli
        # Null changed_by is how "the CLI did it" is recorded.
        assert row.changed_by_user_id is None


def test_cli_works_on_a_database_that_predates_the_latest_schema(client, tmp_path):
    """The break-glass script must not be the thing that fails in an emergency.

    Regression: it queried the User model directly without running migrations,
    so on a database created before token_version existed it crashed with
    "no such column: users.token_version" -- exactly when you are locked out
    and need it most.
    """
    import sqlite3

    old_db = tmp_path / "legacy.db"
    con = sqlite3.connect(old_db)
    con.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, name TEXT, email TEXT, password_hash TEXT,
            role TEXT, created_at TEXT
        );
        INSERT INTO users VALUES (1,'Old Trainer','old@example.com','x','trainer',NULL);
        """
    )
    con.commit()
    con.close()

    url = f"sqlite:///{old_db.as_posix()}"
    listing = run_cli(url, "--list")
    assert listing.returncode == 0, listing.stderr
    assert "old@example.com" in listing.stdout

    changed = run_cli(url, "--email", "old@example.com", "--password", "recovered123")
    assert changed.returncode == 0, changed.stderr
    assert "Old Trainer" in changed.stdout
