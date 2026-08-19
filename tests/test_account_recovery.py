"""Phase 0.5 — account recovery.

The point of this phase: a lockout must never require the developer. These
tests pin down the three recovery paths, session invalidation, the audit
trail, and the authorisation boundaries around all of it.
"""
from sqlalchemy import func, select

from conftest import add_member, login, logout, register_trainer, user_id_by_email


# --- Task 2: trainer resets a member's password -------------------------


def test_trainer_can_reset_a_member_password(client):
    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])

    r = client.post(f"/members/{mid}/password",
                    data={"new_password": "brandnew123"}, follow_redirects=False)
    assert r.status_code == 303
    assert "reset_ok" in r.headers["location"]

    logout(client)
    # Old password is dead, new one works.
    assert login(client, m["email"], m["password"]).headers["location"].startswith("/login")
    assert login(client, m["email"], "brandnew123").headers["location"] == "/dashboard"


def test_reset_rejects_short_and_blank_passwords(client):
    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])

    for bad in ("short", "   ", ""):
        r = client.post(f"/members/{mid}/password",
                        data={"new_password": bad}, follow_redirects=False)
        assert "reset_error" in r.headers["location"], f"accepted bad password {bad!r}"

    logout(client)
    # The original password still works, i.e. nothing was changed.
    assert login(client, m["email"], m["password"]).headers["location"] == "/dashboard"


def test_the_new_password_is_never_echoed_back(client):
    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    secret = "supersecret999"

    client.post(f"/members/{mid}/password", data={"new_password": secret})
    page = client.get("/members").text
    assert secret not in page, "the password was redisplayed on screen"


# --- Task 3: member changes their own password --------------------------


def test_member_can_change_own_password_with_current_one(client):
    register_trainer(client)
    m = add_member(client)
    logout(client)
    login(client, m["email"], m["password"])

    r = client.post("/account/password", data={
        "current_password": m["password"],
        "new_password": "myfreshpass1",
        "confirm_password": "myfreshpass1",
    }, follow_redirects=False)
    # Success signs you out and sends you to login.
    assert r.status_code == 303 and r.headers["location"].startswith("/login")

    assert login(client, m["email"], "myfreshpass1").headers["location"] == "/dashboard"


def test_change_fails_without_the_correct_current_password(client):
    register_trainer(client)
    m = add_member(client)
    logout(client)
    login(client, m["email"], m["password"])

    r = client.post("/account/password", data={
        "current_password": "not-the-right-one",
        "new_password": "myfreshpass1",
        "confirm_password": "myfreshpass1",
    }, follow_redirects=False)
    assert "/account/password?error=" in r.headers["location"]
    logout(client)
    assert login(client, m["email"], m["password"]).headers["location"] == "/dashboard"


def test_mismatched_confirmation_is_rejected(client):
    register_trainer(client)
    m = add_member(client)
    logout(client)
    login(client, m["email"], m["password"])

    r = client.post("/account/password", data={
        "current_password": m["password"],
        "new_password": "myfreshpass1",
        "confirm_password": "different-pass",
    }, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_wrong_current_password_error_is_generic(client):
    """Must not reveal which field was wrong or whether the account exists."""
    register_trainer(client)
    m = add_member(client)
    logout(client)
    login(client, m["email"], m["password"])

    r = client.post("/account/password", data={
        "current_password": "wrong",
        "new_password": "myfreshpass1",
        "confirm_password": "myfreshpass1",
    }, follow_redirects=False)
    msg = r.headers["location"].lower()
    for leak in ("no such user", "not found", "incorrect current", "wrong field"):
        assert leak not in msg


# --- Task 4: a password change kills existing sessions ------------------


def test_password_reset_invalidates_the_members_live_session(client):
    """The behaviour everyone expects from a reset, and easy to get wrong.

    JWTs here are stateless with a 7-day life, so without token_version a
    stolen token would keep working for a week after the reset.
    """
    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])

    # Member signs in on their own "device".
    logout(client)
    login(client, m["email"], m["password"])
    assert client.get("/dashboard", follow_redirects=False).status_code == 200

    member_cookie = client.cookies.get("access_token")
    assert member_cookie

    # Trainer resets the password from another session.
    logout(client)
    t = login(client, "trainer@example.com", "pw123456", role="trainer")
    assert t.headers["location"] == "/dashboard"
    client.post(f"/members/{mid}/password", data={"new_password": "resetpass123"})

    # The member's old token must no longer work.
    logout(client)
    client.cookies.set("access_token", member_cookie)
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303, "stale token still worked after a password reset"


def test_token_version_increments_on_every_change(client, db):
    from app.models import User

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])

    def version():
        with db.get_bind().connect() as _:
            pass
        from app.database import SessionLocal
        with SessionLocal() as s:
            return s.get(User, mid).token_version

    start = version()
    client.post(f"/members/{mid}/password", data={"new_password": "onepass123"})
    assert version() == start + 1
    client.post(f"/members/{mid}/password", data={"new_password": "twopass123"})
    assert version() == start + 2


