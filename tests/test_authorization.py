"""Authorisation boundaries — the highest-stakes tests in the project.

The attack these defend against is IDOR: changing an id in a URL to read or
write someone else's data. Everything here is enforced server-side; hiding a
button in a template is not a control.
"""
from conftest import add_member, login, logout, register_trainer, user_id_by_email


TRAINER_ONLY_PAGES = ["/members", "/exercises", "/today"]


def test_member_cannot_open_trainer_pages(client):
    register_trainer(client)
    m = add_member(client)
    logout(client)
    login(client, m["email"], m["password"])

    for path in TRAINER_ONLY_PAGES:
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (403, 303), f"{path} was reachable by a member"


def test_member_cannot_see_another_members_logs(client):
    """A forged ?user_id= must return the caller's own data, never someone else's."""
    from datetime import date

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Exercise, Log, Role

    register_trainer(client)
    a = add_member(client, "Alice", "alice@example.com")
    b = add_member(client, "Bob", "bob@example.com")
    aid, bid = user_id_by_email(a["email"]), user_id_by_email(b["email"])

    with SessionLocal() as s:
        ex = s.scalar(select(Exercise.id))
        s.add(Log(user_id=bid, exercise_id=ex, date=date.today(),
                  weight=123.5, reps=5, sets=3, logged_by=Role.member))
        s.commit()

    logout(client)
    login(client, a["email"], a["password"])
    page = client.get(f"/dashboard?user_id={bid}").text
    assert "123.5" not in page, "one member could read another's logs by forging user_id"


def test_member_cannot_edit_another_members_routine_or_split(client):
    from app import crud
    from app.database import SessionLocal
    from app.models import BodyPart

    register_trainer(client)
    a = add_member(client, "Alice", "alice@example.com")
    b = add_member(client, "Bob", "bob@example.com")
    aid, bid = user_id_by_email(a["email"]), user_id_by_email(b["email"])

    logout(client)
    login(client, a["email"], a["password"])
    # Alice tries to write to Bob's split; it must silently apply to herself.
    client.post("/split", data={"d1": ["legs"], "user_id": str(bid)})

    with SessionLocal() as s:
        assert crud.body_parts_for_day(s, bid, 1) == []
        assert [p.value for p in crud.body_parts_for_day(s, aid, 1)] == ["legs"]


def test_member_cannot_write_membership_or_attendance(client):
    from datetime import date

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    logout(client)
    login(client, m["email"], m["password"])

    writes = [
        (f"/members/{mid}/membership",
         {"plan_start": date.today().isoformat(), "duration_months": "3",
          "amount": "1", "status": "paid"}),
        (f"/attendance/{mid}/toggle", {"on": date.today().isoformat()}),
        (f"/members/{mid}/weight", {"weight_kg": "80"}),
    ]
    for path, data in writes:
        r = client.post(path, data=data, follow_redirects=False)
        assert r.status_code == 403, f"{path} accepted a member write"


def test_unauthenticated_api_is_rejected(client):
    register_trainer(client)
    logout(client)
    assert client.get("/api/logs").status_code == 401


def test_api_member_cannot_read_other_members_logs(client):
    """The JSON API must scope by owner exactly like the web pages."""
    register_trainer(client)
    m = add_member(client)
    logout(client)

    tok = client.post("/api/auth/login",
                      data={"username": m["email"], "password": m["password"]}).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}
    # Forging another user's id must not widen the result set.
    mine = client.get("/api/logs", headers=headers).json()
    forged = client.get("/api/logs?user_id=1", headers=headers).json()
    assert forged == mine


def test_demo_reset_endpoint_is_unreachable_on_this_deployment(client, monkeypatch):
    """Phase 0 made it local-SQLite-only *and* token-guarded; with no token it is 404."""
    monkeypatch.delenv("RESET_TOKEN", raising=False)
    assert client.get("/danger/reset?token=anything", follow_redirects=False).status_code == 404
