"""Phase 3 — attendance.

Fixes the flaw where "inactive" was derived from logs, so a member who trained
six times but logged nothing looked identical to one who quit.
"""
from datetime import date, timedelta

from conftest import add_member, login, logout, register_trainer, user_id_by_email


def test_marking_present_twice_on_one_day_creates_one_row(client):
    """Idempotent by design: a double tap at a busy desk must not double count."""
    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.models import Attendance

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    today = date.today().isoformat()

    client.post(f"/attendance/{mid}/toggle", data={"on": today})
    with SessionLocal() as s:
        assert s.scalar(select(func.count(Attendance.id)).where(Attendance.user_id == mid)) == 1

    # Toggling off then on again still leaves exactly one row.
    client.post(f"/attendance/{mid}/toggle", data={"on": today})
    client.post(f"/attendance/{mid}/toggle", data={"on": today})
    with SessionLocal() as s:
        assert s.scalar(select(func.count(Attendance.id)).where(Attendance.user_id == mid)) == 1


def test_toggle_can_unmark_a_mistake(client):
    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.models import Attendance

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    today = date.today().isoformat()

    client.post(f"/attendance/{mid}/toggle", data={"on": today})
    client.post(f"/attendance/{mid}/toggle", data={"on": today})
    with SessionLocal() as s:
        assert s.scalar(select(func.count(Attendance.id)).where(Attendance.user_id == mid)) == 0


def test_attendance_not_logging_decides_inactivity(client):
    """The whole point of Phase 3.

    Before it, "inactive" came from the Log table, so someone who trained but
    never logged looked identical to someone who quit. Attending must clear the
    inactivity reason even with zero workout logs.
    """
    from app import crud
    from app.database import SessionLocal
    from app.models import User

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])

    client.post(f"/attendance/{mid}/toggle", data={"on": date.today().isoformat()})

    with SessionLocal() as s:
        st = crud.member_status(s, s.get(User, mid))

    assert not st["inactive"], "a member who attended today was marked inactive"
    # No log rows exist, yet nothing may claim they have not trained.
    assert not any("No session" in r or "No sessions" in r for r in st["reasons"]), st["reasons"]


def test_regular_attendance_leaves_a_member_completely_unflagged(client):
    """Two sessions in the last week also clears the new-member cohort check."""
    from app import crud
    from app.database import SessionLocal
    from app.models import User

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])

    today = date.today()
    client.post(f"/attendance/{mid}/toggle", data={"on": today.isoformat()})
    client.post(f"/attendance/{mid}/toggle",
                data={"on": (today - timedelta(days=3)).isoformat()})

    with SessionLocal() as s:
        st = crud.member_status(s, s.get(User, mid))
    assert not st["flagged"], st["reasons"]


def test_sessions_this_month_counts_correctly(client):
    from app import crud
    from app.database import SessionLocal

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    today = date.today()

    # Only days inside the current month should count.
    marked = 0
    for delta in (0, 1, 2):
        d = today - timedelta(days=delta)
        if d.month == today.month:
            client.post(f"/attendance/{mid}/toggle", data={"on": d.isoformat()})
            marked += 1

    with SessionLocal() as s:
        assert crud.attendance_this_month(s, mid) == marked


def test_today_screen_lists_members_and_shows_counts(client):
    register_trainer(client)
    add_member(client, "Alice", "alice@example.com")
    add_member(client, "Bob", "bob@example.com")

    page = client.get("/today").text
    assert "Alice" in page and "Bob" in page
    assert "attrow" in page


def test_member_sees_own_attendance_read_only(client):
    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    client.post(f"/attendance/{mid}/toggle", data={"on": date.today().isoformat()})

    logout(client)
    login(client, m["email"], m["password"])
    page = client.get("/dashboard").text
    assert "Your attendance" in page
    # No way to mark themselves present.
    assert client.get("/today", follow_redirects=False).status_code == 403
