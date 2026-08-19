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

from conftest import (
    TRAINER,
    add_member,
    login,
    logout,
    register_trainer,
    user_id_by_email,
)


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


# ======================================================================
# Phase 2 — archive / deactivate a member (the reversible default)
#
# Archiving is a soft "they left the gym": the member disappears from the
# active screens and cannot sign in, but nothing is deleted and a restore
# brings them fully back. Permanent deletion is a separate, harder step that
# now requires the member to be archived first (tested here; the full cascade
# is Phase 3). Each test below pins one promise; a red one means the archive
# boundary leaks -- either an archived member is still visible/loggable, or an
# archive was destructive, or the two-step delete guard is gone.
# ======================================================================


def _is_archived(email):
    """Whether an account is archived, via a fresh session."""
    from app.database import SessionLocal
    from app.models import User

    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.email == email))
        return u.is_archived if u else None


def test_archiving_hides_the_member_from_active_screens_but_keeps_the_account(client):
    """The core promise: after archiving, the member is gone from the roster,
    the attendance screen and the trainer's log picker -- but the account still
    exists (archived, not deleted). If this fails, either archiving didn't hide
    them (defeating the feature) or it destroyed data (it must not)."""
    register_trainer(client)
    add_member(client, name="Zoltan Hidden", email="zoltan@example.com")
    mid = user_id_by_email("zoltan@example.com")

    # Visible on every active screen while active.
    assert "Zoltan Hidden" in client.get("/dashboard").text
    assert "Zoltan Hidden" in client.get("/today").text
    assert "Zoltan Hidden" in client.get("/logs/new").text

    r = client.post(f"/members/{mid}/archive", follow_redirects=False)
    assert r.status_code == 303 and "reset_ok" in r.headers["location"]

    # Gone from all three active screens...
    assert "Zoltan Hidden" not in client.get("/dashboard").text
    assert "Zoltan Hidden" not in client.get("/today").text
    assert "Zoltan Hidden" not in client.get("/logs/new").text
    # ...but the account is kept, merely flagged archived.
    assert _is_archived("zoltan@example.com") is True


def test_an_archived_member_cannot_log_in_while_an_active_one_still_can(client):
    """Archiving deactivates the login. A red test means a member who 'left' can
    still get in -- or, worse, that active members were locked out too."""
    register_trainer(client)
    add_member(client, name="Gone Away", email="gone@example.com")
    add_member(client, name="Still Here", email="here@example.com")
    logout(client)

    # The trainer must archive, so sign back in as trainer to do it.
    login(client, TRAINER["email"], TRAINER["password"], role="trainer")
    client.post(f"/members/{user_id_by_email('gone@example.com')}/archive")
    logout(client)

    r = login(client, "gone@example.com", "pw123456")
    assert r.headers["location"].startswith("/login")
    assert "deactivated" in unquote_plus(r.headers["location"]).lower()

    # The other member is unaffected.
    assert login(client, "here@example.com", "pw123456").headers["location"] == "/dashboard"


