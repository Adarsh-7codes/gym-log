"""Authentication, roles and registration rules.

Covers the original app plus the Phase 0 hardening: the first-user-becomes-
trainer bootstrap, the role toggle, closed self-registration, and the email
validation that a real bug report exposed.
"""
from conftest import add_member, login, logout, register_trainer


def test_first_account_becomes_the_trainer(client):
    register_trainer(client)
    page = client.get("/dashboard").text
    assert "trainer" in page.lower()


def test_second_account_is_a_member(client, db):
    from sqlalchemy import select

    from app.models import Role, User

    register_trainer(client)
    add_member(client)
    from app.database import SessionLocal
    with SessionLocal() as s:
        roles = {u.email: u.role for u in s.scalars(select(User)).all()}
    assert roles["trainer@example.com"] == Role.trainer
    assert roles["mo@example.com"] == Role.member


def test_login_requires_the_matching_role_tab(client):
    register_trainer(client)
    logout(client)
    # Right tab works.
    assert login(client, "trainer@example.com", "pw123456",
                 role="trainer").headers["location"] == "/dashboard"
    logout(client)
    # Wrong tab does not.
    assert login(client, "trainer@example.com", "pw123456",
                 role="member").headers["location"].startswith("/login")


def test_wrong_password_is_rejected(client):
    register_trainer(client)
    logout(client)
    r = login(client, "trainer@example.com", "not-it", role="trainer")
    assert "Incorrect" in r.headers["location"]


def test_protected_pages_redirect_when_logged_out(client):
    register_trainer(client)
    logout(client)
    for path in ("/dashboard", "/logs/new", "/progress"):
        assert client.get(path, follow_redirects=False).status_code == 303


def test_self_registration_closes_once_a_trainer_exists(client):
    """Phase 0: members are created by the trainer, not by strangers."""
    register_trainer(client)
    logout(client)
    assert client.get("/register", follow_redirects=False).status_code == 403
    r = client.post("/register", data={"name": "Sneak", "email": "sneak@example.com",
                                       "password": "pw123456"}, follow_redirects=False)
    assert r.status_code == 403


def test_invalid_emails_are_rejected(client):
    """The bug you found: type=email lets `name@g` through, the server must not."""
    for bad in ("ada@12", "nope", "jainandadarsh7423@g", "foo@bar"):
        r = client.post("/register", data={"name": "X", "email": bad, "password": "pw123456"},
                        follow_redirects=False)
        assert "valid+email" in r.headers["location"], f"accepted invalid email {bad!r}"


def test_valid_email_is_accepted(client):
    r = client.post("/register", data={"name": "Ada", "email": "adarsh@gmail.com",
                                       "password": "pw123456"}, follow_redirects=False)
    assert r.headers["location"] == "/dashboard"


def test_passwords_are_stored_hashed_never_in_plain_text(client):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import User

    register_trainer(client)
    with SessionLocal() as s:
        u = s.scalar(select(User))
    assert u.password_hash != "pw123456"
    assert u.password_hash.startswith("$2b$"), "expected a bcrypt hash"
