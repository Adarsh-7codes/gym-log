"""Shared test fixtures.

Every test gets a brand-new empty SQLite database in a temp directory, so tests
never see each other's data and never touch your real gym.db. The app is
imported *after* DATABASE_URL is set, because config.py reads it at import time.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient backed by a throwaway database.

    Used as a context manager so FastAPI's lifespan runs -- that is what
    creates the tables and seeds the exercise library. (Forgetting this was
    the very first test failure in this project.)
    """
    db_file = tmp_path / f"test_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")

    # Drop any cached copies so the new DATABASE_URL is picked up.
    for mod in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
        del sys.modules[mod]

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db():
    """A session on the same database the client fixture just configured."""
    from app.database import SessionLocal

    with SessionLocal() as session:
        yield session


# --- helpers ------------------------------------------------------------

TRAINER = {"name": "Ada Trainer", "email": "trainer@example.com", "password": "pw123456"}
MEMBER = {"name": "Mo Member", "email": "mo@example.com", "password": "pw123456"}


def register_trainer(client, **over):
    """The first account on an empty database always becomes the trainer."""
    data = {**TRAINER, **over}
    client.post("/register", data=data)
    return data


def add_member(client, name="Mo Member", email="mo@example.com", password="pw123456"):
    """Trainer-created member account (self-registration is closed by design)."""
    client.post("/members", data={"name": name, "email": email, "password": password})
    return {"name": name, "email": email, "password": password}


def login(client, email, password, role="member"):
    return client.post(
        "/login",
        data={"email": email, "password": password, "role": role},
        follow_redirects=False,
    )


def logout(client):
    client.post("/logout")


def user_id_by_email(email):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import User

    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.email == email))