def test_archiving_signs_the_member_out_of_a_live_session(client):
    """Archiving bumps token_version, so a member already signed in is kicked out
    at their next request rather than lingering until the token expires. If this
    fails, an archived member keeps a working session for up to a week."""
    register_trainer(client)  # trainer session lives in `client`
    add_member(client, name="Zed Session", email="zed@example.com")

    from fastapi.testclient import TestClient
    from app.main import app

    member = TestClient(app)  # a separate session (cookie jar) for the member
    assert login(member, "zed@example.com", "pw123456").headers["location"] == "/dashboard"
    assert member.get("/dashboard", follow_redirects=False).status_code == 200

    client.post(f"/members/{user_id_by_email('zed@example.com')}/archive")

    # The member's still-held cookie is now stale -> bounced to login.
    r = member.get("/dashboard", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/login" in r.headers["location"]


def test_restoring_a_member_brings_them_back_and_lets_them_log_in(client):
    """Restore is the whole point of archiving over deleting. After it, the
    member reappears on the roster and can sign in again with their old
    password (archive never touched it)."""
    register_trainer(client)
    add_member(client, name="Come Back", email="back@example.com")
    mid = user_id_by_email("back@example.com")

    client.post(f"/members/{mid}/archive")
    assert "Come Back" not in client.get("/dashboard").text

    r = client.post(f"/members/{mid}/restore", follow_redirects=False)
    assert r.status_code == 303 and "reset_ok" in r.headers["location"]
    assert "Come Back" in client.get("/dashboard").text
    assert _is_archived("back@example.com") is False

    logout(client)
    assert login(client, "back@example.com", "pw123456").headers["location"] == "/dashboard"


def test_the_trainer_cannot_be_archived(client):
    """The archive route is member-only. Archiving the sole trainer would lock
    the whole app; this guards against it. The trainer stays active and can log
    in afterwards."""
    register_trainer(client)
    tid = user_id_by_email(TRAINER["email"])

    r = client.post(f"/members/{tid}/archive", follow_redirects=False)
    assert r.status_code == 303 and "reset_error" in r.headers["location"]
    assert _is_archived(TRAINER["email"]) is False

    logout(client)
    assert login(client, TRAINER["email"], TRAINER["password"],
                 role="trainer").headers["location"] == "/dashboard"


def test_a_member_cannot_archive_anyone(client):
    """Archiving is a trainer power (require_trainer_web -> 403 for members).
    A red test means a member could deactivate another member's account."""
    register_trainer(client)
    add_member(client, name="Victim", email="victim@example.com")
    add_member(client, name="Attacker", email="attacker@example.com")
    vid = user_id_by_email("victim@example.com")

    logout(client)
    login(client, "attacker@example.com", "pw123456")
    r = client.post(f"/members/{vid}/archive", follow_redirects=False)
    assert r.status_code == 403
    assert _is_archived("victim@example.com") is False


def test_permanent_delete_requires_archiving_first(client):
    """Two-step deletion: an ACTIVE member cannot be permanently deleted. The
    irreversible action is only reachable after archiving. If this fails, the
    one-tap permanent delete we deliberately removed has crept back."""
    register_trainer(client)
    add_member(client, name="Delete Me", email="del@example.com")
    mid = user_id_by_email("del@example.com")

    # Correct name typed, but the member is not archived -> refused.
    r = client.post(f"/members/{mid}/delete",
                    data={"confirm_name": "Delete Me"}, follow_redirects=False)
    assert r.status_code == 303 and "reset_error" in r.headers["location"]
    assert "archive" in unquote_plus(r.headers["location"]).lower()

    # The account is untouched.
    assert user_id_by_email("del@example.com") == mid


def test_members_page_shows_archive_for_active_and_restore_for_archived(client):
    """The members page is where the whole Phase 2 UI lives -- an active member
    offers Archive, an archived one offers Restore and is badged. This also
    guards the template itself against a rendering error (a GET that 500s here
    would fail this test)."""
    register_trainer(client)
    add_member(client, name="Active Amy", email="amy@example.com")
    add_member(client, name="Archived Al", email="al@example.com")
    client.post(f"/members/{user_id_by_email('al@example.com')}/archive")

    page = client.get("/members")
    assert page.status_code == 200
    # Active member: an Archive form; archived member: a Restore form + badge.
    assert f"/members/{user_id_by_email('amy@example.com')}/archive" in page.text
    assert f"/members/{user_id_by_email('al@example.com')}/restore" in page.text
    assert ">archived<" in page.text


# ======================================================================
# Phase 3 — permanent deletion (the irreversible second step)
#
# Deletion is only reachable once a member is archived. When it runs it must
# remove the member and EVERY row they own, leave every other member wholly
# intact, and preserve audit evidence that lives in someone else's history.
# The most important test here is the orphan-trap guard: it protects future
# contributors from adding a user-owned table and forgetting to delete it.
# ======================================================================


def _seed_full_history(uid):
    """Give a member exactly one row in every user-owned table (+ a PlanItem).

    Inserted directly (not via routes) so the test states plainly which tables
    it expects delete_member to clear.
    """
    from datetime import date

    from app.database import SessionLocal
    from app.models import (
        Attendance, BodyPart, BodyWeight, Exercise, Log, MemberRoutine,
        Membership, PasswordChange, PasswordChangeMethod, PlanDay, PlanItem,
        SplitDay, Target,
    )

    with SessionLocal() as s:
        eid = s.scalar(select(Exercise.id))
        s.add_all([
            Log(user_id=uid, exercise_id=eid, date=date.today(), weight=50.0, reps=5, sets=3),
            Attendance(user_id=uid, date=date.today()),
            Membership(user_id=uid, plan_start=date.today(), duration_months=1,
                       expires_on=date.today()),
            MemberRoutine(user_id=uid, exercise_id=eid, body_part=BodyPart.chest),
            SplitDay(user_id=uid, weekday=0, body_part=BodyPart.chest),
            Target(user_id=uid, exercise_id=eid, target_weight=60.0, target_date=date.today()),
            BodyWeight(user_id=uid, date=date.today(), weight_kg=70.0),
            PasswordChange(user_id=uid, changed_by_user_id=uid,
                           method=PasswordChangeMethod.self_service),
        ])
        pd = PlanDay(user_id=uid, weekday=0)
        s.add(pd)
        s.flush()
        s.add(PlanItem(plan_day_id=pd.id, exercise_id=eid))
        s.commit()


def _history_counts(uid):
    """Rows still owned by a user id, per user-owned table (fresh session)."""
    from sqlalchemy import func

    from app import crud
    from app.database import SessionLocal

    with SessionLocal() as s:
        return {
            m.__tablename__: s.scalar(
                select(func.count()).select_from(m).where(m.user_id == uid)
            )
            for m in crud.USER_OWNED_MODELS
        }


def test_delete_member_handles_every_table_that_references_a_user(client):
    """Orphan-trap guard -- the test the handoff calls the one that protects
    future contributors. It reflects the schema for any table with a user_id
    column and fails if crud.USER_OWNED_MODELS doesn't cover it. Without this,
    a new user-owned table added months from now would silently leave orphaned
    rows behind every deletion, and nothing would notice."""
    from app import crud
    from app.models import Base

    handled = {m.__tablename__ for m in crud.USER_OWNED_MODELS}
    with_user_id = {
        t.name for t in Base.metadata.tables.values() if "user_id" in t.columns
    }
    missing = with_user_id - handled
    assert not missing, (
        f"crud.delete_member() ignores tables with a user_id column: {missing}. "
        f"Add them to crud.USER_OWNED_MODELS or a deleted member orphans their rows."
    )


def test_deleting_an_archived_member_wipes_all_their_data(client):
    """The whole contract of permanent deletion: the user and every row they
    own -- across all nine tables, plus the PlanItem hanging off their PlanDay --
    are gone. If any count is non-zero, deletion is leaking orphans."""
    register_trainer(client)
    add_member(client, name="Full History", email="fh@example.com")
    mid = user_id_by_email("fh@example.com")
    _seed_full_history(mid)

    before = _history_counts(mid)
    assert all(v >= 1 for v in before.values()), f"seed incomplete: {before}"

    client.post(f"/members/{mid}/archive")
    r = client.post(f"/members/{mid}/delete",
                    data={"confirm_name": "Full History"}, follow_redirects=False)
    assert r.status_code == 303 and "reset_ok" in r.headers["location"]

    assert user_id_by_email("fh@example.com") is None
    after = _history_counts(mid)
    assert after == {t: 0 for t in after}, f"orphans left behind: {after}"

    # The PlanItem (no user_id of its own) went with its parent PlanDay.
    from sqlalchemy import func

    from app.database import SessionLocal
    from app.models import PlanItem

    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(PlanItem)) == 0


