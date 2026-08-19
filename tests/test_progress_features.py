"""Phases 4-6 — talking points, overload targets, and body weight.

These three share a theme: the app states facts it can verify and stays silent
otherwise. Several tests below exist purely to stop tempting-but-dishonest
features creeping back in.
"""
from datetime import date, timedelta

from conftest import add_member, login, logout, register_trainer, user_id_by_email


def seed_bench_progress(mid, weeks=((6, 40), (4, 42.5), (2, 45), (0, 47.5))):
    """Six weeks of real improvement on one lift."""
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Exercise, Log, Role

    today = date.today()
    with SessionLocal() as s:
        ex = s.scalar(select(Exercise).where(Exercise.name == "Barbell Bench Press"))
        for wk, w in weeks:
            s.add(Log(user_id=mid, exercise_id=ex.id, date=today - timedelta(weeks=wk),
                      weight=w, reps=5, sets=3, logged_by=Role.member))
        s.commit()
        return ex.id


# --- Phase 4: talking points -------------------------------------------


def test_real_progress_produces_a_correct_improvement_line(client):
    from app import crud
    from app.database import SessionLocal

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    seed_bench_progress(mid)

    with SessionLocal() as s:
        points = crud.talking_points(s, mid)
    line = next((p for p in points if "→" in p), None)
    assert line, f"no improvement line in {points}"
    assert "40" in line and "47.5" in line and "6 weeks" in line


def test_thin_data_produces_no_talking_points_rather_than_filler(client):
    """If there is nothing true to say, say nothing."""
    from app import crud
    from app.database import SessionLocal

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    # Two identical sessions: no trend, no attendance.
    seed_bench_progress(mid, weeks=((1, 50), (0, 50)))

    with SessionLocal() as s:
        assert crud.talking_points(s, mid) == []


def test_talking_points_never_moralise(client):
    """The app cannot observe diet or effort, so it must never mention them."""
    from app import crud
    from app.database import SessionLocal

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    seed_bench_progress(mid)
    client.post(f"/attendance/{mid}/toggle", data={"on": date.today().isoformat()})

    with SessionLocal() as s:
        text = " ".join(crud.talking_points(s, mid)).lower()
    for banned in ("diet", "lazy", "junk", "effort", "motivat", "discipline", "should", "excuse"):
        assert banned not in text, f"talking point used judgemental word {banned!r}"


def test_members_never_see_talking_points_about_themselves(client):
    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    seed_bench_progress(mid)

    assert "Talking points" in client.get(f"/dashboard?user_id={mid}").text
    logout(client)
    login(client, m["email"], m["password"])
    assert "Talking points" not in client.get("/dashboard").text


def test_new_member_with_no_attendance_is_not_flagged(client):
    """Otherwise every member lights up red the day the feature ships."""
    from app import crud
    from app.database import SessionLocal
    from app.models import User

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])

    with SessionLocal() as s:
        cohort = crud.cohort_stats(s, s.get(User, mid))
    assert cohort["is_new"]
    assert not cohort["needs_attention"]


# --- Phase 5: overload targets -----------------------------------------


def test_target_shows_the_gap_and_plateau_duration(client):
    from app import crud
    from app.database import SessionLocal
    from app.models import Exercise, Log, Role
    from sqlalchemy import select

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    today = date.today()

    with SessionLocal() as s:
        ex = s.scalar(select(Exercise).where(Exercise.name == "Barbell Back Squat"))
        for d, w in ((40, 35), (28, 40), (21, 45), (7, 45), (1, 45)):
            s.add(Log(user_id=mid, exercise_id=ex.id, date=today - timedelta(days=d),
                      weight=w, reps=5, sets=3, logged_by=Role.member))
        s.commit()
        ex_id = ex.id

    client.post(f"/members/{mid}/targets", data={
        "exercise_id": str(ex_id), "target_weight": "60", "target_reps": "5",
        "target_date": (today + timedelta(days=30)).isoformat()})

    with SessionLocal() as s:
        t = crud.targets_for(s, mid)[0]
    assert not t["reached"]
    assert t["best"]["weight"] == 45
    assert t["gap"] == 15
    assert t["weeks_at_best"] == 3   # measured from when 45 was FIRST hit
    assert t["days_left"] == 30