# --- Task 5: audit trail ------------------------------------------------


def test_password_change_is_audited_without_storing_the_password(client):
    from app.database import SessionLocal
    from app.models import PasswordChange, PasswordChangeMethod

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    tid = user_id_by_email("trainer@example.com")

    client.post(f"/members/{mid}/password", data={"new_password": "audited123"})

    with SessionLocal() as s:
        rows = s.scalars(select(PasswordChange).where(PasswordChange.user_id == mid)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.method == PasswordChangeMethod.trainer
        assert row.changed_by_user_id == tid
        # The table must record only that a change happened.
        cols = {c.name for c in PasswordChange.__table__.columns}
        assert not (cols & {"password", "password_hash", "new_password", "hash"})


def test_self_service_change_is_recorded_as_self(client):
    from app.database import SessionLocal
    from app.models import PasswordChange, PasswordChangeMethod

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    logout(client)
    login(client, m["email"], m["password"])

    client.post("/account/password", data={
        "current_password": m["password"],
        "new_password": "selfchosen1",
        "confirm_password": "selfchosen1",
    })
    with SessionLocal() as s:
        row = s.scalar(select(PasswordChange).where(PasswordChange.user_id == mid))
        assert row.method == PasswordChangeMethod.self_service
        assert row.changed_by_user_id == mid


# --- Task 6: recovery contacts ------------------------------------------


def test_trainer_can_save_recovery_contacts(client):
    from app.database import SessionLocal
    from app.models import User

    register_trainer(client)
    tid = user_id_by_email("trainer@example.com")

    client.post("/account", data={"recovery_email": "backup@example.com",
                                  "recovery_phone": "+911234567890"})
    with SessionLocal() as s:
        u = s.get(User, tid)
        assert u.recovery_email == "backup@example.com"
        assert u.recovery_phone == "+911234567890"


def test_invalid_recovery_email_is_rejected(client):
    from app.database import SessionLocal
    from app.models import User

    register_trainer(client)
    tid = user_id_by_email("trainer@example.com")
    client.post("/account", data={"recovery_email": "nope@g", "recovery_phone": ""})
    with SessionLocal() as s:
        assert s.get(User, tid).recovery_email is None


# --- Task 7: login page ------------------------------------------------


def test_wrong_role_toggle_points_at_the_toggle(client):
    """A mis-tapped toggle used to look exactly like a wrong password."""
    register_trainer(client)
    r = login(client, "trainer@example.com", "pw123456", role="member")
    msg = r.headers["location"]
    assert "correct+role" in msg or "correct%20role" in msg
    # ...without confirming the account exists or naming its real role.
    assert "trainer+account" not in msg


def test_login_locks_out_after_repeated_failures(client):
    register_trainer(client)
    for _ in range(5):
        login(client, "trainer@example.com", "wrong-password", role="trainer")
    r = login(client, "trainer@example.com", "wrong-password", role="trainer")
    assert "Too+many+attempts" in r.headers["location"]


def test_lockout_does_not_leak_whether_the_account_exists(client):
    register_trainer(client)
    for _ in range(6):
        login(client, "ghost@example.com", "whatever", role="member")
    r = login(client, "ghost@example.com", "whatever", role="member")
    assert "Too+many+attempts" in r.headers["location"]


# --- Authorisation boundaries ------------------------------------------


def test_member_cannot_reset_anyone_password(client):
    register_trainer(client)
    a = add_member(client, "Alice", "alice@example.com")
    b = add_member(client, "Bob", "bob@example.com")
    aid, bid = user_id_by_email(a["email"]), user_id_by_email(b["email"])

    logout(client)
    login(client, a["email"], a["password"])

    # Not another member...
    assert client.post(f"/members/{bid}/password",
                       data={"new_password": "hijacked123"},
                       follow_redirects=False).status_code == 403
    # ...and not even themselves via the trainer route.
    assert client.post(f"/members/{aid}/password",
                       data={"new_password": "hijacked123"},
                       follow_redirects=False).status_code == 403

    logout(client)
    assert login(client, b["email"], b["password"]).headers["location"] == "/dashboard"


def test_password_pages_require_login(client):
    register_trainer(client)
    logout(client)
    for path in ("/account", "/account/password"):
        assert client.get(path, follow_redirects=False).status_code == 303


def test_trainer_cannot_be_reset_through_the_member_route(client):
    """The trainer route is for members only; trainers use the CLI script."""
    register_trainer(client)
    tid = user_id_by_email("trainer@example.com")
    r = client.post(f"/members/{tid}/password",
                    data={"new_password": "nope12345"}, follow_redirects=False)
    assert "reset_error" in r.headers["location"]
    logout(client)
    assert login(client, "trainer@example.com", "pw123456",
                 role="trainer").headers["location"] == "/dashboard"
