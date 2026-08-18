"""Wipe demo data from a GymLog database.

Run from your own machine. There is deliberately no web endpoint for this on
the deployed app -- it holds membership and payment records, so a public
reset URL is not worth the risk (see Phase 0).

Clears: accounts, logs, attendance, memberships, routines, splits, targets,
body weights, planner entries.
Keeps:  the exercise library (it re-seeds itself anyway).

Afterwards the database is empty, so the FIRST account registered becomes the
trainer again.

Usage
-----
Local SQLite (default):
    python scripts/reset_demo.py

Live Render Postgres -- take the *External* Database URL from the Render
dashboard (gymlog-db -> Connect). Never commit it or paste it anywhere public:

    Windows PowerShell:
        $env:DATABASE_URL="postgresql://...";  python scripts/reset_demo.py
    Git Bash / macOS / Linux:
        DATABASE_URL="postgresql://..." python scripts/reset_demo.py

Add --yes to skip the confirmation prompt (for scripted use).
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, inspect, select, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.models import Exercise, User  # noqa: E402

# Children before parents so this works with or without ON DELETE CASCADE.
TABLES_TO_CLEAR = (
    "plan_items",
    "plan_days",
    "split_days",
    "member_routines",
    "targets",
    "body_weights",
    "attendance",
    "memberships",
    "logs",
    "users",
)


def describe_target() -> str:
    url = settings.database_url
    if url.startswith("sqlite"):
        return f"LOCAL SQLite  ({url})"
    # Never print credentials: show only the host/database portion.
    tail = url.rsplit("@", 1)[-1] if "@" in url else url
    return f"REMOTE Postgres  (…@{tail})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset GymLog demo data.")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    target = describe_target()
    with SessionLocal() as db:
        users = db.scalar(select(func.count(User.id))) or 0
        exercises = db.scalar(select(func.count(Exercise.id))) or 0

    print(f"Target : {target}")
    print(f"Before : {users} account(s), {exercises} exercise(s)")
    print("This deletes every account, log, attendance mark, membership,")
    print("routine, split, target and weigh-in. The exercise library is kept.")

    if not args.yes:
        if input('Type RESET to confirm: ').strip() != "RESET":
            print("Aborted. Nothing was changed.")
            return 1

    existing = set(inspect(engine).get_table_names())
    cleared = []
    with engine.begin() as conn:
        for table in TABLES_TO_CLEAR:
            if table in existing:
                conn.execute(text(f"DELETE FROM {table}"))
                cleared.append(table)

    with SessionLocal() as db:
        users_after = db.scalar(select(func.count(User.id))) or 0
        exercises_after = db.scalar(select(func.count(Exercise.id))) or 0

    print(f"Cleared: {', '.join(cleared)}")
    print(f"After  : {users_after} account(s), {exercises_after} exercise(s)")
    print("Done. The next account registered becomes the trainer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