def test_hitting_the_weight_marks_the_target_reached(client):
    from app import crud
    from app.database import SessionLocal
    from app.models import Exercise, Log, Role
    from sqlalchemy import select

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    today = date.today()

    with SessionLocal() as s:
        ex_id = s.scalar(select(Exercise.id))
        s.add(Log(user_id=mid, exercise_id=ex_id, date=today, weight=40,
                  reps=5, sets=3, logged_by=Role.member))
        s.commit()

    client.post(f"/members/{mid}/targets", data={
        "exercise_id": str(ex_id), "target_weight": "50", "target_reps": "",
        "target_date": (today + timedelta(days=30)).isoformat()})

    with SessionLocal() as s:
        assert not crud.targets_for(s, mid)[0]["reached"]
        s.add(Log(user_id=mid, exercise_id=ex_id, date=today, weight=52.5,
                  reps=5, sets=3, logged_by=Role.member))
        s.commit()
        assert crud.targets_for(s, mid)[0]["reached"]


def test_member_sees_own_targets_but_cannot_set_them(client):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Exercise

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    with SessionLocal() as s:
        ex_id = s.scalar(select(Exercise.id))

    client.post(f"/members/{mid}/targets", data={
        "exercise_id": str(ex_id), "target_weight": "60", "target_reps": "",
        "target_date": (date.today() + timedelta(days=30)).isoformat()})

    logout(client)
    login(client, m["email"], m["password"])
    page = client.get("/dashboard").text
    assert "Your targets" in page
    assert "Set a target" not in page
    r = client.post(f"/members/{mid}/targets", data={
        "exercise_id": str(ex_id), "target_weight": "999", "target_reps": "",
        "target_date": date.today().isoformat()}, follow_redirects=False)
    assert r.status_code == 403


# --- Phase 6: body weight ----------------------------------------------


def test_body_weight_reports_trend_and_rate(client):
    from app import crud
    from app.database import SessionLocal

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    today = date.today()

    for d, w in ((42, 82.0), (35, 81.6), (28, 81.0), (21, 80.8), (14, 80.3), (0, 79.9)):
        client.post(f"/members/{mid}/weight",
                    data={"weight_kg": str(w), "on": (today - timedelta(days=d)).isoformat()})

    with SessionLocal() as s:
        tr = crud.body_weight_trend(s, mid)
    assert tr["direction"] == "down"
    assert tr["abs_change"] == 2.1
    assert tr["weeks"] == 6.0
    assert tr["per_week"] == 0.35

    page = client.get(f"/dashboard?user_id={mid}").text
    assert "Down 2.1 kg over 6 weeks" in page
    assert "0.35 kg/week" in page


def test_one_weigh_in_claims_no_trend(client):
    from app import crud
    from app.database import SessionLocal

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    client.post(f"/members/{mid}/weight", data={"weight_kg": "80"})

    with SessionLocal() as s:
        assert crud.body_weight_trend(s, mid)["enough"] is False
    assert "A trend needs at least two" in client.get(f"/dashboard?user_id={mid}").text


def test_re_recording_the_same_day_updates_rather_than_duplicates(client):
    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.models import BodyWeight

    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    today = date.today().isoformat()

    client.post(f"/members/{mid}/weight", data={"weight_kg": "80", "on": today})
    client.post(f"/members/{mid}/weight", data={"weight_kg": "79.5", "on": today})

    with SessionLocal() as s:
        rows = s.scalars(select(BodyWeight).where(BodyWeight.user_id == mid)).all()
    assert len(rows) == 1 and rows[0].weight_kg == 79.5


def test_body_weight_never_shows_a_progress_bar_or_goal_percentage(client):
    """Body weight is not monotonic; a bar going backwards punishes nobody's fault."""
    register_trainer(client)
    m = add_member(client)
    mid = user_id_by_email(m["email"])
    today = date.today()
    for d, w in ((28, 82.0), (14, 81.0), (0, 80.0)):
        client.post(f"/members/{mid}/weight",
                    data={"weight_kg": str(w), "on": (today - timedelta(days=d)).isoformat()})

    page = client.get(f"/dashboard?user_id={mid}").text.lower()
    for banned in ("progress bar", "progressbar", "% of goal", "% complete", "of goal complete"):
        assert banned not in page
    for banned in ("diet", "junk", "discipline", "motivat", "lazy"):
        assert banned not in page
