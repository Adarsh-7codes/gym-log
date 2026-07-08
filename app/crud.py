from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BodyPart, Difficulty, Exercise, Log, MemberRoutine, PlanDay, PlanItem, Role, User


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


def list_logs(
    db: Session,
    current_user: User,
    user_id: Optional[int] = None,
    exercise_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
):
    stmt = select(Log)

    if current_user.role == Role.trainer:
        if user_id is not None:
            stmt = stmt.where(Log.user_id == user_id)
    else:
        # Members can never see another member's logs, no matter what
        # user_id is requested -- this is the enforcement point, not the UI.
        stmt = stmt.where(Log.user_id == current_user.id)

    if exercise_id is not None:
        stmt = stmt.where(Log.exercise_id == exercise_id)
    if start_date is not None:
        stmt = stmt.where(Log.date >= start_date)
    if end_date is not None:
        stmt = stmt.where(Log.date <= end_date)

    stmt = stmt.order_by(Log.date.desc(), Log.id.desc())
    return db.scalars(stmt).all()


def create_log(
    db: Session,
    current_user: User,
    *,
    exercise_id: int,
    log_date: date,
    weight: Optional[float] = None,
    reps: Optional[int] = None,
    sets: Optional[int] = None,
    next_action: Optional[str] = None,
    notes: Optional[str] = None,
    feeling: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Log:
    target_user_id = current_user.id
    if user_id is not None and user_id != current_user.id:
        if current_user.role != Role.trainer:
            raise Forbidden("Only a trainer can log workouts for another user")
        target_user_id = user_id

    if not db.get(User, target_user_id):
        raise NotFound("User not found")
    if not db.get(Exercise, exercise_id):
        raise NotFound("Exercise not found")

    log = Log(
        user_id=target_user_id,
        exercise_id=exercise_id,
        date=log_date,
        weight=weight,
        reps=reps,
        sets=sets,
        next_action=next_action,
        notes=notes,
        feeling=feeling,
        # Whoever is creating the entry: a member logs their own; a trainer
        # logging for a member records logged_by=trainer.
        logged_by=current_user.role,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_owned_log(db: Session, log_id: int, current_user: User) -> Log:
    log = db.get(Log, log_id)
    # A member gets the same 404 for "doesn't exist" and "exists but isn't
    # theirs" -- never confirm another member's log id is valid.
    if not log or (current_user.role != Role.trainer and log.user_id != current_user.id):
        raise NotFound("Log not found")
    return log


def update_log(db: Session, log: Log, **fields) -> Log:
    # Callers pass only the fields that should change (API: exclude_unset:
    # web form: everything on the form) so an explicit None means "clear it".
    for field, value in fields.items():
        setattr(log, field, value)
    db.commit()
    db.refresh(log)
    return log


def delete_log(db: Session, log: Log) -> None:
    db.delete(log)
    db.commit()


# --- Weekly planner -----------------------------------------------------


def resolve_planner_target(current_user: User, user_id: Optional[int]) -> int:
    """Whose planner is being edited. Members are always locked to themselves."""
    if user_id is not None and user_id != current_user.id and current_user.role == Role.trainer:
        return user_id
    return current_user.id


def get_week(db: Session, user_id: int) -> dict:
    """weekday (0..6) -> PlanDay for the user (only days that exist)."""
    days = db.scalars(select(PlanDay).where(PlanDay.user_id == user_id)).all()
    return {d.weekday: d for d in days}


def _get_or_create_day(db: Session, user_id: int, weekday: int) -> PlanDay:
    day = db.scalar(select(PlanDay).where(PlanDay.user_id == user_id, PlanDay.weekday == weekday))
    if day is None:
        day = PlanDay(user_id=user_id, weekday=weekday)
        db.add(day)
        db.flush()
    return day


def set_focus(db: Session, user_id: int, weekday: int, focus: Optional[str]) -> None:
    day = _get_or_create_day(db, user_id, weekday)
    day.focus = focus or None
    db.commit()


def add_plan_item(
    db: Session,
    user_id: int,
    weekday: int,
    *,
    exercise_id: int,
    target_sets: Optional[int] = None,
    target_reps: Optional[int] = None,
    target_weight: Optional[float] = None,
) -> PlanItem:
    if not db.get(Exercise, exercise_id):
        raise NotFound("Exercise not found")
    day = _get_or_create_day(db, user_id, weekday)
    item = PlanItem(
        plan_day_id=day.id,
        exercise_id=exercise_id,
        target_sets=target_sets,
        target_reps=target_reps,
        target_weight=target_weight,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def delete_plan_item(db: Session, current_user: User, item_id: int, target_user_id: int) -> None:
    item = db.get(PlanItem, item_id)
    # Only delete if it belongs to the planner the caller is allowed to edit.
    if item and item.day.user_id == target_user_id:
        db.delete(item)
        db.commit()


# --- Progress / stall detection ----------------------------------------

SESSIONS_TO_COMPARE = 3   # look at the last N logged sessions per exercise
INACTIVE_DAYS = 10        # no logged session in this many days -> flag


def _top_set_value(log: Log) -> tuple:
    """A session's 'top set' as a comparable (weight, reps) tuple.

    Compared lexicographically: heavier weight always wins; at equal weight,
    more reps wins. A good proxy for 'did this session beat the last one'.
    """
    return (log.weight or 0.0, log.reps or 0)


def _stalled_exercises_from_logs(logs) -> list:
    """logs: one user's Log rows, newest-first.

    Returns the Exercise objects that are stalled: exercises with at least
    SESSIONS_TO_COMPARE logged sessions where the most recent session did NOT
    beat the best of the preceding ones (no new personal best -> plateau).
    """
    by_exercise: dict = {}
    for log in logs:
        by_exercise.setdefault(log.exercise_id, []).append(log)

    stalled = []
    for ex_logs in by_exercise.values():
        recent = ex_logs[:SESSIONS_TO_COMPARE]  # newest first
        if len(recent) < SESSIONS_TO_COMPARE:
            continue  # not enough history to call it a stall
        newest = _top_set_value(recent[0])
        prev_best = max(_top_set_value(log) for log in recent[1:])
        if newest <= prev_best:
            stalled.append(recent[0].exercise)
    return stalled


def member_status(db: Session, user: User) -> dict:
    """Quick-glance status for one member: last session + stall/inactivity flags."""
    logs = db.scalars(
        select(Log).where(Log.user_id == user.id).order_by(Log.date.desc(), Log.id.desc())
    ).all()
    last_date = logs[0].date if logs else None
    days_since = (date.today() - last_date).days if last_date else None
    inactive = last_date is None or days_since >= INACTIVE_DAYS
    stalled = _stalled_exercises_from_logs(logs)

    reasons = []
    if last_date is None:
        reasons.append("No sessions logged yet")
    elif inactive:
        reasons.append(f"No session in {days_since} days")
    if stalled:
        reasons.append("Stalled: " + ", ".join(e.name for e in stalled))

    return {
        "user": user,
        "last_date": last_date,
        "days_since": days_since,
        "inactive": inactive,
        "stalled": stalled,
        "flagged": bool(reasons),
        "reasons": reasons,
    }


def member_roster(db: Session) -> list:
    """All members with status, flagged (stalled/inactive) surfaced first."""
    members = db.scalars(select(User).where(User.role == Role.member).order_by(User.name)).all()
    statuses = [member_status(db, m) for m in members]
    statuses.sort(
        key=lambda s: (
            not s["flagged"],                                              # flagged first
            -(s["days_since"] if s["days_since"] is not None else 10**6),  # stalest first
            s["user"].name.lower(),
        )
    )
    return statuses


def stalled_exercise_ids(db: Session, user_id: int) -> set:
    """Set of exercise ids currently flagged as stalled for this user."""
    logs = db.scalars(
        select(Log).where(Log.user_id == user_id).order_by(Log.date.desc(), Log.id.desc())
    ).all()
    return {e.id for e in _stalled_exercises_from_logs(logs)}


def last_sets_by_exercise(db: Session, user_id: int) -> dict:
    """exercise_id -> most recent Log for that exercise, for prefill defaults."""
    logs = db.scalars(
        select(Log).where(Log.user_id == user_id).order_by(Log.date.desc(), Log.id.desc())
    ).all()
    out: dict = {}
    for log in logs:
        out.setdefault(log.exercise_id, log)
    return out


# --- Exercise Library + Member Routine ---------------------------------

# Order body parts / difficulties consistently in the UI.
BODY_PARTS = list(BodyPart)
DIFFICULTY_ORDER = {Difficulty.beginner: 0, Difficulty.intermediate: 1, Difficulty.advanced: 2}


def _exercise_sort_key(ex: Exercise):
    # Beginner first so a newcomer starts easy and progresses.
    return (DIFFICULTY_ORDER.get(ex.difficulty, 99), ex.name.lower())


def library_grouped(db: Session) -> dict:
    """body_part -> [Exercise] sorted by difficulty then name (categorized library)."""
    exercises = db.scalars(select(Exercise).where(Exercise.body_part.is_not(None))).all()
    groups: dict = {}
    for ex in exercises:
        groups.setdefault(ex.body_part, []).append(ex)
    for part in groups:
        groups[part].sort(key=_exercise_sort_key)
    return groups


def routine_exercise_ids(db: Session, user_id: int) -> set:
    """Set of exercise ids currently in a member's routine (for checkbox state)."""
    return set(
        db.scalars(select(MemberRoutine.exercise_id).where(MemberRoutine.user_id == user_id)).all()
    )


def routine_grouped(db: Session, user_id: int) -> dict:
    """body_part -> [Exercise] for the member's active routine (drives the log page)."""
    entries = db.scalars(select(MemberRoutine).where(MemberRoutine.user_id == user_id)).all()
    groups: dict = {}
    for entry in entries:
        ex = entry.exercise
        if ex is not None and ex.body_part is not None:
            groups.setdefault(ex.body_part, []).append(ex)
    for part in groups:
        groups[part].sort(key=_exercise_sort_key)
    return groups


def set_routine_for_body_part(db: Session, user_id: int, body_part: BodyPart, exercise_ids) -> None:
    """Make the member's routine for one body_part exactly `exercise_ids`.

    Adds newly selected, removes unchecked. Removal deletes ONLY the
    MemberRoutine rows -- historical Log entries are a separate table and are
    never touched here.
    """
    valid = set(
        db.scalars(select(Exercise.id).where(Exercise.body_part == body_part)).all()
    )
    want = {int(i) for i in exercise_ids if int(i) in valid}

    current = db.scalars(
        select(MemberRoutine).where(
            MemberRoutine.user_id == user_id, MemberRoutine.body_part == body_part
        )
    ).all()
    have = {row.exercise_id: row for row in current}

    for ex_id, row in have.items():
        if ex_id not in want:
            db.delete(row)  # remove from routine only; logs stay intact
    for ex_id in want:
        if ex_id not in have:
            db.add(MemberRoutine(user_id=user_id, exercise_id=ex_id, body_part=body_part, date_added=date.today()))
    db.commit()


def remove_from_routine(db: Session, user_id: int, exercise_id: int) -> None:
    """Remove a single exercise from the member's routine. Logs are kept."""
    row = db.scalar(
        select(MemberRoutine).where(
            MemberRoutine.user_id == user_id, MemberRoutine.exercise_id == exercise_id
        )
    )
    if row:
        db.delete(row)
        db.commit()
