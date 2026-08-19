"""Phase 1 — membership and dues.

The trainer's daily question: who owes me money, and whose plan ends this week.
"""
from datetime import date, timedelta
from decimal import Decimal

from conftest import add_member, login, logout, register_trainer, user_id_by_email


def test_add_months_clamps_to_the_end_of_short_months(client):
    from app.crud import add_months

    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)   # leap year
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)  # year rollover
    assert add_months(date(2026, 3, 31), 1) == date(2026, 4, 30)


def test_three_month_membership_stores_the_right_expiry(client):
    from app import crud
    from app.database import SessionLocal
    from app.models import MembershipStatus

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    today = date.today()

    client.post(f"/members/{mid}/membership", data={
        "plan_start": today.isoformat(), "duration_months": "3",
        "amount": "3000", "status": "paid"})

    with SessionLocal() as s:
        cur = crud.current_membership(s, mid)
        assert cur.expires_on == crud.add_months(today, 3)
        assert cur.status == MembershipStatus.paid
        assert cur.paid_on is not None


def test_expired_membership_is_flagged_and_sorted_first(client):
    from app import crud
    from app.database import SessionLocal

    register_trainer(client)
    late = add_member(client, "Late Larry", "larry@example.com")
    fine = add_member(client, "Fine Fiona", "fiona@example.com")
    lid, fid = user_id_by_email(late["email"]), user_id_by_email(fine["email"])
    today = date.today()

    # Larry: started 4 months ago on a 3-month plan, unpaid.
    client.post(f"/members/{lid}/membership", data={
        "plan_start": crud.add_months(today, -4).isoformat(), "duration_months": "3",
        "amount": "2500", "status": "pending"})
    # Fiona: healthy and paid.
    client.post(f"/members/{fid}/membership", data={
        "plan_start": today.isoformat(), "duration_months": "3",
        "amount": "3000", "status": "paid"})

    with SessionLocal() as s:
        roster = crud.member_roster(s)
        names = [r["user"].name for r in roster]
        larry = next(r for r in roster if r["user"].id == lid)
        assert larry["expired"] and larry["days_to_expiry"] < 0
        assert names[0] == "Late Larry", f"expected the flagged member first, got {names}"


def test_membership_expiring_within_a_week_is_marked_soon(client):
    from app import crud
    from app.database import SessionLocal

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    today = date.today()
    start = crud.add_months(today, -3) + timedelta(days=5)

    client.post(f"/members/{mid}/membership", data={
        "plan_start": start.isoformat(), "duration_months": "3",
        "amount": "1000", "status": "paid"})

    from app.models import User

    with SessionLocal() as s:
        st = crud.member_status(s, s.get(User, mid))
        assert st["expiring_soon"], f"days_to_expiry={st['days_to_expiry']}"


def test_marking_paid_drops_the_dues_total(client):
    from app import crud
    from app.database import SessionLocal

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])

    client.post(f"/members/{mid}/membership", data={
        "plan_start": date.today().isoformat(), "duration_months": "3",
        "amount": "2500", "status": "pending"})

    with SessionLocal() as s:
        before = crud.roster_summary(crud.member_roster(s))["pending_dues"]
        mem_id = crud.current_membership(s, mid).id
    assert before == Decimal("2500")

    client.post(f"/membership/{mem_id}/paid")

    with SessionLocal() as s:
        after = crud.roster_summary(crud.member_roster(s))["pending_dues"]
    assert after == Decimal("0")


def test_member_sees_own_membership_but_no_dues_chasing(client):
    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    client.post(f"/members/{mid}/membership", data={
        "plan_start": date.today().isoformat(), "duration_months": "3",
        "amount": "3000", "status": "pending"})

    logout(client)
    login(client, m["email"], m["password"])
    page = client.get("/dashboard").text

    assert "Your membership" in page and "Valid till" in page
    for trainer_only in ("Pending dues", "Add renewal", "Mark paid"):
        assert trainer_only not in page