def test_deleting_one_member_leaves_other_members_data_intact(client):
    """Deletion must be surgical. Seed two members with identical full histories,
    delete one, and the other's rows must all survive. A red test means deletion
    is over-reaching across accounts -- catastrophic on real data."""
    register_trainer(client)
    add_member(client, name="Doomed", email="doomed@example.com")
    add_member(client, name="Keeper", email="keeper@example.com")
    did = user_id_by_email("doomed@example.com")
    kid = user_id_by_email("keeper@example.com")
    _seed_full_history(did)
    _seed_full_history(kid)

    client.post(f"/members/{did}/archive")
    client.post(f"/members/{did}/delete", data={"confirm_name": "Doomed"})

    assert _history_counts(did) == {t: 0 for t in _history_counts(did)}
    keeper = _history_counts(kid)
    assert all(v >= 1 for v in keeper.values()), f"keeper lost data: {keeper}"
    assert user_id_by_email("keeper@example.com") == kid


def test_deleting_a_member_nulls_audit_rows_they_authored_for_others(client):
    """A deleted member may be the *author* (changed_by_user_id) of a password
    change recorded against ANOTHER account. That audit row belongs to the other
    account's history: it must survive with its author cleared, never be deleted.
    A red test means deleting one member erases evidence from another's record."""
    register_trainer(client)
    add_member(client, name="Author Gone", email="author@example.com")
    add_member(client, name="Subject Stays", email="subject@example.com")
    aid = user_id_by_email("author@example.com")
    sid = user_id_by_email("subject@example.com")

    from app.database import SessionLocal
    from app.models import PasswordChange, PasswordChangeMethod

    with SessionLocal() as s:
        s.add(PasswordChange(user_id=sid, changed_by_user_id=aid,
                             method=PasswordChangeMethod.trainer))
        s.commit()

    client.post(f"/members/{aid}/archive")
    client.post(f"/members/{aid}/delete", data={"confirm_name": "Author Gone"})

    with SessionLocal() as s:
        row = s.scalar(select(PasswordChange).where(PasswordChange.user_id == sid))
        assert row is not None, "the subject's audit row was wrongly deleted"
        assert row.changed_by_user_id is None, "the deleted author was not cleared"


