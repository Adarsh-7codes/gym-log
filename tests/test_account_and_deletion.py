"""Phase 1 — trainer/member profile editing.

Feature 1 of the wip/trainer-account-and-member-delete branch: any logged-in
user can change their own display name and login *email* at /account/profile.
This is how the seeded demo trainer becomes a real one -- same account, same
role, new identity. The role is deliberately NOT editable here.

Why this file exists at all: the feature changes the email you log in with. A
subtle bug here could lock the user out of the live trainer account. Each test
below pins one promise the feature makes; if a test goes red, that promise is
broken and the feature is unsafe to ship. (Member archive + permanent deletion
land in later phases and will extend this file.)
"""
from urllib.parse import unquote_plus

from sqlalchemy import select

from conftest import TRAINER, add_member, login, logout, register_trainer


def _flash(response):
    """The human-readable message the /account/profile route redirects with.

    Both success and error redirect to /account?saved=<message>, so this one
    helper serves every case. Returns "" if there is no message.
    """
    loc = response.headers["location"]
    return unquote_plus(loc.split("saved=", 1)[1]) if "saved=" in loc else ""


def _name_and_role(email):
    """(name, role-string) for an account, read via a fresh session.

    Opened fresh each call -- like conftest.user_id_by_email -- so it always
    reflects what the request handler committed, never a stale identity map.
    """
    from app.database import SessionLocal
    from app.models import User

    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.email == email))
        return (u.name, u.role.value) if u else (None, None)


# --- the happy path -----------------------------------------------------


def test_trainer_changes_name_and_email_then_logs_in_with_the_new_email(client):
    """The core promise: after editing, the NEW email is the login and the OLD
    one is not. A failure here means either the change did not persist or the
    old email still works -- both would confuse a user who just "moved" their
    account, and the second is a lingering-credential surprise."""
    register_trainer(client)  # first account -> trainer, and now logged in

    r = client.post(
        "/account/profile",
        data={"name": "Real Trainer", "email": "real@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Saved" in _flash(r)

    logout(client)
    # New email logs in; old email is dead.
    assert login(client, "real@example.com", TRAINER["password"],
                 role="trainer").headers["location"] == "/dashboard"
    assert login(client, TRAINER["email"], TRAINER["password"],
                 role="trainer").headers["location"].startswith("/login")


def test_role_is_unchanged_after_editing_profile(client):
    """Editing identity must never touch the role. If this fails, a trainer
    could accidentally demote themselves to member -- and there is no UI to make
    a trainer again, so that would be an unrecoverable footgun."""
    register_trainer(client)
    client.post("/account/profile",
                data={"name": "Real Trainer", "email": "real@example.com"})

    name, role = _name_and_role("real@example.com")
    assert name == "Real Trainer"
    assert role == "trainer"


# --- rejections: each proves a bad edit is refused AND changes nothing ---


def test_editing_rejects_an_email_already_used_by_another_account(client):
    """Two accounts must not share a login email -- login resolves by email, so
    a collision would make sign-in ambiguous. A red test here means the uniqueness
    guard is gone and the trainer could grab a member's email."""
    register_trainer(client)
    add_member(client, email="taken@example.com")  # a second account owns this

    r = client.post(
        "/account/profile",
        data={"name": TRAINER["name"], "email": "taken@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "already used" in _flash(r).lower()

    # Proof nothing changed: the trainer's original email still logs in.
    logout(client)
    assert login(client, TRAINER["email"], TRAINER["password"],
                 role="trainer").headers["location"] == "/dashboard"


def test_editing_rejects_a_blank_name(client):
    """A blank name would leave a nameless account across the roster and login
    banner. The guard lives in crud.update_profile; this catches its removal."""
    register_trainer(client)
    r = client.post(
        "/account/profile",
        data={"name": "   ", "email": "real@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "name" in _flash(r).lower()

    # And the account was not partially mutated (original email still works).
    logout(client)
    assert login(client, TRAINER["email"], TRAINER["password"],
                 role="trainer").headers["location"] == "/dashboard"


def test_editing_rejects_a_blank_email(client):
    """A blank email is a blank login -- unusable. Caught at the route as an
    invalid address before it can reach the database."""
    register_trainer(client)
    r = client.post(
        "/account/profile",
        data={"name": TRAINER["name"], "email": "   "},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "valid email" in _flash(r).lower()


def test_editing_rejects_an_invalid_email(client):
    """The same offline email validation the rest of the app uses must apply
    here: 'ada@12' has no real domain. If this passes the account could end up
    with an address that can never receive anything and looks like a typo."""
    register_trainer(client)
    r = client.post(
        "/account/profile",
        data={"name": TRAINER["name"], "email": "ada@12"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "valid email" in _flash(r).lower()


def test_a_member_can_edit_their_own_details_only(client):
    """The route edits current_user and nothing else -- there is no user_id
    parameter to forge. This test proves a member editing themselves leaves
    every other account untouched. If it fails, member self-service editing has
    started reaching across accounts."""
    register_trainer(client)
    add_member(client, name="Mo Member", email="mo@example.com")
    add_member(client, name="Sam Member", email="sam@example.com")

    logout(client)
    login(client, "mo@example.com", "pw123456")  # sign in as Mo

    r = client.post(
        "/account/profile",
        data={"name": "Mo Renamed", "email": "mo2@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "Saved" in _flash(r)

    # Mo changed; Sam and the trainer did not.
    assert _name_and_role("mo2@example.com") == ("Mo Renamed", "member")
    assert _name_and_role("sam@example.com")[0] == "Sam Member"
    assert _name_and_role(TRAINER["email"])[0] == TRAINER["name"]
