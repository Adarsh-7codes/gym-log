"""Exercise library, weekly split, trainer-assigned routines and fast logging.

Covers the original library work and Phase 2. The invariant that matters most
here is the last test: changing a routine must never destroy training history.
"""
from datetime import date

from conftest import add_member, login, logout, register_trainer, user_id_by_email


def chest_ids(n=4):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import BodyPart, Exercise

    with SessionLocal() as s:
        rows = s.scalars(select(Exercise).where(Exercise.body_part == BodyPart.chest)).all()[:n]
        return [str(e.id) for e in rows], [e.name for e in rows]


def test_exercise_library_is_seeded_on_startup(client):
    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.models import BodyPart, Exercise

    with SessionLocal() as s:
        total = s.scalar(select(func.count(Exercise.id)))
        parts = {bp for (bp,) in s.execute(select(Exercise.body_part).distinct()).all()}
    assert total >= 40, f"only {total} exercises seeded"
    assert set(BodyPart) <= parts, "every body part should have exercises"


def test_a_weekday_can_hold_two_body_parts(client):
    """Real people train chest and arms on the same day."""
    from app import crud
    from app.database import SessionLocal

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    logout(client)
    login(client, m["email"], m["password"])

    client.post("/split", data={"d0": ["chest", "arms"]})
    with SessionLocal() as s:
        assert {p.value for p in crud.body_parts_for_day(s, mid, 0)} == {"chest", "arms"}


def test_log_screen_shows_only_todays_body_parts(client):
    from app import crud
    from app.database import SessionLocal
    from app.models import BodyPart

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    ids, names = chest_ids(2)

    with SessionLocal() as s:
        back = crud.library_grouped(s)[BodyPart.back][:2]
        back_names = [e.name for e in back]
        crud.set_routine_for_body_part(s, mid, BodyPart.back, [e.id for e in back])

    client.post("/library/chest", data={"exercise_ids": ids, "user_id": str(mid)})
    client.post("/split", data={"d0": ["chest"], "user_id": str(mid)})

    logout(client)
    login(client, m["email"], m["password"])
    monday = client.get("/logs/new?weekday=0").text

    for n in names:
        assert n in monday
    for n in back_names:
        assert n not in monday, "an unscheduled body part leaked onto the day"


def test_rest_day_says_so(client):
    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    client.post("/split", data={"d0": ["chest"], "user_id": str(mid)})

    logout(client)
    login(client, m["email"], m["password"])
    assert "rest day" in client.get("/logs/new?weekday=1").text.lower()


def test_trainer_assigned_exercises_are_marked_and_have_demo_links(client):
    """Phase 2: the member should see who prescribed it, and how to do it."""
    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    ids, names = chest_ids(4)

    client.post("/split", data={"d0": ["chest"], "user_id": str(mid)})
    client.post("/library/chest", data={"exercise_ids": ids, "user_id": str(mid)})

    logout(client)
    login(client, m["email"], m["password"])
    page = client.get("/logs/new?weekday=0").text

    assert all(n in page for n in names)
    assert page.count("assigned by your trainer") >= 4
    # Every exercise gets a link that resolves, even with no specific video set.
    assert page.count('class="howto"') >= 4
    assert "youtube.com/results" in page


def test_member_self_selection_is_not_marked_as_trainer_assigned(client):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import MemberRoutine, Role

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    ids, _ = chest_ids(2)

    logout(client)
    login(client, m["email"], m["password"])
    client.post("/library/chest", data={"exercise_ids": ids})

    with SessionLocal() as s:
        rows = s.scalars(select(MemberRoutine).where(MemberRoutine.user_id == mid)).all()
        assert rows and all(r.assigned_by == Role.member for r in rows)


def test_logging_records_who_entered_it(client):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Log, Role

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    ids, _ = chest_ids(1)

    # Trainer logs on the member's behalf.
    client.post("/logs/new", data={"exercise_id": ids[0], "log_date": date.today().isoformat(),
                                   "weight": "50", "reps": "8", "sets": "3", "user_id": str(mid)})
    # Member logs their own.
    logout(client)
    login(client, m["email"], m["password"])
    client.post("/logs/new", data={"exercise_id": ids[0], "log_date": date.today().isoformat(),
                                   "weight": "55", "reps": "8", "sets": "3", "weekday": "0"})

    with SessionLocal() as s:
        rows = s.scalars(select(Log).where(Log.user_id == mid).order_by(Log.id)).all()
        assert [r.logged_by for r in rows] == [Role.trainer, Role.member]


def test_removing_an_exercise_from_a_routine_keeps_its_log_history(client):
    """The single most important data rule in the app."""
    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.models import Log

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    ids, _ = chest_ids(2)

    client.post("/library/chest", data={"exercise_ids": ids, "user_id": str(mid)})
    client.post("/logs/new", data={"exercise_id": ids[0], "log_date": date.today().isoformat(),
                                   "weight": "60", "reps": "5", "sets": "3", "user_id": str(mid)})

    with SessionLocal() as s:
        assert s.scalar(select(func.count(Log.id)).where(Log.user_id == mid)) == 1

    # Clear the whole chest routine.
    client.post("/library/chest", data={"exercise_ids": [], "user_id": str(mid)})

    with SessionLocal() as s:
        still = s.scalar(select(func.count(Log.id)).where(Log.user_id == mid))
    assert still == 1, "clearing a routine destroyed training history"