def test_delete_refuses_a_wrong_or_empty_confirmation_name(client):
    """Even on an archived member, the typed name must match exactly. This is the
    last guard before an irreversible action; a mistyped or empty box must be a
    no-op, not a deletion."""
    register_trainer(client)
    add_member(client, name="Careful Carl", email="carl@example.com")
    mid = user_id_by_email("carl@example.com")
    client.post(f"/members/{mid}/archive")

    for bad in ("wrong name", "", "   "):
        r = client.post(f"/members/{mid}/delete",
                        data={"confirm_name": bad}, follow_redirects=False)
        assert r.status_code == 303 and "reset_error" in r.headers["location"]
        assert user_id_by_email("carl@example.com") == mid, f"deleted on {bad!r}"


def test_delete_control_is_shown_only_for_archived_members(client):
    """The two-step rule made visible: the permanent-delete form renders on an
    archived member's row and NOT on an active one. If it appears on an active
    row, the one-tap delete we removed has returned to the UI."""
    register_trainer(client)
    add_member(client, name="Active One", email="a1@example.com")
    add_member(client, name="Archived One", email="a2@example.com")
    aid = user_id_by_email("a1@example.com")
    zid = user_id_by_email("a2@example.com")
    client.post(f"/members/{zid}/archive")

    page = client.get("/members").text
    assert f"/members/{zid}/delete" in page, "no delete control on the archived member"
    assert f"/members/{aid}/delete" not in page, "delete control leaked onto an active member"
