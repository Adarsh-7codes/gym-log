from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def ensure_schema() -> None:
    """Add columns introduced after a DB was first created.

    create_all() only creates missing *tables*, never new columns on an
    existing one -- so a gym.db from an earlier version needs the `feeling`
    column added by hand. Idempotent; safe on every startup, SQLite or Postgres.
    """
    insp = inspect(engine)
    tables = set(insp.get_table_names())

    # Each block guards its own table: one missing table must never silently
    # skip the migrations for the others.
    if "logs" in tables:
        columns = {c["name"] for c in insp.get_columns("logs")}
        if "feeling" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE logs ADD COLUMN feeling VARCHAR(20)"))
        if "logged_by" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE logs ADD COLUMN logged_by VARCHAR(20)"))
                # Rows predating this column were entered before trainer-logging
                # was distinguished -- treat them as member-entered.
                conn.execute(text("UPDATE logs SET logged_by = 'member' WHERE logged_by IS NULL"))

    # Phase 0.5: session invalidation + recovery contacts on the account.
    if "users" in tables:
        user_cols = {c["name"] for c in insp.get_columns("users")}
        if "token_version" not in user_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN token_version INTEGER DEFAULT 0"))
                conn.execute(text("UPDATE users SET token_version = 0 WHERE token_version IS NULL"))
        for col, ddl in (
            ("recovery_email", "ALTER TABLE users ADD COLUMN recovery_email VARCHAR(255)"),
            ("recovery_phone", "ALTER TABLE users ADD COLUMN recovery_phone VARCHAR(40)"),
            # Archive/deactivate: NULL = active, a timestamp = archived. Nullable
            # with no default, so every existing account stays active on upgrade.
            ("archived_at", "ALTER TABLE users ADD COLUMN archived_at TIMESTAMP"),
        ):
            if col not in user_cols:
                with engine.begin() as conn:
                    conn.execute(text(ddl))

    # Phase 2: who prescribed each routine entry. Pre-existing rows were all
    # self-selected by the member, so backfill them to 'member'.
    if "member_routines" in tables:
        routine_cols = {c["name"] for c in insp.get_columns("member_routines")}
        if "assigned_by" not in routine_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE member_routines ADD COLUMN assigned_by VARCHAR(20)"))
                conn.execute(
                    text("UPDATE member_routines SET assigned_by = 'member' WHERE assigned_by IS NULL")
                )

    # Exercise Library columns added after the exercises table first existed.
    if "exercises" in tables:
        ex_cols = {c["name"] for c in insp.get_columns("exercises")}
        for col, ddl in (
            ("body_part", "ALTER TABLE exercises ADD COLUMN body_part VARCHAR(20)"),
            ("difficulty", "ALTER TABLE exercises ADD COLUMN difficulty VARCHAR(20)"),
            ("equipment", "ALTER TABLE exercises ADD COLUMN equipment VARCHAR(120)"),
            ("instructions", "ALTER TABLE exercises ADD COLUMN instructions TEXT"),
            ("demo_url", "ALTER TABLE exercises ADD COLUMN demo_url VARCHAR(300)"),
        ):
            if col not in ex_cols:
                with engine.begin() as conn:
                    conn.execute(text(ddl))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
